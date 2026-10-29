"""工具注册中心:统一管理模型可调用的工具。

设计要点:
- Tool 把"给模型看的说明"(name/description/parameters)和"真正的执行函数"
  (handler)绑定在一起;
- Agent 只依赖 ToolRegistry,不关心具体有哪些工具;
- 新增能力(联网搜索、计算器等)只需 register 一个新 Tool,Agent 无需改动。

错误处理约定:execute() 永远不抛异常,出错时返回以"错误"开头的字符串,
让模型能看到错误原因并自己修正(这是 Agent 工具调用的常见做法)。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from inspect import isawaitable, iscoroutinefunction
from typing import Any, Callable

# 工具执行函数:接收关键字参数,返回结果字符串(同步/异步均可)
ToolHandler = Callable[..., Any]


@dataclass
class Tool:
    """一个可供模型调用的工具。"""

    name: str          # 工具名,如 "read_file"
    description: str   # 功能描述(给模型看,写清楚模型才知道什么时候用它)
    parameters: dict   # 参数 JSON Schema(给模型看,定义参数怎么传)
    handler: ToolHandler  # 真正的执行函数


class ToolRegistry:
    """工具注册中心。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ---------- 注册管理 ----------

    def register(self, tool: Tool) -> None:
        """注册一个工具(同名工具会被覆盖)。"""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """移除一个工具。"""
        self._tools.pop(name, None)

    def names(self) -> list[str]:
        """所有已注册工具的名字。"""
        return list(self._tools)

    # ---------- 给 LLM 的工具说明 ----------

    def openai_schemas(self) -> list[dict]:
        """生成 OpenAI function calling 格式的工具说明列表。

        说明里不包含 handler(执行函数),模型只拿到"说明书"。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    # ---------- 执行 ----------

    async def execute(self, name: str, arguments: str) -> str:
        """执行一次工具调用,返回结果字符串(永不抛异常)。

        :param name: 工具名
        :param arguments: 模型给出的参数 JSON 字符串(如 '{"path": "a.txt"}')
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"错误:没有名为 {name} 的工具,可用工具:{self.names()}"

        # 解析参数:模型给的 JSON 可能不合法,这里给出友好错误而不是崩溃
        try:
            kwargs = json.loads(arguments) if arguments else {}
            if not isinstance(kwargs, dict):
                return f"错误:参数必须是 JSON 对象,收到:{arguments!r}"
        except json.JSONDecodeError as exc:
            return f"错误:参数不是合法 JSON:{exc}"

        # 执行工具:支持同步和异步两种 handler。
        # 同步 handler 放到线程池执行 —— 像 RunCli 这类最长 30 秒的命令,
        # 如果直接调用会阻塞事件循环、卡死整个 TUI 界面。
        try:
            if iscoroutinefunction(tool.handler):
                result = await tool.handler(**kwargs)
            else:
                result = await asyncio.to_thread(tool.handler, **kwargs)
            if isawaitable(result):
                result = await result
            return str(result)
        except TypeError as exc:
            # 参数对不上(缺参数/多参数)
            return f"错误:工具参数不正确:{exc}"
        except Exception as exc:  # noqa: BLE001 —— 工具内部异常统一转成文本
            return f"错误:工具执行失败:{exc}"
