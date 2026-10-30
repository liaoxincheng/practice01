"""WorkspaceClient 的单元测试。

用 httpx.MockTransport 模拟 AnythingLLM 服务,不联网、不需要真实服务。

运行:
    python -m unittest test_api -v
"""
from __future__ import annotations

import json
import unittest

import httpx

from anythingllm_api import AnythingLLMError, WorkspaceClient


def make_client(handler, api_key: str = "test-key") -> WorkspaceClient:
    return WorkspaceClient(
        base_url="http://fake-anythingllm:3001",
        api_key=api_key,
        transport=httpx.MockTransport(handler),
    )


class TestListWorkspaces(unittest.IsolatedAsyncioTestCase):
    async def test_returns_workspaces_and_sends_bearer_token(self):
        """正常返回列表,并带上 Bearer 鉴权头。"""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            seen["path"] = request.url.path
            return httpx.Response(
                200,
                json={
                    "workspaces": [
                        {"id": 1, "name": "课程资料", "slug": "course"},
                        {"id": 2, "name": "病历", "slug": "pets"},
                    ]
                },
            )

        client = make_client(handler)
        try:
            workspaces = await client.list_workspaces()
        finally:
            await client.aclose()

        self.assertEqual(seen["path"], "/api/v1/workspaces")
        self.assertEqual(seen["auth"], "Bearer test-key")
        self.assertEqual([ws["slug"] for ws in workspaces], ["course", "pets"])

    async def test_missing_api_key_is_reported_before_network(self):
        """没配 Key 时直接给出配置提示,不发请求。"""
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"workspaces": []})

        client = make_client(handler, api_key="")
        with self.assertRaisesRegex(AnythingLLMError, "ANYTHINGLLM_API_KEY"):
            await client.list_workspaces()
        self.assertFalse(called)

    async def test_invalid_response_shape_is_reported(self):
        """响应缺少 workspaces 字段时报错。"""
        client = make_client(lambda r: httpx.Response(200, json={"ok": True}))
        try:
            with self.assertRaisesRegex(AnythingLLMError, "workspaces"):
                await client.list_workspaces()
        finally:
            await client.aclose()


class TestAsk(unittest.IsolatedAsyncioTestCase):
    async def test_posts_message_and_mode(self):
        """请求体包含 message/mode,返回 reply 与 sources。"""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "textResponse": "根据课程资料,Agent 分为 core 与 tui 两层。",
                    "sources": [{"title": "xAgent.md"}],
                    "type": "textResponse",
                },
            )

        client = make_client(handler)
        try:
            result = await client.ask("course", "xAgent 怎么分层?", "query")
        finally:
            await client.aclose()

        self.assertEqual(seen["path"], "/api/v1/workspace/course/chat")
        self.assertEqual(seen["body"], {"message": "xAgent 怎么分层?", "mode": "query"})
        self.assertEqual(result["reply"], "根据课程资料,Agent 分为 core 与 tui 两层。")
        self.assertEqual(result["sources"], [{"title": "xAgent.md"}])
        self.assertEqual(result["mode"], "query")

    async def test_http_error_includes_server_message(self):
        """HTTP 403 时把服务端错误信息带出来。"""
        client = make_client(
            lambda r: httpx.Response(403, json={"error": "Invalid API key"})
        )
        try:
            with self.assertRaisesRegex(AnythingLLMError, "403.*Invalid API key"):
                await client.ask("course", "你好")
        finally:
            await client.aclose()

    async def test_business_error_field_is_reported(self):
        """HTTP 200 但响应里有 error 字段时也视为失败。"""
        client = make_client(
            lambda r: httpx.Response(200, json={"error": "Workspace not found"})
        )
        try:
            with self.assertRaisesRegex(AnythingLLMError, "Workspace not found"):
                await client.ask("nope", "你好")
        finally:
            await client.aclose()

    async def test_validation_errors_do_not_hit_network(self):
        """空 slug / 空消息 / 非法 mode 在本地校验。"""
        client = make_client(
            lambda r: httpx.Response(200, json={"textResponse": "x"})
        )
        try:
            for slug, message, mode in [
                ("", "你好", "query"),
                ("course", "  ", "query"),
                ("course", "你好", "unknown"),
            ]:
                with self.assertRaises(AnythingLLMError):
                    await client.ask(slug, message, mode)
        finally:
            await client.aclose()

    async def test_missing_reply_is_reported(self):
        """服务端 200 但没有回答文本时报错。"""
        client = make_client(lambda r: httpx.Response(200, json={"sources": []}))
        try:
            with self.assertRaisesRegex(AnythingLLMError, "没有返回回答"):
                await client.ask("course", "你好")
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
