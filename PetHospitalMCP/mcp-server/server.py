import os
from tools import mcp

if __name__ == "__main__":
    port = int(os.environ.get("MCP_PORT", "8081"))
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", port=port, stateless_http=True)