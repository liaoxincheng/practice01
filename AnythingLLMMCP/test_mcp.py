import httpx
import asyncio

async def test():
    client = httpx.AsyncClient(
        base_url="http://localhost:8082",
        timeout=5.0,
        trust_env=False
    )
    try:
        resp = await client.get("/mcp")
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text[:200]}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.aclose()

asyncio.run(test())