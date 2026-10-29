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
        print(f"Response: {resp.json()}")
        
        # 然后调用tools/list
        payload2 = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        resp2 = await client.post("/mcp", json=payload2)
        print(f"\nTools list: {resp2.status_code}")
        print(f"Response: {resp2.json()}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.aclose()

asyncio.run(test())