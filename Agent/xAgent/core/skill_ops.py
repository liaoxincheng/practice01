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

import re
from pathlib import Path

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


def register_skill_ops(registry: ToolRegistry, skills_dir: Path) -> Path:
    """把 createSkill 工具注册进工具注册中心,并确保技能目录存在。

    :param registry: 目标工具注册中心
    :param skills_dir: 技能目录(建议为项目根的 .agents/skills,绝对路径)
    :return: 解析后的技能目录绝对路径
    """
    skills_dir = skills_dir.resolve()
    skills_dir.mkdir(parents=True, exist_ok=True)

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

    return skills_dir
