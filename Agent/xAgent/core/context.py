"""上下文估算、会话归档与归档检索。

这里放的都是与 UI 无关的纯业务逻辑:

- ``estimate_tokens`` / ``estimate_messages_tokens``:按字符粗估 Token 占用
  (中文约 1 字 1 token,ASCII 约 4 字符 1 token),只用于界面提示;
- ``ArchiveStore``:把对话历史保存成 JSON 会话文件,支持列出、读取和按关键字检索;
- ``ContextStats`` / ``SessionInfo`` / ``RecallHit``:给 Agent 和界面使用的数据对象。

归档文件保存在配置的 ``archive_dir`` 下(默认 ``.xagent/sessions``),
每次压缩前都会自动归档一份完整历史,所以压缩不会丢信息;
模型需要旧细节时可以通过 ``recallContext`` 工具检索这些文件。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# 单个归档文件最多读取 5 MB,避免误读超大文件
MAX_ARCHIVE_BYTES = 5_000_000
# 最多扫描 200 个会话文件
MAX_ARCHIVE_FILES = 200
# 检索结果里每条记录的摘要片段长度
MAX_SNIPPET_CHARS = 260
# 摘要预览长度
MAX_PREVIEW_CHARS = 80

_ARCHIVE_VERSION = 1
_VALID_ROLES = {"system", "user", "assistant", "tool"}


# ---------- Token 估算 ----------

def estimate_tokens(text: str) -> int:
    """粗估一段文本的 Token 数。

    这是一个不依赖分词器的近似值:ASCII 字符按 4 个 1 token,
    其他字符(中文等)按 1 个 1 token。用于界面提示与压缩前后对比,
    不用于计费。
    """
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    other_chars = len(text) - ascii_chars
    return max(1, ascii_chars // 4 + other_chars)


def message_text(message: Any) -> str:
    """把一条 OpenAI messages 记录转成可检索/可阅读的文本。"""
    if not isinstance(message, dict):
        return ""
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        parts.append(content)
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        name = function.get("name", "")
        arguments = function.get("arguments", "")
        parts.append(f"[工具调用 {name}] {arguments}")
    return "\n".join(parts)


def estimate_messages_tokens(messages: Iterable[Any]) -> int:
    """粗估整段聊天历史的 Token 数(含少量消息格式开销)。"""
    total = 0
    for message in messages:
        total += 4
        total += estimate_tokens(message_text(message))
    return total


# ---------- 数据对象 ----------

@dataclass(frozen=True)
class ContextStats:
    """当前上下文的使用情况(供状态栏和 /status 展示)。"""

    messages: int
    conversation_messages: int
    estimated_tokens: int
    max_tokens: int
    summary_messages: int = 0


@dataclass(frozen=True)
class SessionInfo:
    """一个归档会话的摘要信息。"""

    path: Path
    name: str
    created_at: str
    message_count: int
    summary_preview: str = ""


@dataclass(frozen=True)
class RecallHit:
    """一条归档检索结果。"""

    session: str
    created_at: str
    role: str
    snippet: str


# ---------- 归档存储 ----------

def _sanitize_name(name: str | None) -> str:
    """把用户给的名字变成安全的文件名片段。"""
    text = (name or "").strip()
    if not text:
        return "session"
    text = re.sub(r"[^\w.-]+", "-", text, flags=re.UNICODE)
    text = text.strip("-._")
    if not text or text in {".", ".."}:
        text = "session"
    return text[:40]


def _preview(text: Any) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= MAX_PREVIEW_CHARS:
        return compact
    return compact[:MAX_PREVIEW_CHARS] + "…"


def _snippet(text: str, needle: str) -> str:
    compact = " ".join(text.split())
    index = compact.casefold().find(needle.casefold())
    if index < 0:
        return compact[:MAX_SNIPPET_CHARS]
    start = max(0, index - MAX_SNIPPET_CHARS // 3)
    end = min(len(compact), start + MAX_SNIPPET_CHARS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(compact) else ""
    return f"{prefix}{compact[start:end]}{suffix}"


class ArchiveStore:
    """会话归档目录的读写与检索。"""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory).expanduser().resolve()

    @property
    def directory(self) -> Path:
        return self._directory

    # ---- 写入 ----

    def save(
        self,
        messages: list[dict],
        *,
        name: str | None = None,
        summary: str = "",
    ) -> Path:
        """保存一份会话,返回归档文件路径。写入采用临时文件 + 替换。"""
        if not messages:
            raise ValueError("没有可保存的对话内容")

        self._directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone()
        slug = _sanitize_name(name)
        filename = f"{now.strftime('%Y%m%d-%H%M%S-%f')}-{slug}.json"
        target = self._directory / filename
        record = {
            "version": _ARCHIVE_VERSION,
            "name": filename,
            "created_at": now.isoformat(timespec="seconds"),
            "summary": str(summary or ""),
            "messages": list(messages),
        }
        data = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        _atomic_write(target, data)
        return target

    # ---- 读取 ----

    def list_sessions(self) -> list[SessionInfo]:
        """列出全部有效归档,按文件名(时间)从新到旧。"""
        if not self._directory.is_dir():
            return []
        try:
            paths = sorted(
                self._directory.glob("*.json"),
                key=lambda path: path.name,
                reverse=True,
            )
        except OSError:
            return []

        sessions: list[SessionInfo] = []
        for path in paths[:MAX_ARCHIVE_FILES]:
            record = self._read(path)
            if record is None:
                continue
            messages = record.get("messages")
            count = len(messages) if isinstance(messages, list) else 0
            sessions.append(
                SessionInfo(
                    path=path,
                    name=path.name,
                    created_at=str(record.get("created_at") or ""),
                    message_count=count,
                    summary_preview=_preview(record.get("summary")),
                )
            )
        return sessions

    def load(self, ref: str) -> tuple[dict[str, Any], list[dict]]:
        """按文件名 / 名字片段读取一个会话,返回 (记录, 消息列表)。"""
        text = (ref or "").strip()
        if not text:
            raise ValueError("请提供会话文件名或名字片段")

        candidates = self._candidate_paths(text)
        if not candidates:
            raise ValueError(
                f"找不到会话归档:{ref}。可用 /sessions 查看已有会话。"
            )
        if len(candidates) > 1:
            names = "、".join(path.name for path in candidates[:5])
            raise ValueError(
                f"名字 {ref!r} 匹配到多个会话:{names}。请写得更具体一些。"
            )

        path = candidates[0]
        record = self._read(path)
        if record is None:
            raise ValueError(f"会话文件无法读取或不是合法 JSON:{path.name}")
        raw_messages = record.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ValueError(f"会话文件里没有消息:{path.name}")

        messages = [
            message
            for message in raw_messages
            if isinstance(message, dict)
            and message.get("role") in _VALID_ROLES
        ]
        if not messages:
            raise ValueError(f"会话文件里没有可恢复的消息:{path.name}")
        return record, messages

    # ---- 检索 ----

    def search(self, query: str, limit: int = 5) -> list[RecallHit]:
        """在全部归档中按关键字检索,返回最近的若干条命中。"""
        needle = (query or "").strip()
        if not needle:
            raise ValueError("query 不能为空")
        max_hits = max(1, min(int(limit), 20))

        hits: list[RecallHit] = []
        for info in self.list_sessions():
            record = self._read(info.path)
            if record is None:
                continue
            created_at = str(record.get("created_at") or info.created_at)

            summary = record.get("summary")
            if isinstance(summary, str) and needle.casefold() in summary.casefold():
                hits.append(
                    RecallHit(
                        session=info.name,
                        created_at=created_at,
                        role="摘要",
                        snippet=_snippet(summary, needle),
                    )
                )
                if len(hits) >= max_hits:
                    return hits

            messages = record.get("messages")
            if not isinstance(messages, list):
                continue
            session_hits = 0
            for message in messages:
                if not isinstance(message, dict):
                    continue
                text = message_text(message)
                if needle.casefold() not in text.casefold():
                    continue
                hits.append(
                    RecallHit(
                        session=info.name,
                        created_at=created_at,
                        role=str(message.get("role") or ""),
                        snippet=_snippet(text, needle),
                    )
                )
                session_hits += 1
                if len(hits) >= max_hits:
                    return hits
                if session_hits >= 2:
                    break
        return hits

    # ---- 内部 ----

    def _read(self, path: Path) -> dict | None:
        try:
            if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
                return None
            text = path.read_text(encoding="utf-8-sig")
            record = json.loads(text)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(record, dict):
            return None
        return record

    def _candidate_paths(self, ref: str) -> list[Path]:
        if not self._directory.is_dir():
            return []
        try:
            paths = sorted(
                self._directory.glob("*.json"),
                key=lambda path: path.name,
                reverse=True,
            )
        except OSError:
            return []
        paths = paths[:MAX_ARCHIVE_FILES]

        if ref in {"last", "latest", "最新"}:
            return paths[:1]

        folded = ref.casefold()
        exact: list[Path] = []
        suffix: list[Path] = []
        stem: list[Path] = []
        contains: list[Path] = []
        for path in paths:
            name = path.name
            if name == ref:
                exact.append(path)
            elif name.casefold() == folded:
                exact.append(path)
            elif name.casefold().endswith(f"-{folded}.json"):
                suffix.append(path)
            elif path.stem.casefold() == folded:
                stem.append(path)
            elif folded in name.casefold():
                contains.append(path)
        for group in (exact, suffix, stem, contains):
            if group:
                return group
        return []


def _atomic_write(target: Path, data: bytes) -> None:
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "ArchiveStore",
    "ContextStats",
    "RecallHit",
    "SessionInfo",
    "estimate_messages_tokens",
    "estimate_tokens",
    "message_text",
]
