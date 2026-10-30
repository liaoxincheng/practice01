"""宠物医院 MCP 服务器入口。

环境变量:
    MCP_TRANSPORT  streamable-http(默认)或 stdio
    MCP_PORT       HTTP 模式端口,默认 8081,端点 /mcp
    PET_API_URL    宠物医院 REST API 地址,默认 http://127.0.0.1:8080
    OLLAMA_URL / OLLAMA_MODEL  仅在 followup_summary 工具中使用

启动:
    python server.py                    # HTTP 模式
    MCP_TRANSPORT=stdio python server.py  # stdio 模式(供 MCP 客户端拉起)
"""
from __future__ import annotations

import os

from tools import mcp

DEFAULT_PORT = 8081


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    port = int(os.environ.get("MCP_PORT", str(DEFAULT_PORT)))
    # 无状态模式:每个请求独立处理,符合 MCP 2026-07-28 的无状态核心
    mcp.run(transport="streamable-http", port=port, stateless_http=True)


if __name__ == "__main__":
    main()
