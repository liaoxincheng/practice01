import httpx
import asyncio

async def test():
    client = httpx.AsyncClient(
        base_url="http://localhost:8082",
        timeout=10.0,
        trust_env=False,
        follow_redirects=True
    )
    try:
        # MCP协议需要POST到/mcp
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        }
        resp = await client.post("/mcp", json=payload)
        print(f"Initialize: {resp.status_code}")
        print(f"Response headers: {dict(resp.headers)}")
        print(f"Response text: {resp.text[:500]}")
        
        # 然后调用tools/list
        payload2 = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        resp2 = await client.post("/mcp", json=payload2)
        print(f"\nTools list: {resp2.status_code}")
        print(f"Response text: {resp2.text[:500]}")
        
        # 调用list_workspaces
        payload3 = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_workspaces",
                "arguments": {}
            }
        }
        resp3 = await client.post("/mcp", json=payload3)
        print(f"\nList workspaces: {resp3.status_code}")
        print(f"Response text: {resp3.text[:1000]}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.aclose()

asyncio.run(test())