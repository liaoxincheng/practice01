"""Agent 核心:整个项目最核心的业务编排逻辑(与 UI 完全无关)。

职责:
1. 维护聊天历史(多轮上下文);
2. 把用户消息交给 LLM 流式请求,并把文本增量事件转发出去;
3. 当 LLM 要求调用工具时,通过 ToolRegistry 执行工具,
   把结果作为 tool 消息送回 LLM,循环往复,直到 LLM 不再调用工具;
4. 全程通过"异步生成器"产出 events.py 中定义的标准事件。

UI 层(现在是 tui/,未来可以是 web/、desktop/)只需:
    async for event in agent.chat(user_input):
        ... 根据事件类型渲染界面 ...
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from . import events
from .llm import LLMClient
from .tools import ToolRegistry


def _safe_parse_arguments(raw: str) -> dict:
    """把模型给的参数 JSON 字符串解析成 dict(仅用于界面展示)。

    解析失败不中断流程(执行时由 ToolRegistry 再解析并给出错误)。
    """
    try:
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {"raw": raw}
    except json.JSONDecodeError:
        return {"raw": raw}


class Agent:
    """与 UI 无关的对话 Agent(可被任何界面复用)。"""

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        max_tool_rounds: int = 5,
    ) -> None:
        """
        :param llm: LLM 客户端(依赖注入,方便测试时替换成假实现)
        :param tools: 工具注册中心(依赖注入)
        :param max_tool_rounds: 一轮对话中工具调用的最大来回数
        """
        self._llm = llm
        self._tools = tools
        self._max_tool_rounds = max_tool_rounds
        self._messages: list[dict] = []  # 聊天历史(OpenAI messages 格式)

    # ---------- 对外只读信息 ----------

    @property
    def model_name(self) -> str:
        """当前使用的模型名(供界面展示)。"""
        return self._llm.model_name

    @property
    def messages(self) -> list[dict]:
        """聊天历史的副本(只读,避免外部误改)。"""
        return list(self._messages)

    def reset(self) -> None:
        """清空聊天历史,开始新一轮对话。"""
        self._messages.clear()

    # ---------- 对话主流程 ----------

    async def chat(self, user_input: str) -> AsyncIterator[events.AgentEvent]:
        """处理一条用户消息,产出事件流。

        典型事件序列:
        1. TextChunk × N        —— 模型流式输出的文本增量
        2. ToolCallStarted/Finished —— 模型要求调用工具并执行(可多轮循环)
        3. ReplyDone            —— 本轮对话结束

        出错时产出 AgentError,并把聊天历史回滚到本轮开始前,
        保证用户重试时不会带上半截的对话。
        """
        user_input = user_input.strip()
        if not user_input:
            yield events.ReplyDone(text="")
            return

        snapshot = len(self._messages)  # 记录本轮开始的位置,出错时回滚用
        self._messages.append({"role": "user", "content": user_input})

        try:
            # 工具调用循环:模型每回答一次,若还想调用工具就执行工具并送回结果,
            # 直到它不再调用工具(或达到最大轮数)。
            for _round in range(self._max_tool_rounds):
                reply_parts: list[str] = []
                tool_calls: list[events.ToolCallProposal] = []

                # 1) 请求 LLM,收集本轮回复
                async for ev in self._llm.stream_chat(
                    self._messages,
                    tools=self._tools.openai_schemas(),
                ):
                    if isinstance(ev, events.TextChunk):
                        yield ev  # 文本增量直接转发给 UI
                        reply_parts.append(ev.text)
                    elif isinstance(ev, events.ToolCallProposal):
                        tool_calls.append(ev)

                reply_text = "".join(reply_parts)

                # 2) 模型没有要求调用工具 → 正常回答结束
                if not tool_calls:
                    if reply_text:
                        self._messages.append(
                            {"role": "assistant", "content": reply_text}
                        )
                    yield events.ReplyDone(text=reply_text)
                    return

                # 3) 模型要求调用工具:先把"带工具调用的回复"记入历史
                self._messages.append(
                    {
                        "role": "assistant",
                        "content": reply_text or None,
                        "tool_calls": [
                            {
                                "id": tc.call_id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": tc.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )

                # 4) 逐个执行工具,把结果作为 tool 消息送回 LLM(下一轮循环)
                for tc in tool_calls:
                    yield events.ToolCallStarted(
                        name=tc.name,
                        arguments=_safe_parse_arguments(tc.arguments),
                    )
                    result = await self._tools.execute(tc.name, tc.arguments)
                    yield events.ToolCallFinished(
                        name=tc.name,
                        ok=not result.startswith("错误"),
                        result=result,
                    )
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.call_id,
                            "content": result,
                        }
                    )

            # 5) 超过最大工具轮数还没结束:中断并提示
            yield events.AgentError(
                message=(
                    f"工具调用超过 {self._max_tool_rounds} 轮仍未结束,已强制停止。"
                    "请尝试把需求描述得更具体一些。"
                )
            )
        except Exception as exc:
            # 出错回滚:丢弃本轮产生的所有消息,对话可以继续
            del self._messages[snapshot:]
            yield events.AgentError(message=f"调用大模型失败:{exc}")
