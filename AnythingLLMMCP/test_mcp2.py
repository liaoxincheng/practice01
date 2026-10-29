import httpx
import asyncio

async def test():
    client = httpx.AsyncClient(
        base_url="http://localhost:8082",
        timeout=10.0,
        trust_env=False
    )
    try:
        # 尝试不同的端点
        endpoints = ["/mcp", "/mcp/", "/", "/health"]
        for ep in endpoints:
            try:
                resp = await client.get(ep)
                print(f"GET {ep}: {resp.status_code} {resp.text[:100]}")
            except Exception as e:
                print(f"GET {ep}: {e}")
        
        # 尝试POST到根路径
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {}
        }
        resp = await client.post("/", json=payload)
        print(f"POST /: {resp.status_code} {resp.text[:200]}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.aclose()

asyncio.run(test())