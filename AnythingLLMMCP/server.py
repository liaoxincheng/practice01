"""AnythingLLM MCP 服务器:把工作区检索问答暴露成 AI 可调用的工具。

工具:
- list_workspaces  列出 AnythingLLM 上的工作区(name / slug)
- query_workspace  在指定工作区里提问,返回回答与来源

传输方式由环境变量决定:
- MCP_TRANSPORT=streamable-http(默认),监听 MCP_PORT(默认 8082),端点 /mcp
- MCP_TRANSPORT=stdio,供 opencode / Claude Desktop 等本地客户端启动
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from anythingllm_api import AnythingLLMError, WorkspaceClient

DEFAULT_PORT = 8082

mcp = MCPServer("anythingllm-workspaces", version="1.0.0")


@mcp.tool()
async def list_workspaces() -> dict:
    """列出 AnythingLLM 上所有可用的工作区。

    返回每个工作区的 name(名称)和 slug(唯一标识,查询时要用 slug)。
    """
    client = WorkspaceClient()
    try:
        workspaces = await client.list_workspaces()
    except AnythingLLMError as exc:
        raise ToolError(f"获取工作区列表失败:{exc}")
    finally:
        await client.aclose()

    summary = [{"name": ws.get("name", ""), "slug": ws.get("slug", "")} for ws in workspaces]
    return {"count": len(summary), "workspaces": summary}


@mcp.tool()
async def query_workspace(
    workspace_slug: str,
    message: str,
    mode: str = "query",
) -> dict:
    """在 AnythingLLM 的某个工作区里提问,基于该工作区的文档回答问题。

    参数:
        workspace_slug: 工作区标识 slug(先用 list_workspaces 查询)
        message: 要提问的内容
        mode: query(默认,单次检索问答)或 chat(带工作区聊天上下文)

    返回:
        reply: AI 回答文本
        sources: 引用的文档来源
        mode: 实际使用的模式
    """
    client = WorkspaceClient()
    try:
        return await client.ask(workspace_slug, message, mode)
    except AnythingLLMError as exc:
        raise ToolError(f"查询工作区失败:{exc}")
    finally:
        await client.aclose()


def main() -> None:
    """按环境变量选择传输方式并启动服务器。"""
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    port = int(os.environ.get("MCP_PORT", str(DEFAULT_PORT)))
    # stateless_http:每个请求独立处理,便于用 curl / 简单 HTTP 客户端测试
    mcp.run(transport="streamable-http", port=port, stateless_http=True)


if __name__ == "__main__":
    main()
