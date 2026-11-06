"""Agent Skills 的自动发现与元数据解析。

Skill 约定:``<项目根>/.agents/skills/<技能名>/SKILL.md``,文件开头是
YAML Frontmatter,必须声明 ``name`` 和 ``description``。

启动时只读取 Frontmatter(名称 + 简介),不读正文;正文由 ``loadSkill``
工具在模型真正需要时再加载。这样系统提示词很短,不会一次塞满上下文。

扫描顺序:各项目根下的 .agents/skills → 显式传入的技能目录。
同名技能以先扫描到的为准,后面的只记一条提示,不覆盖已有技能。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

SKILL_FILENAME = "SKILL.md"
SKILLS_RELATIVE_PATH = Path(".agents") / "skills"

_MAX_FRONTMATTER_LINES = 512
_MAX_FRONTMATTER_CHARS = 128 * 1024

_KEY_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")
_BLOCK_PATTERN = re.compile(r"^[|>][+-]?\d*$")
_YAML_INDICATORS = tuple("-?:,[]{}#&*!|>'\"%@`")


@dataclass(frozen=True)
class SkillInfo:
    """一个已发现的技能(只含初始化阶段需要的元数据)。"""

    name: str
    description: str
    directory: Path      # 技能目录(所有附件都必须在这个目录内)
    skill_file: Path     # SKILL.md 的绝对路径
    root: Path           # 包含 .agents 的项目根目录


@dataclass(frozen=True)
class SkillsCatalog:
    """一次启动发现的全部技能。"""

    skills: tuple[SkillInfo, ...] = ()
    issues: tuple[str, ...] = ()
    roots: tuple[Path, ...] = ()

    @classmethod
    def discover(
        cls,
        roots: Iterable[str | Path] = (),
        *,
        skill_dirs: Iterable[str | Path] = (),
    ) -> "SkillsCatalog":
        """扫描技能。

        :param roots: 项目根目录,会扫描每个根下的 ``.agents/skills``
        :param skill_dirs: 直接当作技能目录扫描(用于配置里自定义的 skills_dir,
            不要求它位于 ``<项目根>/.agents/skills``)
        """
        resolved_roots = _resolve_dirs(roots)
        resolved_skill_dirs = _resolve_dirs(skill_dirs)

        skills: list[SkillInfo] = []
        issues: list[str] = []
        seen: set[str] = set()
        scanned: set[Path] = set()

        for root in resolved_roots:
            skills_root = root / SKILLS_RELATIVE_PATH
            if skills_root in scanned:
                continue
            scanned.add(skills_root)
            _scan_skills_dir(skills_root, skills, seen, issues)

        for skills_root in resolved_skill_dirs:
            if skills_root in scanned:
                continue
            scanned.add(skills_root)
            _scan_skills_dir(skills_root, skills, seen, issues)

        return cls(
            skills=tuple(skills),
            issues=tuple(issues),
            roots=tuple(resolved_roots),
        )

    def get(self, name: str) -> SkillInfo | None:
        """按名称查找技能。

        优先精确匹配 Frontmatter 的 name;其次允许用目录名作为别名;
        最后做一次唯一的大小写不敏感匹配。
        """
        text = (name or "").strip()
        if not text:
            return None

        for skill in self.skills:
            if skill.name == text:
                return skill

        aliases = [skill for skill in self.skills if skill.directory.name == text]
        if len(aliases) == 1:
            return aliases[0]

        folded = [
            skill
            for skill in self.skills
            if skill.name.casefold() == text.casefold()
            or skill.directory.name.casefold() == text.casefold()
        ]
        return folded[0] if len(folded) == 1 else None

    def __iter__(self) -> Iterator[SkillInfo]:
        return iter(self.skills)

    def __len__(self) -> int:
        return len(self.skills)


# ---------- 扫描 ----------

def _resolve_dirs(values: Iterable[str | Path]) -> list[Path]:
    """展开并去重目录列表(顺序保持不变)。"""
    result: list[Path] = []
    for value in values:
        try:
            path = Path(value).expanduser().resolve()
        except (OSError, RuntimeError):
            path = Path(value).expanduser()
        if path not in result:
            result.append(path)
    return result


def _scan_skills_dir(
    skills_root: Path,
    skills: list[SkillInfo],
    seen: set[str],
    issues: list[str],
) -> None:
    if not skills_root.is_dir():
        return

    # 默认布局 <项目根>/.agents/skills 时,root 指项目根;
    # 自定义技能目录则用其上级目录作为「项目根」,供附件路径解析使用
    if skills_root.name == "skills" and skills_root.parent.name == ".agents":
        root = skills_root.parent.parent
    else:
        root = skills_root.parent

    try:
        directories = sorted(
            (entry for entry in skills_root.iterdir() if entry.is_dir()),
            key=lambda entry: entry.name.casefold(),
        )
    except OSError as exc:
        issues.append(f"无法读取技能目录 {skills_root}:{exc}")
        return

    for directory in directories:
        skill_file = _find_skill_file(directory)
        if skill_file is None:
            issues.append(f"跳过技能 {directory.name}:目录里没有 SKILL.md")
            continue

        try:
            metadata = read_skill_metadata(skill_file)
        except OSError as exc:
            issues.append(f"跳过技能 {directory.name}:读取 SKILL.md 失败:{exc}")
            continue

        if metadata is None:
            issues.append(
                f"跳过技能 {directory.name}:SKILL.md 缺少合法的 YAML Frontmatter"
            )
            continue

        name, description = metadata
        if not name:
            issues.append(f"跳过技能 {directory.name}:Frontmatter 缺少 name")
            continue
        if not description:
            issues.append(f"跳过技能 {directory.name}:Frontmatter 缺少 description")
            continue
        if name in seen:
            issues.append(f"跳过技能 {directory.name}:技能名称重复:{name}")
            continue

        try:
            resolved_dir = directory.resolve()
            resolved_file = skill_file.resolve()
        except (OSError, RuntimeError) as exc:
            issues.append(f"跳过技能 {directory.name}:无法解析路径:{exc}")
            continue

        seen.add(name)
        skills.append(
            SkillInfo(
                name=name,
                description=description,
                directory=resolved_dir,
                skill_file=resolved_file,
                root=root,
            )
        )


def _find_skill_file(directory: Path) -> Path | None:
    exact = directory / SKILL_FILENAME
    if exact.is_file():
        return exact
    try:
        candidates = sorted(directory.iterdir(), key=lambda entry: entry.name.casefold())
    except OSError:
        return None
    for entry in candidates:
        if entry.is_file() and entry.name.casefold() == SKILL_FILENAME.casefold():
            return entry
    return None


# ---------- Frontmatter 解析 ----------

def read_skill_metadata(path: str | Path) -> tuple[str, str] | None:
    """读取 SKILL.md 的 name / description;格式不合法时返回 None。"""
    lines = _read_frontmatter_lines(Path(path))
    if not lines:
        return None
    fields = _parse_mapping(lines)
    return (
        str(fields.get("name", "")).strip(),
        str(fields.get("description", "")).strip(),
    )


def _read_frontmatter_lines(path: Path) -> list[str] | None:
    """只读到结束分隔符 ``---`` 为止,避免把正文读进内存。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return _read_lines_until_fence(path, encoding)
        except UnicodeDecodeError:
            continue
        except (OSError, RuntimeError):
            return None
    return None


def _read_lines_until_fence(path: Path, encoding: str) -> list[str] | None:
    lines: list[str] = []
    total = 0
    with path.open("r", encoding=encoding, newline=None) as handle:
        for index, line in enumerate(handle):
            if index > _MAX_FRONTMATTER_LINES:
                return None
            total += len(line)
            if total > _MAX_FRONTMATTER_CHARS:
                return None

            if index == 0:
                if line.lstrip("﻿").strip() != "---":
                    return None
                continue

            raw = line.rstrip("\r\n")
            if raw.rstrip() == "---":
                return lines
            lines.append(raw)
    return None


def _parse_mapping(lines: list[str]) -> dict[str, str]:
    """解析扁平的 key/value Frontmatter,支持常用 YAML 标量写法。"""
    result: dict[str, str] = {}
    index = 0
    while index < len(lines):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue

        match = _KEY_PATTERN.match(raw)
        if match is None:
            index += 1
            continue

        key = match.group(1)
        value = match.group(2).strip()
        base_indent = len(raw) - len(raw.lstrip())

        block: list[str] = []
        next_index = index + 1
        while next_index < len(lines):
            following = lines[next_index]
            if not following.strip():
                block.append("")
                next_index += 1
                continue
            indent = len(following) - len(following.lstrip())
            if indent <= base_indent:
                break
            block.append(following.strip())
            next_index += 1

        while block and not block[-1]:
            block.pop()

        result[key] = _combine_scalar(value, block)
        index = next_index

    return result


def _combine_scalar(value: str, block: list[str]) -> str:
    if _BLOCK_PATTERN.match(value):
        if value.startswith(">"):
            text = " ".join(part for part in block if part)
        else:
            text = "\n".join(block)
        return text.strip()

    inline = _unquote(value)
    if block:
        continuation = " ".join(part for part in block if part)
        inline = f"{inline} {continuation}".strip()
    return inline.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return value[1:-1].replace('\\"', '"').replace("\\n", "\n")
        return parsed if isinstance(parsed, str) else value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value
