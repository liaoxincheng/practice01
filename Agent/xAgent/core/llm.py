"""LLM 调用模块:封装大模型的流式请求。

设计要点(依赖倒置原则):
- Agent 只依赖抽象接口 LLMClient,不依赖具体实现;
- 默认实现 OpenAILLMClient 走 OpenAI 兼容接口(LM Studio、Ollama 等都兼容);
- 以后想换别的模型服务(如 Ollama 原生 HTTP、云端 API),只需新增一个
  实现了 LLMClient 的类,Agent 和其他代码完全不用改。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from . import events
from .config import LLMConfig


class LLMClient(ABC):
    """LLM 抽象接口:定义"流式请求一次对话回合"的能力。"""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """当前使用的模型名(供界面展示)。"""

    @abstractmethod
    def stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> AsyncIterator[events.TextChunk | events.ToolCallProposal | events.StreamEnd]:
        """流式请求一次对话回合。

        :param messages: 聊天历史(OpenAI messages 格式)
        :param tools: 工具说明列表(OpenAI function calling 格式),None 表示不提供工具
        :yield: 文本增量 / 完整工具调用 / 回合结束 事件
        """


class OpenAILLMClient(LLMClient):
    """OpenAI 兼容接口的流式客户端实现(本地 LM Studio / Ollama 均适用)。"""

    def __init__(self, config: LLMConfig):
        self._config = config
        # AsyncOpenAI:异步客户端,配合 Textual 的异步任务模型使用
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )

    @property
    def model_name(self) -> str:
        return self._config.model

    async def stream_chat(self, messages, tools=None):
        stream = await self._client.chat.completions.create(
            model=self._config.model,
            messages=messages,
            tools=tools,          # None 时表示这一轮不允许模型调用工具
            stream=True,
        )

        # 流式协议细节:一次工具调用会拆成多个增量(chunk)到达,
        # 这里按 index 把它们合并成"完整调用",再作为事件产出,
        # 让上层(Agent)不用关心 OpenAI 的流式细节。
        pending: dict[int, dict] = {}
        ended = False

        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta is None:
                continue

            # 1) 普通文本增量
            if delta.content:
                yield events.TextChunk(text=delta.content)

            # 2) 工具调用增量(合并)
            if delta.tool_calls:
                for part in delta.tool_calls:
                    entry = pending.setdefault(
                        part.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if part.id:
                        entry["id"] = part.id
                    if part.function:
                        if part.function.name:
                            entry["name"] += part.function.name
                        if part.function.arguments:
                            entry["arguments"] += part.function.arguments

            # 3) 回合结束:先把合并好的工具调用全部产出
            if choice.finish_reason:
                for entry in pending.values():
                    yield events.ToolCallProposal(
                        call_id=entry["id"],
                        name=entry["name"],
                        arguments=entry["arguments"],
                    )
                pending.clear()
                yield events.StreamEnd(finish_reason=choice.finish_reason)
                ended = True

        # 兜底:某些服务端不发 finish_reason 就断开,也要把残余事件产出
        if not ended:
            for entry in pending.values():
                yield events.ToolCallProposal(
                    call_id=entry["id"],
                    name=entry["name"],
                    arguments=entry["arguments"],
                )
            yield events.StreamEnd(finish_reason=None)
