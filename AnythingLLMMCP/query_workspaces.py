import httpx
import asyncio

async def list_workspaces():
    client = httpx.AsyncClient(
        base_url="http://localhost:8082",
        timeout=5.0,
        trust_env=False
    )
    try:
        # MCP协议调用
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "list_workspaces",
                "arguments": {}
            }
        }
        resp = await client.post("/mcp", json=payload)
        result = resp.json()
        print("工作区列表:")
        if "result" in result and "content" in result["result"]:
            for content in result["result"]["content"]:
                if content.get("type") == "text":
                    print(content["text"])
        else:
            print(result)
    except Exception as e:
        print(f"错误: {e}")
    finally:
        await client.aclose()

asyncio.run(list_workspaces())