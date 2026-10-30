"""宠物医院 MCP 服务器测试(不需要真实后端 / Ollama / 网络)。

用一个本地 ThreadingHTTPServer 同时模拟:
- 宠物医院 REST API(/api/v1/pets ...)
- Ollama(/api/chat)

覆盖:查询参数映射、档案增查、病历追加、随访小结、ID 越界防护,
以及通过 MCP 层实际调用 5 个工具。

运行:
    python -m unittest test_server -v
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from ollama_client import OllamaError, OllamaSummarizer, render_case
from pet_api_client import PetApiError, PetHospitalClient
from tools import mcp


# ---------------------------------------------------------------------------
# 模拟服务
# ---------------------------------------------------------------------------
class FakeServer:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.pets: dict[str, dict] = {
            "PET-000001": {
                "id": "PET-000001",
                "name": "旺财",
                "species": "犬",
                "breed": "金毛",
                "gender": "公",
                "ageMonths": 36,
                "ownerName": "张三",
                "ownerPhone": "13800001111",
                "doctor": "李医生",
                "disease": "急性肠胃炎",
                "status": "就诊中",
                "records": [
                    {
                        "id": "REC-1",
                        "visitDate": "2026-09-01",
                        "doctor": "李医生",
                        "diagnosis": "急性肠胃炎",
                        "symptoms": "呕吐腹泻",
                        "treatment": "补液消炎",
                        "prescription": ["阿莫西林"],
                        "followUp": "一个月后复查血常规",
                        "charge": 380,
                    }
                ],
                "charges": [],
                "visitCount": 1,
                "totalCost": 380.0,
            }
        }
        self._next_id = 2

    def handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, payload: dict) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _envelope(self, code: int, data=None, message="ok") -> dict:
                return {"code": code, "message": message, "data": data, "time": "2026-10-29T00:00:00+08:00"}

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(length) or b"{}")

            def do_GET(self):
                parsed = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                outer.requests.append(("GET", parsed.path, query))
                parts = parsed.path.strip("/").split("/")

                if parsed.path == "/api/v1/pets":
                    items = list(outer.pets.values())
                    species = query.get("species")
                    if species:
                        items = [p for p in items if p["species"] == species]
                    return self._send(
                        200,
                        self._envelope(
                            200,
                            {
                                "items": items,
                                "total": len(items),
                                "page": int(query.get("page", 1)),
                                "pageSize": int(query.get("pageSize", 20)),
                                "totalPages": 1,
                                "totalCost": sum(p["totalCost"] for p in items),
                            },
                        ),
                    )

                if len(parts) == 4 and parts[:3] == ["api", "v1", "pets"]:
                    pet = outer.pets.get(parts[3])
                    if not pet:
                        return self._send(404, self._envelope(404, None, "档案不存在"))
                    return self._send(200, self._envelope(200, pet))

                self._send(404, self._envelope(404, None, "not found"))

            def do_POST(self):
                parsed = urlparse(self.path)
                body = self._body()
                outer.requests.append(("POST", parsed.path, body))

                if parsed.path == "/api/v1/pets":
                    pet = {
                        "id": f"PET-{outer._next_id:06d}",
                        "records": [],
                        "charges": [],
                        "visitCount": 0,
                        "totalCost": 0.0,
                        **body,
                    }
                    outer._next_id += 1
                    outer.pets[pet["id"]] = pet
                    return self._send(201, self._envelope(201, pet, "档案已创建"))

                if parsed.path.endswith("/records"):
                    pet_id = parsed.path.split("/")[4]
                    pet = outer.pets.get(pet_id)
                    if not pet:
                        return self._send(404, self._envelope(404, None, "档案不存在"))
                    record = {
                        "id": f"REC-{len(pet['records']) + 1}",
                        "visitDate": "2026-10-29",
                        "doctor": "",
                        "charge": 0,
                        **body,
                    }
                    pet["records"].append(record)
                    pet["visitCount"] = len(pet["records"])
                    return self._send(201, self._envelope(201, pet, "病历已追加"))

                if parsed.path == "/api/chat":
                    last = (body.get("messages") or [{}])[-1].get("content", "")
                    return self._send(
                        200,
                        {
                            "message": {
                                "role": "assistant",
                                "content": "一、病情小结:急性肠胃炎恢复期。\n"
                                "二、复查:一个月后复查血常规。\n"
                                "三、护理:清淡饮食,观察呕吐情况。",
                            },
                            "prompt_echo": last,
                        },
                    )

                self._send(404, self._envelope(404, None, "not found"))

        return Handler


def start_fake_server() -> tuple[FakeServer, ThreadingHTTPServer]:
    fake = FakeServer()
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return fake, server


def content_text(result) -> str:
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return item.text
    raise AssertionError(f"结果里没有文本内容:{result!r}")


async def call_tool(name: str, arguments: dict):
    result = await mcp.call_tool(name, arguments)
    return result, json.loads(content_text(result))


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
class TestClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake, self.server = start_fake_server()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    async def test_list_pets_maps_all_query_parameters(self):
        """snake_case 参数正确映射为 REST 的查询参数。"""
        client = PetHospitalClient(base_url=self.base)
        try:
            data = await client.list_pets(
                {
                    "species": "犬",
                    "doctor": "李医生",
                    "status": "就诊中",
                    "disease": "肠胃炎",
                    "search": "呕吐",
                    "owner_name": "张三",
                    "owner_phone": "13800001111",
                    "min_cost": 100,
                    "max_cost": 500,
                    "sort_by": "totalCost",
                    "order": "asc",
                    "page": 2,
                    "page_size": 5,
                }
            )
        finally:
            await client.aclose()

        query = self.fake.requests[-1][2]
        self.assertEqual(
            query,
            {
                "species": "犬",
                "doctor": "李医生",
                "status": "就诊中",
                "disease": "肠胃炎",
                "q": "呕吐",
                "ownerName": "张三",
                "ownerPhone": "13800001111",
                "min": "100",
                "max": "500",
                "sortBy": "totalCost",
                "order": "asc",
                "page": "2",
                "pageSize": "5",
            },
        )
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["name"], "旺财")

    async def test_get_pet_unwraps_envelope(self):
        client = PetHospitalClient(base_url=self.base)
        try:
            pet = await client.get_pet("PET-000001")
        finally:
            await client.aclose()
        self.assertEqual(pet["name"], "旺财")
        self.assertEqual(len(pet["records"]), 1)

    async def test_get_pet_rejects_bad_id_and_reports_not_found(self):
        client = PetHospitalClient(base_url=self.base)
        try:
            with self.assertRaisesRegex(PetApiError, "格式不对"):
                await client.get_pet("../admin/compact")
            with self.assertRaisesRegex(PetApiError, "档案不存在"):
                await client.get_pet("PET-999999")
        finally:
            await client.aclose()

    async def test_create_and_append_record(self):
        client = PetHospitalClient(base_url=self.base)
        try:
            created = await client.create_pet(
                {
                    "name": "自检犬",
                    "species": "犬",
                    "ownerName": "测试",
                    "ownerPhone": "13900000000",
                    "disease": "自检",
                    "doctor": "自检医生",
                }
            )
            self.assertTrue(created["id"].startswith("PET-"))
            updated = await client.add_record(
                created["id"],
                {"diagnosis": "急性肠胃炎", "doctor": "自检医生", "charge": 380},
            )
        finally:
            await client.aclose()
        self.assertEqual(updated["visitCount"], 1)
        self.assertEqual(updated["records"][-1]["diagnosis"], "急性肠胃炎")


class TestOllamaClient(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake, cls.server = start_fake_server()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_render_case_keeps_followup_verbatim(self):
        """病历渲染保留 followUp 原文,供模型照抄。"""
        pet = self.fake.pets["PET-000001"]
        text = render_case(pet)
        self.assertIn("一个月后复查血常规", text)
        self.assertIn("阿莫西林", text)
        self.assertIn("旺财", text)

    async def test_summarize_returns_model_content(self):
        summarizer = OllamaSummarizer(base_url=self.base, model="fake-model")
        try:
            summary = await summarizer.summarize(self.fake.pets["PET-000001"])
        finally:
            await summarizer.aclose()
        self.assertIn("病情小结", summary)
        self.assertIn("复查", summary)
        # 发给模型的资料里包含病历原文
        sent = self.fake.requests[-1][2]
        self.assertIn("一个月后复查血常规", sent["messages"][-1]["content"])

    async def test_empty_model_reply_is_reported(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"message": {"content": "   "}})
        )
        summarizer = OllamaSummarizer(
            base_url="http://fake", transport=transport
        )
        try:
            with self.assertRaisesRegex(OllamaError, "没有返回内容"):
                await summarizer.summarize({"name": "x", "records": [{}]})
        finally:
            await summarizer.aclose()


class TestMcpTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake, self.server = start_fake_server()
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._env = {
            "PET_API_URL": base,
            "OLLAMA_URL": base,
            "OLLAMA_MODEL": "fake-model",
        }
        self._old = {key: os.environ.get(key) for key in self._env}
        os.environ.update(self._env)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    async def test_all_five_tools_are_registered(self):
        tools = await mcp.list_tools()
        names = {tool.name for tool in tools}
        self.assertEqual(
            names,
            {"list_pets", "create_pet", "get_pet", "add_record", "followup_summary"},
        )

    async def test_list_pets_through_mcp(self):
        result, data = await call_tool(
            "list_pets", {"species": "犬", "page_size": 5}
        )
        self.assertFalse(result.is_error)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["id"], "PET-000001")

    async def test_invalid_arguments_are_rejected(self):
        """参数校验失败抛 ToolError;经 MCP 协议时会转成 isError 结果。"""
        for name, args in [
            ("list_pets", {"species": "恐龙"}),
            ("list_pets", {"order": "up"}),
            ("list_pets", {"page_size": 9999}),
            ("get_pet", {"pet_id": "../admin/compact"}),
        ]:
            with self.assertRaises(ToolError, msg=f"{name} {args} 应报错"):
                await mcp.call_tool(name, args)

    async def test_full_workflow_through_mcp(self):
        """新建档案 → 追加病历 → 查询 → 随访小结,全部通过 MCP 工具完成。"""
        result, created = await call_tool(
            "create_pet",
            {
                "name": "端检犬",
                "species": "犬",
                "owner_name": "测试",
                "owner_phone": "13900000000",
                "disease": "自检",
                "doctor": "自检医生",
                "status": "待就诊",
            },
        )
        self.assertFalse(result.is_error)
        pet_id = created["id"]

        result, updated = await call_tool(
            "add_record",
            {
                "pet_id": pet_id,
                "diagnosis": "急性肠胃炎",
                "doctor": "自检医生",
                "symptoms": "呕吐腹泻",
                "prescription": ["阿莫西林"],
                "follow_up": "一周后复查",
                "charge": 380,
            },
        )
        self.assertFalse(result.is_error)
        self.assertEqual(updated["visitCount"], 1)

        result, fetched = await call_tool("get_pet", {"pet_id": pet_id})
        self.assertFalse(result.is_error)
        self.assertEqual(fetched["records"][-1]["followUp"], "一周后复查")

        result, summary = await call_tool("followup_summary", {"pet_id": pet_id})
        self.assertFalse(result.is_error)
        self.assertEqual(summary["model"], "fake-model")
        self.assertIn("病情小结", summary["summary"])


if __name__ == "__main__":
    unittest.main()
