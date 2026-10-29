from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from anythingllm_client import AnythingLLMClient

mcp = MCPServer("anythingllm", version="0.1.0")

_original_run = mcp.run

def _run(transport="stdio", **kwargs):
    if transport == "streamable-http":
        kwargs.setdefault("stateless_http", True)
    return _original_run(transport, **kwargs)

mcp.run = _run


@mcp.tool()
async def query_workspace(
    workspace_slug: str,
    message: str,
    mode: str = "query",
) -> dict:
    """
    查询AnythingLLM工作区并获取AI回答。
    
    参数:
        workspace_slug: 工作区标识符（slug）
        message: 要查询的消息内容
        mode: 查询模式，可选值：query（单次查询）或 chat（带上下文的对话）
    
    返回:
        包含AI回答、来源和类型的字典
    """
    try:
        client = AnythingLLMClient()
        result = await client.chat_with_workspace(workspace_slug, message, mode)
        await client.close()
        return result
    except Exception as e:
        raise ToolError(f"查询工作区失败: {e}")


@mcp.tool()
async def list_workspaces() -> dict:
    """
    列出所有可用的AnythingLLM工作区。
    
    返回:
        包含工作区列表的字典
    """
    try:
        client = AnythingLLMClient()
        workspaces = await client.list_workspaces()
        await client.close()
        return {"workspaces": workspaces}
    except Exception as e:
        raise ToolError(f"获取工作区列表失败: {e}")