""".mcp.json 解析、stdio MCP 接入和 createMcp 工具的测试。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.config import McpServerConfig
from core.mcp_config import load_mcp_json
from core.mcp_config_ops import register_mcp_config_ops
from core.mcp_ops import register_mcp_ops
from core.tools import ToolRegistry


def remote_tool(name: str, description: str = ""):
    return SimpleNamespace(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}},
    )


def text_result(text: str):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        structured_content=None,
        is_error=False,
    )


class FakeClient:
    def __init__(self, label, tools, results=None):
        self.label = label
        self._tools = tools
        self._results = results or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, arguments):
        value = self._results.get(name)
        return value if value is not None else text_result(f"{self.label}:{name}")


def execute(tools: ToolRegistry, name: str, arguments: dict) -> str:
    return asyncio.run(tools.execute(name, json.dumps(arguments, ensure_ascii=False)))


class TestLoadMcpJson(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = self.root / ".mcp.json"

    def write(self, payload) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_parses_stdio_and_http_servers(self):
        self.write({
            "mcpServers": {
                "local": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["server.py", "--flag"],
                    "env": {"TOKEN": "${TEST_TOKEN}"},
                    "cwd": ".",
                },
                "remote": {
                    "type": "streamable_http",
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer ${TEST_TOKEN}"},
                },
            }
        })
        with mock.patch.dict(os.environ, {"TEST_TOKEN": "abc123"}):
            servers, issues = load_mcp_json(self.path)

        self.assertEqual(issues, [])
        by_name = {server.name: server for server in servers}
        self.assertEqual(by_name["local"].transport, "stdio")
        self.assertEqual(by_name["local"].command, "python")
        self.assertEqual(by_name["local"].args, ("server.py", "--flag"))
        self.assertEqual(by_name["local"].env["TOKEN"], "abc123")
        self.assertEqual(Path(by_name["local"].cwd), self.root)
        self.assertEqual(by_name["remote"].transport, "http")
        self.assertEqual(by_name["remote"].headers["Authorization"], "Bearer abc123")

    def test_transport_is_inferred(self):
        self.write({
            "mcpServers": {
                "local": {"command": "node", "args": ["server.js"]},
                "remote": {"url": "http://127.0.0.1:9000/mcp"},
            }
        })
        servers, issues = load_mcp_json(self.path)
        self.assertEqual(issues, [])
        self.assertEqual({s.name: s.transport for s in servers},
                         {"local": "stdio", "remote": "http"})

    def test_env_default_value(self):
        self.write({
            "mcpServers": {
                "remote": {"url": "${NO_SUCH_VAR:-http://127.0.0.1:9000/mcp}"}
            }
        })
        servers, issues = load_mcp_json(self.path)
        self.assertEqual(issues, [])
        self.assertEqual(servers[0].url, "http://127.0.0.1:9000/mcp")

    def test_missing_env_variable_is_reported_per_server(self):
        self.write({
            "mcpServers": {
                "bad": {"url": "http://example.com/${MISSING_VAR}/mcp"},
                "good": {"url": "http://127.0.0.1:9000/mcp"},
            }
        })
        servers, issues = load_mcp_json(self.path)
        self.assertEqual([s.name for s in servers], ["good"])
        self.assertTrue(any("bad" in issue and "未设置" in issue for issue in issues))

    def test_invalid_json_is_reported(self):
        self.path.write_text("{not json", encoding="utf-8")
        servers, issues = load_mcp_json(self.path)
        self.assertEqual(servers, [])
        self.assertTrue(any("不是合法 JSON" in issue for issue in issues))

    def test_missing_file_is_not_an_error(self):
        servers, issues = load_mcp_json(self.root / "none.json")
        self.assertEqual(servers, [])
        self.assertEqual(issues, [])

    def test_invalid_server_entry_is_skipped(self):
        self.write({"mcpServers": {"empty": {"type": "stdio"}}})
        servers, issues = load_mcp_json(self.path)
        self.assertEqual(servers, [])
        self.assertTrue(any("command" in issue for issue in issues))


class TestStdioRegistration(unittest.TestCase):
    def test_stdio_server_tools_are_registered_and_callable(self):
        server = McpServerConfig(
            name="local-demo",
            transport="stdio",
            command="python",
            args=("server.py",),
            env={"TOKEN": "abc"},
            cwd="C:/demo",
        )
        calls = []

        def stdio_factory(received, timeout_seconds):
            calls.append((received, timeout_seconds))
            return FakeClient(
                received.name,
                [remote_tool("hello", "打个招呼")],
                {"hello": text_result("你好,世界")},
            )

        registry = ToolRegistry()
        statuses = register_mcp_ops(
            registry,
            [server],
            stdio_client_factory=stdio_factory,
        )

        self.assertIn("mcp_local_demo_hello", registry.names())
        self.assertIn("已接入 1 个工具", statuses[0])
        self.assertEqual(calls[0][0].command, "python")
        self.assertEqual(calls[0][0].args, ("server.py",))
        result = execute(registry, "mcp_local_demo_hello", {})
        self.assertIn("你好,世界", result)
        # 发现工具一次 + 调用工具一次
        self.assertEqual(len(calls), 2)


try:
    import mcp  # noqa: F401
    from mcp.server.mcpserver import MCPServer  # noqa: F401

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


@unittest.skipUnless(HAS_MCP, "需要安装 mcp 包(项目 venv 中已安装)")
class TestRealStdioMcp(unittest.TestCase):
    """用真实的 mcp 包启动 stdio 子进程服务器,做一次端到端验证。"""

    def test_stdio_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "server.py").write_text(
                "from mcp.server.mcpserver import MCPServer\n"
                "\n"
                'server = MCPServer("demo")\n'
                "\n"
                "@server.tool()\n"
                'def hello(name: str = "world") -> str:\n'
                '    return f"你好,{name}"\n'
                "\n"
                'if __name__ == "__main__":\n'
                "    server.run()\n",
                encoding="utf-8",
            )
            server = McpServerConfig(
                name="local-demo",
                transport="stdio",
                command=sys.executable,
                args=(str(root / "server.py"),),
                cwd=str(root),
            )
            registry = ToolRegistry()
            statuses = register_mcp_ops(
                registry, [server], timeout_seconds=30
            )
            self.assertIn("已接入 1 个工具", statuses[0])
            self.assertIn("mcp_local_demo_hello", registry.names())

            result = execute(registry, "mcp_local_demo_hello", {"name": "世界"})
            self.assertIn("你好,世界", result)


class TestCreateMcp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = self.root / ".mcp.json"
        self.tools = ToolRegistry()
        register_mcp_config_ops(self.tools, self.path)

    def test_writes_new_stdio_server(self):
        result = execute(self.tools, "createMcp", {
            "servers": {
                "local": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["server.py"],
                    "env": {"TOKEN": "super-secret"},
                }
            }
        })
        self.assertTrue(result.startswith("成功"), result)
        self.assertNotIn("super-secret", result)
        self.assertIn("重启", result)

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["mcpServers"]["local"]["command"], "python"
        )
        self.assertEqual(
            payload["mcpServers"]["local"]["env"]["TOKEN"], "super-secret"
        )

    def test_merges_existing_servers_and_keeps_other_keys(self):
        self.path.write_text(json.dumps({
            "mcpServers": {"old": {"url": "http://127.0.0.1:9000/mcp"}},
            "otherSetting": 1,
        }), encoding="utf-8")

        result = execute(self.tools, "createMcp", {
            "servers": {"new": {"url": "http://127.0.0.1:9001/mcp"}}
        })
        self.assertTrue(result.startswith("成功"), result)

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(payload["mcpServers"]), {"old", "new"})
        self.assertEqual(payload["otherSetting"], 1)

    def test_conflict_requires_overwrite(self):
        execute(self.tools, "createMcp", {
            "servers": {"demo": {"url": "http://127.0.0.1:9000/mcp"}}
        })
        original = self.path.read_text(encoding="utf-8")

        result = execute(self.tools, "createMcp", {
            "servers": {"demo": {"url": "http://127.0.0.1:9001/mcp"}}
        })
        self.assertIn("错误", result)
        self.assertIn("已存在", result)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

        result = execute(self.tools, "createMcp", {
            "servers": {"demo": {"url": "http://127.0.0.1:9001/mcp"}},
            "overwrite": True,
        })
        self.assertTrue(result.startswith("成功"), result)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["mcpServers"]["demo"]["url"], "http://127.0.0.1:9001/mcp"
        )

    def test_invalid_config_does_not_touch_file(self):
        result = execute(self.tools, "createMcp", {
            "servers": {"bad": {"type": "stdio"}}
        })
        self.assertIn("错误", result)
        self.assertIn("command", result)
        self.assertFalse(self.path.exists())

    def test_accepts_json_string(self):
        result = execute(self.tools, "createMcp", {
            "servers": json.dumps({
                "demo": {"command": "python", "args": ["server.py"]}
            })
        })
        self.assertTrue(result.startswith("成功"), result)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("demo", payload["mcpServers"])

    def test_secret_hint_for_literal_values(self):
        result = execute(self.tools, "createMcp", {
            "servers": {
                "demo": {
                    "url": "http://127.0.0.1:9000/mcp",
                    "headers": {"Authorization": "Bearer literal-token"},
                }
            }
        })
        self.assertIn("安全提示", result)
        self.assertNotIn("literal-token", result)


if __name__ == "__main__":
    unittest.main()
