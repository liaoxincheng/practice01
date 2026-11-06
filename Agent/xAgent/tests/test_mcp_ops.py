"""MCP 工具接入(mcp_ops)的行为测试。

覆盖:工具注册与命名规则(mcp_ 前缀 / 非法字符转下划线 / 重名加序号)、
服务器连不上自动跳过、连接卡死超时保护、mcp 包未安装时优雅降级、
调用时新开连接、返回内容渲染(文字块 / 结构化数据 / is_error 加"错误:"前缀)、
以及用真的 mcp 包(进程内连接)做一次端到端验证。

运行方式(在项目根目录):
    venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from core.config import McpServerConfig
from core.mcp_ops import register_mcp_ops
from core.tools import Tool, ToolRegistry


def remote_tool(name: str, description: str = "", schema: dict | None = None):
    """构造一个假的"远端 MCP 工具"(list_tools 返回的条目)。"""
    return SimpleNamespace(
        name=name,
        description=description,
        input_schema=schema or {"type": "object", "properties": {}},
    )


def text_result(text: str, is_error: bool = False):
    """构造一个假的 MCP 调用结果(返回一段文字)。"""
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        structured_content=None,
        is_error=is_error,
    )


class FakeClient:
    """测试用的假 MCP 客户端(代替 mcp.Client)。"""

    def __init__(self, url, timeout_seconds, tools, results, fail_call=None):
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._tools = tools
        self._results = results
        self._fail_call = fail_call

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, arguments):
        if self._fail_call is not None:
            raise RuntimeError(self._fail_call)
        value = self._results.get(name)
        if callable(value):
            # 传入 client 本体,测试可以按"连的是哪台服务器"返回不同结果
            return value(arguments, client=self)
        return value if value is not None else text_result(f"(未预设结果:{name})")


class HungClient(FakeClient):
    """连上后一直不回应的假客户端:用来验证发现阶段的超时保护。"""

    async def __aenter__(self):
        await asyncio.sleep(3600)
        return self


def make_factory(tools_by_url=None, results=None, fail_urls=None, hang_urls=None,
                 fail_call=None):
    """创建假客户端工厂,返回 (factory, calls)。

    calls 记录每次创建客户端时收到的 (url, timeout_seconds)。
    fail_urls: 连接时直接抛错的地址集合(模拟"服务器没启动")。
    hang_urls: 连接后不响应的地址集合(模拟"服务器卡死")。
    """
    calls = []

    def factory(url, timeout_seconds):
        calls.append((url, timeout_seconds))
        if fail_urls and url in fail_urls:
            raise ConnectionError(f"连不上 {url}")
        tools = (tools_by_url or {}).get(url, [])
        if hang_urls and url in hang_urls:
            return HungClient(url, timeout_seconds, tools, results or {})
        return FakeClient(url, timeout_seconds, tools, results or {}, fail_call)

    return factory, calls


def make_registry(servers, *, timeout_seconds=30.0, factory=None,
                  mcp_installed=True):
    """注册 MCP 工具,返回 (registry, statuses)。

    mcp_installed=False 时模拟"环境中没装 mcp 包"。
    """
    registry = ToolRegistry()
    if not mcp_installed:
        with mock.patch("core.mcp_ops._make_default_factory", return_value=None):
            statuses = register_mcp_ops(registry, servers,
                                        timeout_seconds=timeout_seconds)
        return registry, statuses
    statuses = register_mcp_ops(registry, servers,
                                timeout_seconds=timeout_seconds,
                                client_factory=factory)
    return registry, statuses


def execute(tools: ToolRegistry, name: str, arguments: dict) -> str:
    return asyncio.run(tools.execute(name, json.dumps(arguments, ensure_ascii=False)))


PET = McpServerConfig(name="pet", url="http://127.0.0.1:8081/mcp")
LLM = McpServerConfig(name="anythingllm", url="http://127.0.0.1:8082/mcp")


class TestRegisterMcpOps(unittest.TestCase):
    """工具注册与命名规则。"""

    def test_registers_prefixed_tools_for_each_server(self):
        factory, calls = make_factory(tools_by_url={
            PET.url: [remote_tool("list_pets", "列出宠物")],
            LLM.url: [remote_tool("query_workspace", "查询知识库")],
        })
        registry, statuses = make_registry([PET, LLM], factory=factory)

        self.assertIn("mcp_pet_list_pets", registry.names())
        self.assertIn("mcp_anythingllm_query_workspace", registry.names())
        self.assertEqual(len(statuses), 2)
        self.assertIn("pet", statuses[0])
        self.assertIn("1 个工具", statuses[0])
        self.assertEqual(len(calls), 2)  # 每台服务器一次发现连接

    def test_description_marks_mcp_source(self):
        factory, _ = make_factory(tools_by_url={
            PET.url: [remote_tool("list_pets", "列出全部宠物")],
        })
        registry, _ = make_registry([PET], factory=factory)
        schemas = {s["function"]["name"]: s["function"]
                   for s in registry.openai_schemas()}
        self.assertEqual(
            schemas["mcp_pet_list_pets"]["description"],
            "[MCP 服务器 pet] 列出全部宠物",
        )

    def test_remote_schema_is_used_as_parameters(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        factory, _ = make_factory(tools_by_url={
            PET.url: [remote_tool("get_pet", "查询宠物", schema=schema)],
        })
        registry, _ = make_registry([PET], factory=factory)
        schemas = {s["function"]["name"]: s["function"]
                   for s in registry.openai_schemas()}
        self.assertEqual(schemas["mcp_pet_get_pet"]["parameters"], schema)

    def test_illegal_chars_in_names_are_sanitized(self):
        # 服务器名和工具名里的大写 / 连字符 / 中文一律转小写下划线
        factory, _ = make_factory(tools_by_url={
            PET.url: [remote_tool("Pet.List@全部", "x")],
        })
        registry, statuses = make_registry(
            [McpServerConfig(name="Anything-LLM", url=PET.url)], factory=factory,
        )
        self.assertIn("mcp_anything_llm_pet_list", registry.names())
        # 状态说明里仍显示服务器原名,方便用户对号入座
        self.assertIn("Anything-LLM", statuses[0])

    def test_duplicate_names_get_number_suffix(self):
        # 两个同名服务器提供同名工具:第二个注册的工具加序号,互不覆盖
        s1 = McpServerConfig(name="pet", url="http://a/mcp")
        s2 = McpServerConfig(name="pet", url="http://b/mcp")
        factory, _ = make_factory(tools_by_url={
            s1.url: [remote_tool("list_pets")],
            s2.url: [remote_tool("list_pets")],
        })
        # 结果按"连的是哪台服务器"区分,验证两个工具各绑定自己的地址
        results = {"list_pets": lambda args, client: text_result(client.url)}
        factory, _ = make_factory(
            tools_by_url={s1.url: [remote_tool("list_pets")],
                          s2.url: [remote_tool("list_pets")]},
            results=results,
        )
        registry, _ = make_registry([s1, s2], factory=factory)
        self.assertIn("mcp_pet_list_pets", registry.names())
        self.assertIn("mcp_pet_list_pets_2", registry.names())
        self.assertIn("http://a/mcp",
                      execute(registry, "mcp_pet_list_pets", {}))
        self.assertIn("http://b/mcp",
                      execute(registry, "mcp_pet_list_pets_2", {}))

    def test_existing_registry_name_is_not_overwritten(self):
        # 注册中心里已有同名工具(如另一个模块注册的):新工具加序号,
        # 老工具及其 handler 原样保留
        registry = ToolRegistry()
        registry.register(Tool(
            name="mcp_pet_list_pets",
            description="旧工具",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: "old",
        ))
        factory, _ = make_factory(tools_by_url={
            PET.url: [remote_tool("list_pets")],
        })
        statuses = register_mcp_ops(registry, [PET], client_factory=factory)
        self.assertIn("mcp_pet_list_pets_2", registry.names())
        self.assertIn("已接入 1 个工具", statuses[0])
        self.assertEqual(execute(registry, "mcp_pet_list_pets", {}), "old")

    def test_unreachable_server_is_skipped(self):
        factory, _ = make_factory(
            tools_by_url={LLM.url: [remote_tool("query_workspace")]},
            fail_urls={PET.url},
        )
        registry, statuses = make_registry([PET, LLM], factory=factory)
        self.assertIn("mcp_anythingllm_query_workspace", registry.names())
        self.assertNotIn("mcp_pet_", " ".join(registry.names()))
        self.assertIn("连接失败", statuses[0])
        self.assertIn("已接入", statuses[1])

    def test_hung_server_is_timed_out(self):
        factory, _ = make_factory(tools_by_url={}, hang_urls={PET.url})
        _, statuses = make_registry([PET], factory=factory, timeout_seconds=0.2)
        self.assertIn("连接失败", statuses[0])

    def test_exception_group_is_unwrapped_to_root_cause(self):
        # anyio 会把连接错误包成 ExceptionGroup,状态里要展示真正的原因
        def group_factory(url, timeout_seconds):
            raise ExceptionGroup("unhandled errors in a TaskGroup",
                                 [ConnectionRefusedError("目标机器拒绝连接")])

        registry, statuses = make_registry([PET], factory=group_factory)
        self.assertIn("连接失败", statuses[0])
        self.assertIn("拒绝连接", statuses[0])
        self.assertNotIn("TaskGroup", statuses[0])

    def test_server_without_tools_is_noted(self):
        factory, _ = make_factory(tools_by_url={PET.url: []})
        registry, statuses = make_registry([PET], factory=factory)
        self.assertEqual(registry.names(), [])
        self.assertIn("没有提供任何工具", statuses[0])

    def test_empty_server_list_does_nothing(self):
        registry = ToolRegistry()
        statuses = register_mcp_ops(registry, [], client_factory=lambda u, t: None)
        self.assertEqual(statuses, [])
        self.assertEqual(registry.names(), [])

    def test_missing_mcp_package_is_reported(self):
        registry, statuses = make_registry([PET], mcp_installed=False)
        self.assertEqual(registry.names(), [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("未安装 mcp 包", statuses[0])


class TestMcpToolExecution(unittest.TestCase):
    """调用 MCP 工具时的连接与结果渲染。"""

    def test_call_opens_fresh_connection_and_forwards_arguments(self):
        factory, calls = make_factory(
            tools_by_url={PET.url: [remote_tool("add_record", "添加病历")]},
            results={"add_record": lambda args, client: text_result(
                "已添加 " + str(args.get("pet_id")))},
        )
        registry, _ = make_registry([PET], factory=factory)
        result = execute(registry, "mcp_pet_add_record", {"pet_id": "PET-42"})
        self.assertIn("已添加 PET-42", result)
        # 第一次连接是启动时发现,第二次是这次调用(每次调用都新开连接)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

    def test_call_failure_is_reported_as_error(self):
        factory, _ = make_factory(
            tools_by_url={PET.url: [remote_tool("list_pets")]},
            fail_call="服务器内部错误",
        )
        registry, _ = make_registry([PET], factory=factory)
        result = execute(registry, "mcp_pet_list_pets", {})
        self.assertIn("错误:", result)
        self.assertIn("服务器内部错误", result)

    def test_is_error_result_gets_error_prefix(self):
        factory, _ = make_factory(
            tools_by_url={PET.url: [remote_tool("get_pet")]},
            results={"get_pet": text_result("PET-42 不存在", is_error=True)},
        )
        registry, _ = make_registry([PET], factory=factory)
        result = execute(registry, "mcp_pet_get_pet", {"pet_id": "PET-42"})
        self.assertTrue(result.startswith("错误:"), result)
        self.assertIn("PET-42 不存在", result)

    def test_structured_content_is_rendered_as_json(self):
        structured = SimpleNamespace(
            content=[],
            structured_content={"total": 3, "names": ["豆豆", "球球"]},
            is_error=False,
        )
        factory, _ = make_factory(
            tools_by_url={PET.url: [remote_tool("list_pets")]},
            results={"list_pets": structured},
        )
        registry, _ = make_registry([PET], factory=factory)
        result = execute(registry, "mcp_pet_list_pets", {})
        self.assertIn("total", result)
        self.assertIn("豆豆", result)

    def test_empty_result_gets_placeholder(self):
        empty = SimpleNamespace(content=[], structured_content=None, is_error=False)
        factory, _ = make_factory(
            tools_by_url={PET.url: [remote_tool("list_pets")]},
            results={"list_pets": empty},
        )
        registry, _ = make_registry([PET], factory=factory)
        result = execute(registry, "mcp_pet_list_pets", {})
        self.assertIn("没有返回内容", result)


try:
    from mcp import Client
    from mcp.server.mcpserver import MCPServer

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


@unittest.skipUnless(HAS_MCP, "需要安装 mcp 包(项目 venv 中已安装)")
class TestRealMcpInProcess(unittest.TestCase):
    """用真的 mcp 包(进程内连接)做一次端到端验证。"""

    def test_real_mcp_server_tool_round_trip(self):
        server = MCPServer(name="pet-test")

        @server.tool()
        def hello(name: str) -> str:
            """打个招呼。"""
            return f"你好,{name}!"

        factory = lambda url, timeout_seconds: Client(server)  # noqa: E731
        registry, statuses = make_registry(
            [McpServerConfig(name="pet", url="in-process://pet-test")],
            factory=factory,
        )
        self.assertIn("已接入 1 个工具", statuses[0])
        self.assertIn("mcp_pet_hello", registry.names())

        result = execute(registry, "mcp_pet_hello", {"name": "世界"})
        self.assertIn("你好,世界", result)

        # 描述来自函数的 docstring
        schemas = {s["function"]["name"]: s["function"]
                   for s in registry.openai_schemas()}
        self.assertIn("打个招呼", schemas["mcp_pet_hello"]["description"])


if __name__ == "__main__":
    unittest.main()
