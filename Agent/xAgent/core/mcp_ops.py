"""MCP 客户端接入模块:把外部 MCP 服务器变成 xAgent 的工具。

MCP(Model Context Protocol)是一种"工具服务器"协议:
一个 MCP 服务器对外提供若干工具(如查询宠物医院、查询知识库),
客户端连上后把工具"带回来",注册成 xAgent 自己的 Function Calling 工具。
这样内置工具之外,还可以随时接入新能力,而且 xAgent 一行代码都不用改。

设计要点:
- 启动时逐个连接配置里的 MCP 服务器,发现对方提供的工具并注册;
- 每调用一个工具就新开一次连接,用完即关(无状态,简单可靠);
- 某个服务器没启动 / 连不上时自动跳过,不影响内置工具;
- 未安装 mcp 包时跳过并提示,不会崩溃。

工具命名规则:mcp_<服务器名>_<工具名>,全部小写、非法字符换成下划线、
超长截断、重名加序号 —— 保证不与内置工具冲突,模型一眼能看出来源。
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Callable

from .tools import Tool, ToolRegistry

# 单个工具名最长 64 字符(OpenAI function calling 的名称限制)
MAX_NAME_LENGTH = 64

# 启动时"发现工具"的限时(秒):服务器卡死时最多等这么久,
# 不让启动界面一直黑屏;工具调用本身仍使用配置里的 timeout_seconds
DISCOVERY_TIMEOUT_SECONDS = 10.0


def _make_default_factory():
    """创建默认的 HTTP MCP 客户端工厂;未安装 mcp 包时返回 None。

    工厂签名是 ``(server, timeout) -> 客户端``。服务器配置了 headers 时,
    用自定义 httpx 客户端把请求头带进去;没有 headers 时直接连 URL。
    """
    try:
        from mcp import Client
        from mcp.client.streamable_http import (
            create_mcp_http_client,
            streamable_http_client,
        )
    except ImportError:
        return None

    def factory(server: Any, timeout_seconds: float):
        if not server.headers:
            return Client(server.url, read_timeout_seconds=timeout_seconds)

        @asynccontextmanager
        async def transport():
            async with create_mcp_http_client(
                headers=dict(server.headers)
            ) as http_client:
                async with streamable_http_client(
                    server.url, http_client=http_client
                ) as streams:
                    yield streams

        return Client(transport(), read_timeout_seconds=timeout_seconds)

    return factory


def _make_default_stdio_factory():
    """创建默认的 stdio MCP 客户端工厂;未安装 mcp 包时返回 None。"""
    try:
        from mcp import Client, StdioServerParameters
    except ImportError:
        return None

    def factory(server: Any, timeout_seconds: float):
        # 自定义 env 时与当前环境合并,保证 PATH 等基础变量仍然可用
        env = {**os.environ, **dict(server.env)} if server.env else None
        parameters = StdioServerParameters(
            command=server.command,
            args=list(server.args),
            env=env,
            cwd=server.cwd or None,
        )
        return Client(parameters, read_timeout_seconds=timeout_seconds)

    return factory


def _sanitize(text: str) -> str:
    """把一段文字变成合法的工具名片段:小写,非 ASCII 字母数字换成下划线。

    用 ASCII 字母数字而不是 isalnum:工具名要符合 OpenAI 的命名规范
    (^[a-zA-Z0-9_-]+$),中文等非 ASCII 字符一律换成下划线。
    """
    cleaned = "".join(
        ch if ch.isascii() and ch.isalnum() else "_" for ch in text.lower()
    )
    return cleaned.strip("_") or "tool"


def _make_tool_name(server_name: str, tool_name: str, used: set[str]) -> str:
    """生成不冲突的工具名:mcp_<服务器>_<工具>,重名加序号,超长截断。"""
    base = f"mcp_{_sanitize(server_name)}_{_sanitize(tool_name)}"[:MAX_NAME_LENGTH]
    candidate = base
    index = 2
    while candidate in used:
        suffix = f"_{index}"
        candidate = base[: MAX_NAME_LENGTH - len(suffix)] + suffix
        index += 1
    used.add(candidate)
    return candidate


def _root_cause(exc: BaseException) -> BaseException:
    """一路剥到最里层的原因。

    anyio 会把底层连接错误包成 ExceptionGroup("unhandled errors in a
    TaskGroup"),直接展示对用户不友好,这里取出真正的原因。
    """
    current = exc
    while True:
        inner = getattr(current, "exceptions", None)
        if not isinstance(inner, (list, tuple)) or not inner:
            return current
        current = inner[0]


def _render_result(result: Any) -> tuple[str, bool]:
    """把 MCP 调用结果渲染成文本,返回 (文本, 是否出错)。

    MCP 的返回内容是一串"内容块"(文字 / 图片 / 结构化数据),
    这里主要取文字块;没有文字块时把结构化数据转成 JSON 文本。
    """
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        else:
            parts.append(str(block))
    structured = getattr(result, "structured_content", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    return ("\n".join(parts), bool(getattr(result, "is_error", False)))


async def _discover(connector, timeout_seconds: float) -> list[Any]:
    """连接 MCP 服务器并列出它的全部工具(整体限时,防止启动卡死)。"""

    async def _go() -> list[Any]:
        async with connector(timeout_seconds) as client:
            result = await client.list_tools()
            return list(getattr(result, "tools", None) or [])

    return await asyncio.wait_for(
        _go(), timeout=min(timeout_seconds, DISCOVERY_TIMEOUT_SECONDS)
    )


def _make_handler(
    connector: Callable[[float], Any],
    tool_name: str,
    timeout_seconds: float,
):
    """生成一个 MCP 工具的 handler:每次调用新开连接,用完即关。

    handler 是异步函数:执行时由 ToolRegistry 直接 await,
    不会阻塞 TUI 界面的事件循环。
    """

    async def _call(**kwargs: Any) -> str:
        async def _go() -> Any:
            async with connector(timeout_seconds) as client:
                return await client.call_tool(tool_name, kwargs)

        result = await asyncio.wait_for(_go(), timeout=timeout_seconds)
        text, is_error = _render_result(result)
        if is_error:
            return f"错误:MCP 工具 {tool_name} 返回错误:{text}"
        return text or "(MCP 工具没有返回内容)"

    return _call


def register_mcp_ops(
    registry: ToolRegistry,
    servers: list[Any] | tuple[Any, ...],
    *,
    timeout_seconds: float = 30.0,
    client_factory: Callable[[str, float], Any] | None = None,
    stdio_client_factory: Callable[[Any, float], Any] | None = None,
) -> list[str]:
    """把配置里的 MCP 服务器逐个接入工具注册中心,返回状态说明列表。

    :param registry: 工具注册中心(接入的工具注册到这里)
    :param servers: McpServerConfig 列表(来自 config.ini 的 [mcp] 段和 .mcp.json)
    :param timeout_seconds: 连接 / 调用的超时秒数
    :param client_factory: 测试时注入的 HTTP 假客户端工厂
        ``(url, timeout) -> 客户端对象``
    :param stdio_client_factory: 测试时注入的 stdio 假客户端工厂
        ``(server, timeout) -> 客户端对象``
    :return: 每个服务器的接入状态说明(如 "服务器 pet: 已接入 5 个工具"),
             供界面启动时展示给用户
    """
    if not servers:
        return []

    injected_http_factory = client_factory is not None
    if client_factory is None:
        client_factory = _make_default_factory()
    if stdio_client_factory is None:
        stdio_client_factory = _make_default_stdio_factory()

    statuses: list[str] = []
    used_names = set(registry.names())
    for server in servers:
        transport = getattr(server, "transport", "http") or "http"
        if transport == "stdio":
            if stdio_client_factory is None:
                statuses.append(
                    f"服务器 {server.name}: 未安装 mcp 包,已跳过"
                    "(可执行 pip install mcp 安装)"
                )
                continue

            def connector(
                timeout: float,
                _server=server,
                _factory=stdio_client_factory,
            ):
                return _factory(_server, timeout)
        else:
            if client_factory is None:
                statuses.append(
                    f"服务器 {server.name}: 未安装 mcp 包,已跳过"
                    "(可执行 pip install mcp 安装)"
                )
                continue

            def connector(
                timeout: float,
                _server=server,
                _factory=client_factory,
                _injected=injected_http_factory,
            ):
                # 注入的测试工厂沿用历史签名 (url, timeout);默认工厂用 (server, timeout)
                if _injected:
                    return _factory(_server.url, timeout)
                return _factory(_server, timeout)

        # 发现工具(连不上就跳过这一台,不影响其他服务器和内置工具)
        try:
            remote_tools = asyncio.run(_discover(connector, timeout_seconds))
        except Exception as exc:  # noqa: BLE001 —— 单台服务器失败不影响整体
            reason = str(_root_cause(exc)) or exc.__class__.__name__
            if len(reason) > 80:
                reason = reason[:77] + "..."
            statuses.append(f"服务器 {server.name}: 连接失败,已跳过({reason})")
            continue

        registered: list[str] = []
        for remote in remote_tools:
            remote_name = getattr(remote, "name", None)
            if not remote_name:
                continue
            local_name = _make_tool_name(server.name, remote_name, used_names)
            schema = getattr(remote, "input_schema", None)
            if not isinstance(schema, dict):
                # 没有参数说明的工具:给一个空对象 schema
                schema = {"type": "object", "properties": {}}
            registry.register(
                Tool(
                    name=local_name,
                    description=(
                        f"[MCP 服务器 {server.name}] "
                        f"{getattr(remote, 'description', '') or remote_name}"
                    ),
                    parameters=schema,
                    handler=_make_handler(
                        connector, remote_name, timeout_seconds
                    ),
                )
            )
            registered.append(local_name)

        if registered:
            statuses.append(
                f"服务器 {server.name}: 已接入 {len(registered)} 个工具"
                f"({', '.join(registered)})"
            )
        else:
            statuses.append(f"服务器 {server.name}: 已连接,但没有提供任何工具")

    return statuses
