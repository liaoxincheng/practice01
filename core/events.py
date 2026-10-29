"""事件协议:core 与 UI 之间唯一的"对话语言"。

分层解耦的关键设计:
- core 层通过"异步生成器"产出这些标准事件,完全不知道 UI 是谁;
- UI 层(Textual / Web / 桌面)只负责遍历事件流、把事件渲染出来。

事件分两组:

1. LLM 层事件(LLMClient 产出,Agent 内部消费,UI 一般不直接看到):
   - TextChunk        流式文本增量(一个字/一个词)
   - ToolCallProposal LLM 要求调用一次工具
   - StreamEnd        一次流式请求结束

2. Agent 层事件(Agent.chat() 产出,UI 直接消费):
   - TextChunk        流式文本增量(与 LLM 层同名,由 Agent 转发)
   - ToolCallStarted  工具开始执行
   - ToolCallFinished 工具执行完毕(带结果)
   - ReplyDone        一条用户消息的完整回复结束
   - AgentError       出错(LLM 连不上、工具执行异常等)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union


# ---------- LLM 层事件 ----------

@dataclass
class TextChunk:
    """一段流式文本增量。"""

    text: str


@dataclass
class ToolCallProposal:
    """LLM 要求调用一次工具。

    arguments 是模型给出的 JSON 字符串(未解析),由工具注册中心负责解析。
    """

    call_id: str      # 本次调用的唯一编号(回传结果时使用)
    name: str         # 工具名,如 read_file
    arguments: str    # 参数 JSON 字符串,如 '{"path": "notes.txt"}'


@dataclass
class StreamEnd:
    """一次流式请求结束。"""

    finish_reason: str | None = None  # 结束原因,如 "stop" / "tool_calls"


# ---------- Agent 层事件(UI 消费) ----------

@dataclass
class ToolCallStarted:
    """工具开始执行(参数已解析成 dict,便于界面展示)。"""

    name: str
    arguments: dict[str, Any]


@dataclass
class ToolCallFinished:
    """工具执行完毕。ok=False 表示执行出错(错误信息在 result 里)。"""

    name: str
    ok: bool
    result: str


@dataclass
class ReplyDone:
    """一条用户消息的完整回复结束,text 是完整回复文本。"""

    text: str


@dataclass
class AgentError:
    """对话过程中出错,message 是可展示的错误信息。"""

    message: str


# Agent.chat() 产出的事件联合类型(方便 UI 层做类型标注)
AgentEvent = Union[
    TextChunk, ToolCallStarted, ToolCallFinished, ReplyDone, AgentError
]
