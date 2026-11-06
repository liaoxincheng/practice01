"""AGENTS.md 读取与注入的行为测试。

覆盖:文件缺失/空文件/UTF-8/GBK 编码、消息格式、会话开始时只注入一次、
多轮对话与工具调用中始终复用同一条消息、reset 后重新注入、TUI 欢迎语提示。

运行方式(在项目根目录):
    venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from textual.widgets import RichLog

from core import events
from core.agent import Agent
from core.agents_md import (
    AGENTS_MD_FILENAME,
    build_agents_md_message,
    load_agents_md,
)
from core.tools import Tool, ToolRegistry
from tests.test_core import FakeLLM
from tests.test_tui_smoke import log_text
from tui.app import ChatApp


def run_chat(agent: Agent, text: str) -> list:
    async def _run():
        return [ev async for ev in agent.chat(text)]

    return asyncio.run(_run())


def simple_agent(agents_md: str | None, turns: list | None = None) -> Agent:
    """一个只回一句话的 Agent(不需要工具)。"""
    return Agent(
        llm=FakeLLM(turns or [[events.TextChunk("收到"), events.StreamEnd("stop")]]),
        tools=ToolRegistry(),
        agents_md=agents_md,
    )


class TestLoadAgentsMd(unittest.TestCase):
    """读取 AGENTS.md 文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_missing_file_returns_none(self):
        self.assertIsNone(load_agents_md(self.root))

    def test_empty_file_returns_none(self):
        (self.root / AGENTS_MD_FILENAME).write_text("  \n\n", encoding="utf-8")
        self.assertIsNone(load_agents_md(self.root))

    def test_reads_utf8_and_strips(self):
        (self.root / AGENTS_MD_FILENAME).write_text(
            "\n# 项目约定\n\n- 用中文回答\n\n", encoding="utf-8"
        )
        self.assertEqual(load_agents_md(self.root), "# 项目约定\n\n- 用中文回答")

    def test_reads_gbk_file(self):
        """兼容用记事本 GBK 编码保存的文件。"""
        (self.root / AGENTS_MD_FILENAME).write_bytes(
            "# 项目约定\n用中文回答".encode("gbk")
        )
        self.assertEqual(load_agents_md(self.root), "# 项目约定\n用中文回答")

    def test_directory_named_agents_md_returns_none(self):
        (self.root / AGENTS_MD_FILENAME).mkdir()
        self.assertIsNone(load_agents_md(self.root))


class TestAgentsMdMessage(unittest.TestCase):
    """注入消息的格式。"""

    def test_message_is_system_role_with_header_and_content(self):
        message = build_agents_md_message("# 项目约定\n用中文回答")
        self.assertEqual(message["role"], "system")
        self.assertIn("AGENTS.md", message["content"])
        self.assertIn("# 项目约定\n用中文回答", message["content"])


class TestAgentInjection(unittest.TestCase):
    """Agent 会话中的注入行为。"""

    def test_injected_as_first_message_on_load(self):
        agent = simple_agent("# 项目约定\n用中文回答")
        self.assertTrue(agent.has_agents_md)
        self.assertEqual(len(agent.messages), 1)
        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertIn("用中文回答", agent.messages[0]["content"])

    def test_no_agents_md_means_no_extra_message(self):
        agent = simple_agent(None)
        self.assertFalse(agent.has_agents_md)
        self.assertEqual(agent.messages, [])

    def test_every_llm_call_reuses_the_same_single_message(self):
        """多轮对话:每次请求都带上同一条消息,且只出现一次(缓存前缀稳定)。"""
        llm = FakeLLM([
            [events.TextChunk("第一轮"), events.StreamEnd("stop")],
            [events.TextChunk("第二轮"), events.StreamEnd("stop")],
        ])
        agent = Agent(llm=llm, tools=ToolRegistry(), agents_md="# 项目约定")

        run_chat(agent, "你好")
        run_chat(agent, "继续")

        self.assertEqual(len(llm.calls), 2)
        for call in llm.calls:
            self.assertEqual(call[0]["role"], "system")
            self.assertIn("# 项目约定", call[0]["content"])
            self.assertEqual(
                sum(1 for m in call if m["role"] == "system"), 1,
                "AGENTS.md 只应注入一次,不能每轮重复",
            )
        # 两次请求的公共前缀完全相同,利于提示词缓存
        self.assertEqual(llm.calls[0][0], llm.calls[1][0])

    def test_message_stays_first_during_tool_calls(self):
        tools = ToolRegistry()
        tools.register(Tool(
            name="echo",
            description="回声",
            parameters={"type": "object", "properties": {}},
            handler=lambda: "工具结果",
        ))
        llm = FakeLLM([
            [
                events.ToolCallProposal(call_id="call_1", name="echo", arguments="{}"),
                events.StreamEnd("tool_calls"),
            ],
            [events.TextChunk("完成"), events.StreamEnd("stop")],
        ])
        agent = Agent(llm=llm, tools=tools, agents_md="# 项目约定")
        run_chat(agent, "调用工具")

        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertIn("# 项目约定", agent.messages[0]["content"])
        # 工具调用消息都排在注入消息之后
        roles = [m["role"] for m in agent.messages]
        self.assertEqual(roles[0], "system")
        self.assertIn("tool", roles)

    def test_reset_clears_and_reinjects_for_new_session(self):
        agent = simple_agent("# 项目约定", turns=[
            [events.TextChunk("第一次"), events.StreamEnd("stop")],
            [events.TextChunk("新会话"), events.StreamEnd("stop")],
        ])
        run_chat(agent, "你好")
        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertGreater(len(agent.messages), 1)

        agent.reset()
        self.assertEqual(len(agent.messages), 1)
        self.assertEqual(agent.messages[0]["role"], "system")

        run_chat(agent, "重新开始")
        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertEqual(
            sum(1 for m in agent.messages if m["role"] == "system"), 1
        )


class TestTuiWelcome(unittest.IsolatedAsyncioTestCase):
    """TUI 欢迎语展示 AGENTS.md 加载状态。"""

    async def test_welcome_mentions_agents_md(self):
        agent = Agent(
            llm=FakeLLM([[events.TextChunk("x"), events.StreamEnd("stop")]]),
            tools=ToolRegistry(),
            agents_md="# 项目约定",
        )
        app = ChatApp(agent=agent)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            text = log_text(pilot.app)
            self.assertIn("已加载项目说明 AGENTS.md", text)

    async def test_welcome_has_no_line_without_agents_md(self):
        agent = Agent(
            llm=FakeLLM([[events.TextChunk("x"), events.StreamEnd("stop")]]),
            tools=ToolRegistry(),
        )
        app = ChatApp(agent=agent)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            text = log_text(pilot.app)
            self.assertNotIn("已加载项目说明", text)


if __name__ == "__main__":
    unittest.main()
