"""技能创建工具(createSkill):让模型能按自然语言要求创建新的 Agent Skill。

一个 Skill 就是项目 `.agents/skills/<名称>/SKILL.md` 下的一个目录 + 说明文件:
- SKILL.md 必须符合 Agent Skills 规范:YAML Frontmatter(name、description)+ 正文;
- 名称统一小写字母、数字、连字符(如 web-search),对应目录名;
- 若技能还需要 references/、scripts/ 等目录或辅助文件,模型应继续调用
  文件工具(write_file 等)自己创建 —— createSkill 只负责 SKILL.md 这一份。

安全设计:与 file_ops 的 work_dir 沙箱不同,createSkill 会把文件写到
项目根的 .agents/skills/(这是 Agent 的"能力扩展区",属于项目的一部分)。
但写入位置是固定的,且 name 经过严格正则校验(不允许任何路径字符),
不存在路径注入面。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .skills_catalog import (
    SKILL_FILENAME,
    SkillInfo,
    SkillsCatalog,
)
from .tools import Tool, ToolRegistry

# Skill 名称规范:小写字母、数字、连字符,不能以连字符开头或结尾
NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

NAME_RULES = (
    "名称只能由小写字母、数字和连字符组成,不能以连字符开头或结尾,"
    "例如 web-search、pdf-reader"
)

SKILL_FORMAT_EXAMPLE = (
    "---\n"
    "name: <与 name 参数一致的技能名>\n"
    "description: <一句话说明这个技能做什么、什么时候用>\n"
    "---\n"
    "\n"
    "<正文:给 Agent 看的操作步骤、注意事项等>\n"
)

# 单个技能文件最多读取 200 KB,返回给模型的内容最多 5 万字符
MAX_SKILL_FILE_BYTES = 200_000
MAX_SKILL_OUTPUT_CHARS = 50_000


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_plain_name(filename: str) -> bool:
    return (
        Path(filename).name == filename
        and "/" not in filename
        and "\\" not in filename
    )


def _search_attachment_by_name(skill: SkillInfo, filename: str) -> list[Path]:
    """在技能目录内递归查找同名文件(精确匹配优先于大小写不敏感匹配)。"""
    target = filename.casefold()
    exact: list[Path] = []
    folded: list[Path] = []
    try:
        for entry in skill.directory.rglob("*"):
            if not entry.is_file():
                continue
            if entry.name == filename:
                exact.append(entry)
            elif entry.name.casefold() == target:
                folded.append(entry)
    except OSError:
        return []

    matches: list[Path] = []
    for entry in exact or folded:
        try:
            resolved = entry.resolve()
        except (OSError, RuntimeError):
            continue
        if _is_within(resolved, skill.directory):
            matches.append(resolved)
    return sorted(
        set(matches),
        key=lambda path: path.relative_to(skill.directory).as_posix().casefold(),
    )


def _resolve_attachment(skill: SkillInfo, filename: str) -> Path | list[Path]:
    """把 filename 解析成技能目录内的文件。

    返回 Path 表示唯一命中;返回 list 表示找到多个同名文件,需要模型
    指定更具体的路径;找不到或越界时抛 ValueError。
    """
    text = (filename or "").strip()
    if not text:
        raise ValueError("filename 不能为空")
    if "\x00" in text:
        raise ValueError("filename 不能包含空字符")

    try:
        raw = Path(os.path.expanduser(text))
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"无法解析 filename:{filename}:{exc}") from exc

    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(skill.directory / raw)
        candidates.append(skill.root / raw)

    outside_attempt = False
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if not _is_within(resolved, skill.directory):
            # 文件确实存在,或者路径里带了 .. 想往外跳,都算越界尝试
            if resolved.exists() or ".." in raw.parts:
                outside_attempt = True
            continue
        if resolved.is_file():
            return resolved

    if _is_plain_name(text):
        matches = _search_attachment_by_name(skill, text)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return matches

    if outside_attempt:
        raise ValueError(
            f"filename 解析到了技能目录之外:{filename};"
            f"只能读取 {skill.directory} 内部的文件"
        )
    raise ValueError(
        f"在技能 {skill.name!r} 的目录中找不到附件:{filename}"
    )


def _read_skill_text(target: Path) -> tuple[str, str]:
    """读取技能文件并识别编码;二进制、超大文件抛 ValueError。"""
    if not target.is_file():
        raise ValueError(f"文件不存在:{target}")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ValueError(f"无法读取文件信息:{target}:{exc}") from exc
    if size > MAX_SKILL_FILE_BYTES:
        raise ValueError(
            f"文件过大({size} 字节),超过 {MAX_SKILL_FILE_BYTES} 字节的读取上限"
        )

    try:
        data = target.read_bytes()
    except OSError as exc:
        raise ValueError(f"读取文件失败:{target}:{exc}") from exc

    sample = data[:65_536]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    elif sample.startswith(b"\xef\xbb\xbf"):
        encodings = ("utf-8-sig",)
    elif b"\x00" in sample:
        raise ValueError(f"不是纯文本文件:{target}")
    else:
        encodings = ("utf-8", "gb18030")

    for encoding in encodings:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别文件编码(仅支持 UTF-8 / GBK 等纯文本):{target}")


def register_skill_ops(
    registry: ToolRegistry,
    skills_dir: Path,
    catalog: SkillsCatalog | None = None,
) -> Path:
    """注册 createSkill 和 loadSkill,并确保技能目录存在。

    :param registry: 目标工具注册中心
    :param skills_dir: 技能目录(建议为项目根的 .agents/skills,绝对路径)
    :param catalog: 启动时发现的技能目录;不传时按 skills_dir 所在项目现场扫描
    :return: 解析后的技能目录绝对路径
    """
    skills_dir = skills_dir.resolve()
    skills_dir.mkdir(parents=True, exist_ok=True)

    if catalog is None:
        # skills_dir 可能是默认的 <项目根>/.agents/skills,也可能是配置的自定义目录,
        # 因此除了项目根,还要把 skills_dir 本身作为技能目录扫描
        project_root = skills_dir.parent.parent
        catalog = SkillsCatalog.discover(
            [project_root, Path.cwd()], skill_dirs=[skills_dir]
        )

    def _check_name(name: str) -> None:
        """校验 Skill 名称,不合法时抛 ValueError(错误信息给模型看)。"""
        if not name or not NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Skill 名称不合法:{name!r}。{NAME_RULES}")

    def _parse_skill(text: str) -> tuple[dict[str, str] | None, str]:
        """拆出 Frontmatter 字段和正文。

        Frontmatter 不合法(首行不是 ---,或没有结束的 ---)时返回 (None, "")。
        """
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return None, ""
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                fields: dict[str, str] = {}
                for line in lines[1:index]:
                    if ":" in line:
                        key, _, value = line.partition(":")
                        fields[key.strip().lower()] = value.strip()
                return fields, "\n".join(lines[index + 1:])
        return None, ""

    def create_skill(name: str, skillContext: str = "") -> str:
        """按规范创建新 Skill,返回创建结果(错误也以文本返回,不抛异常)。

        已存在的 Skill 一律拒绝,不会覆盖原有内容。
        """
        try:
            # ---- 1. 校验参数 ----
            _check_name((name or "").strip())
            name = (name or "").strip()
            # 校验时忽略首尾空白,但写入文件的是 skillContext 原文(不篡改内容)
            content = (skillContext or "").strip()
            if not content:
                return "错误:skillContext 不能为空,需要 SKILL.md 的完整内容"

            # ---- 2. 校验 SKILL.md 是否符合 Agent Skills 规范 ----
            fields, body = _parse_skill(content)
            if fields is None:
                return (
                    "错误:skillContext 缺少 YAML Frontmatter。"
                    f"请按下面的格式提供完整内容:\n{SKILL_FORMAT_EXAMPLE}"
                )
            if not fields.get("name"):
                return "错误:Frontmatter 里缺少 name 字段(技能名称)"
            if not fields.get("description"):
                return "错误:Frontmatter 里缺少 description 字段(一句话说明用途)"
            if fields["name"] != name:
                return (
                    f"错误:Frontmatter 里的 name({fields['name']!r})与参数 "
                    f"name({name!r})不一致,请保持一致"
                )
            if not body.strip():
                return "错误:Frontmatter 之后缺少正文内容,请写明操作步骤"

            # ---- 3. 创建技能目录与 SKILL.md(已存在则拒绝,不覆盖) ----
            target_dir = skills_dir / name
            target = target_dir / "SKILL.md"
            if target.exists():
                display = _display_path(target)
                return (
                    f"Skill {name!r} 已存在:{display}。"
                    "为避免覆盖原有内容,没有做任何修改。"
                    "如需更换内容,请先删除或重命名它(可用 delete_file / rename_path),"
                    "或者换一个技能名称。"
                )

            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(skillContext, encoding="utf-8")
            return (
                f"成功:已创建 Skill {name!r}\n"
                f"SKILL.md:{_display_path(target)}({len(content)} 字符)\n"
                "新技能会在下次启动 xAgent 后出现在可用技能列表中。\n"
                "如该 Skill 还需要 references/、scripts/ 等目录或辅助文件,"
                "请继续调用文件工具创建。"
            )
        except ValueError as exc:
            return f"错误:{exc}"
        except Exception as exc:  # noqa: BLE001 —— 其余异常同样转文本
            return f"错误:创建 Skill 失败:{exc}"

    def _display_path(target: Path) -> str:
        """尽量显示相对技能目录上级(项目根)的路径,便于阅读。"""
        try:
            return str(target.relative_to(skills_dir.parent))
        except ValueError:
            return str(target)

    registry.register(
        Tool(
            name="createSkill",
            description=(
                "根据用户要求创建一个新的 Agent Skill:在项目 .agents/skills/"
                "目录下创建以技能名称命名的目录,并把 skillContext 写入其中的 "
                "SKILL.md。\n"
                "使用时机:用户说“创建一个…技能 / skill”时调用。\n"
                "参数约定:\n"
                f"- name:技能名称,{NAME_RULES};\n"
                "- skillContext:SKILL.md 的完整内容,必须是 YAML Frontmatter"
                "(name、description 两字段都要,且 name 与参数 name 一致)"
                "加正文的格式。\n"
                f"格式示例:\n{SKILL_FORMAT_EXAMPLE}"
                "注意:同名 Skill 已存在时本工具会拒绝创建;"
                "若技能还需要 scripts/、references/ 等辅助文件,"
                "请继续调用文件工具(write_file 等)完成,本工具只创建 SKILL.md。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": f"技能名称({NAME_RULES})",
                    },
                    "skillContext": {
                        "type": "string",
                        "description": (
                            "要写入 SKILL.md 的完整内容:YAML Frontmatter"
                            "(name/description)+ 正文"
                        ),
                    },
                },
                "required": ["name", "skillContext"],
            },
            handler=create_skill,
        )
    )

    available = "、".join(skill.name for skill in catalog.skills) or "(暂无)"

    def load_skill(name: str, filename: str | None = None) -> str:
        """加载技能说明或附件;错误也以文本返回,不抛异常。"""
        try:
            skill = catalog.get(name or "")
            if skill is None:
                return (
                    f"错误:未找到 Skill:{name!r}。当前可用 Skill:{available}"
                )

            if not (filename or "").strip():
                target = skill.skill_file
                kind = "SKILL.md"
                relative = SKILL_FILENAME
            else:
                located = _resolve_attachment(skill, filename or "")
                if isinstance(located, list):
                    lines = [
                        f"技能 {skill.name!r} 中有多个名为 {filename!r} 的文件,"
                        "无法确定要读取哪一个。",
                        "请用 filename 参数指定以下候选路径之一:",
                    ]
                    lines.extend(
                        f"- {path.relative_to(skill.directory).as_posix()}"
                        for path in located
                    )
                    return "\n".join(lines)
                target = located
                kind = "附件"
                relative = target.relative_to(skill.directory).as_posix()

            text, encoding = _read_skill_text(target)
            truncated = len(text) > MAX_SKILL_OUTPUT_CHARS
            shown = text[:MAX_SKILL_OUTPUT_CHARS]
            lines = [
                f"Skill:{skill.name}",
                f"类型:{kind}",
                f"文件:{target}",
                f"相对路径:{relative}",
                f"编码:{encoding}",
                "--- 内容开始 ---",
                shown,
            ]
            if truncated:
                lines.append(
                    f"--- 内容已截断,仅显示前 {MAX_SKILL_OUTPUT_CHARS} 个字符"
                    f"(共 {len(text)} 个)---"
                )
            else:
                lines.append("--- 内容结束 ---")
            return "\n".join(lines)
        except ValueError as exc:
            return f"错误:{exc}"
        except Exception as exc:  # noqa: BLE001 —— 工具错误统一转成文本
            return f"错误:加载 Skill 失败:{exc}"

    registry.register(
        Tool(
            name="loadSkill",
            description=(
                "按需加载一个 Agent Skill 的完整说明或附件。"
                "只提供 name 时返回该 Skill 的 SKILL.md 全文;"
                "同时提供 filename 时只读取 Skill 目录内的对应附件。"
                "filename 支持相对于该 Skill 目录的路径、相对于项目根目录的路径"
                "和绝对路径;只提供文件名时会递归搜索该 Skill 目录,"
                "匹配到多个同名文件时返回候选路径。"
                f"当前可用 Skill:{available}。"
                "当用户的任务与某个 Skill 的简介匹配时,先调用本工具读取完整说明,"
                "不要只根据简介猜测操作步骤。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill 名称,必须是系统提示中列出的技能之一",
                    },
                    "filename": {
                        "type": "string",
                        "description": (
                            "可选。要加载的附件文件路径或文件名;"
                            "省略时加载 SKILL.md 全文"
                        ),
                    },
                },
                "required": ["name"],
            },
            handler=load_skill,
        )
    )

    return skills_dir
