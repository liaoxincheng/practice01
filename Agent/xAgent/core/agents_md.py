"""AGENTS.md 项目说明的读取与注入。

AGENTS.md 是放在项目根目录的"给 Agent 看的说明书":里面可以写项目约定、
代码风格、目录说明、常用命令等。xAgent 在会话开始时读取它一次,
作为一条 system 消息放进聊天历史的最前面,之后每一轮对话都自动复用:

    ┌──────────────────────────┐
    │ system: AGENTS.md 内容    │ ← 只注入一次,位置永远不变
    │ user: ...                 │
    │ assistant: ...            │
    │ ...                       │
    └──────────────────────────┘

这样既满足"一次注入、全程复用",又让每次请求的公共前缀完全相同,
有利于模型服务端的提示词缓存。

注意:本模块只负责"读文件 + 生成消息",何时注入由 Agent 决定。
"""
from __future__ import annotations

from pathlib import Path

from .text_utils import decode_bytes

# 约定的文件名(项目根目录下)
AGENTS_MD_FILENAME = "AGENTS.md"

# 注入消息的开头说明:让模型知道这段内容的来源和作用
AGENTS_MD_HEADER = (
    "以下是项目根目录 AGENTS.md 的内容,"
    "描述了本项目的约定与要求,请在整个会话中遵守:"
)


def load_agents_md(project_dir: str | Path) -> str | None:
    """读取项目根目录下的 AGENTS.md,返回正文;没有或为空时返回 None。

    - 文件不存在、是目录、没有读取权限:返回 None(不打断启动);
    - 编码先按 UTF-8,失败时退回系统默认编码(兼容 GBK 保存的文件);
    - 统一换行符为 \\n(Windows 保存的文件通常是 CRLF),
      保证不同平台下注入的内容完全一致;
    - 内容去掉首尾空白,空文件视为没有。
    """
    path = Path(project_dir) / AGENTS_MD_FILENAME
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    content = decode_bytes(raw).replace("\r\n", "\n").replace("\r", "\n").strip()
    return content or None


def build_agents_md_message(content: str) -> dict:
    """把 AGENTS.md 正文包装成一条 OpenAI messages 格式的 system 消息。"""
    return {
        "role": "system",
        "content": f"{AGENTS_MD_HEADER}\n\n{content.strip()}",
    }
