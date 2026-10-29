import asyncio
import json
import os
import urllib.request

from mcp import Client, StdioServerParameters

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
REST = os.environ.get("PET_API_URL", "http://127.0.0.1:8080")
out = []


def log(name, value):
    out.append({"step": name, "result": value})


def text_of(res):
    for c in res.content:
        if getattr(c, "type", None) == "text":
            return c.text
    return str(res.content)


async def main():
    params = StdioServerParameters(
        command="python",
        args=[SERVER],
        env={**os.environ, "MCP_TRANSPORT": "stdio"},
    )
    async with Client(params) as c:
        tools = await c.list_tools()
        log("tools", sorted(t.name for t in tools.tools))

        pet = json.loads(text_of(await c.call_tool("get_pet", {"pet_id": "PET-000001"})))
        log("get_pet", {
            "id": pet.get("id"),
            "name": pet.get("name"),
            "species": pet.get("species"),
            "records": len(pet.get("records") or []),
            "charges": len(pet.get("charges") or []),
            "visitCount": pet.get("visitCount"),
            "totalCost": pet.get("totalCost"),
        })

        listed = json.loads(text_of(await c.call_tool("list_pets", {"species": "犬", "page_size": 3})))
        log("list_pets", {"total": listed.get("total"), "got": len(listed.get("items") or [])})

        summary = json.loads(text_of(await c.call_tool("followup_summary", {"pet_id": "PET-000001"})))
        out.append({"step": "followup_summary", "result": summary})

        created = json.loads(text_of(await c.call_tool("create_pet", {
            "name": "自检犬", "species": "犬", "owner_name": "测试",
            "owner_phone": "00000000000", "disease": "自检", "doctor": "自检医生",
        })))
        new_id = created.get("id")
        log("create_pet", {"id": new_id, "visitCount": created.get("visitCount")})

        after = json.loads(text_of(await c.call_tool("add_record", {
            "pet_id": new_id, "diagnosis": "急性肠胃炎", "doctor": "自检医生",
            "symptoms": "呕吐腹泻", "treatment": "补液消炎",
            "prescription": ["阿莫西林"], "charge": 380, "visit_date": "2026-09-24",
        })))
        log("add_record", {
            "visitCount": after.get("visitCount"),
            "totalCost": after.get("totalCost"),
            "lastRecord": (after.get("records") or [{}])[-1],
        })

        req = urllib.request.Request(f"{REST}/api/v1/pets/{new_id}", method="DELETE")
        with urllib.request.urlopen(req) as r:
            log("cleanup", json.loads(r.read().decode("utf-8")).get("message"))

        guard = await c.call_tool("get_pet", {"pet_id": "../admin/compact"})
        log("id_guard", {"is_error": guard.is_error, "msg": text_of(guard)[:140]})


asyncio.run(main())
with open("test_mcp_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("DONE")
