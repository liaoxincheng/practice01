"""TUI 冒烟测试:用 Textual 的 headless(无头)模式验证界面能正常启动和交互。

不需要真实终端、不需要 LM Studio:
- 界面能挂载、输入框存在;
- 输入消息回车后,core 的事件流被正确渲染到聊天记录里;
- exit 命令能退出程序。

运行方式(在项目根目录):
    venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Optional

from textual.widgets import Input, RichLog, Static

from core import events
from core.agent import Agent
from core.llm import LLMClient
from core.tools import ToolRegistry
from tests.test_core import FakeLLM as TurnFakeLLM
from tests.test_context import make_chat_turns
from tui.app import ChatApp, ConfigNoticeScreen


class FakeLLM(LLMClient):
    """回放固定剧本的假 LLM。"""

    model_name: str = "fake-model"

    def __init__(self, script: list):
        self.script = list(script)

    def stream_chat(self, messages, tools: Optional[list[dict]] = None):
        async def _gen():
            for ev in self.script:
                yield ev

        return _gen()


def log_text(app) -> str:
    """读取聊天记录 RichLog 的当前可见文本(RichLog.lines 是公开属性)。"""
    log = app.query_one("#log", RichLog)
    return "\n".join(getattr(strip, "text", str(strip)) for strip in log.lines)


class TuiSmokeTest(unittest.IsolatedAsyncioTestCase):
    """TUI 无头冒烟测试。"""

    def make_app(self) -> ChatApp:
        agent = Agent(
            llm=FakeLLM([
                events.TextChunk("你好,"),
                events.TextChunk("世界"),
                events.StreamEnd("stop"),
            ]),
            tools=ToolRegistry(),
        )
        return ChatApp(agent=agent)

    async def test_app_mounts(self):
        """界面能挂载:标题、输入框、聊天记录、状态栏都在。"""
        app = self.make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            self.assertTrue(pilot.app.query_one("#input", Input))
            self.assertTrue(pilot.app.query_one("#log", RichLog))
            self.assertTrue(pilot.app.query_one("#status"))
            self.assertIn("fake-model", log_text(pilot.app))

    async def test_mcp_status_is_shown_in_welcome(self):
        """传入 MCP 接入状态时,欢迎语里展示每台服务器的结果。"""
        agent = Agent(llm=FakeLLM([]), tools=ToolRegistry())
        app = ChatApp(agent=agent, mcp_status=[
            "服务器 pet: 已接入 5 个工具(mcp_pet_list_pets)",
            "服务器 anythingllm: 连接失败,已跳过(All connection attempts failed)",
        ])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            text = log_text(pilot.app)
            self.assertIn("MCP 服务器接入情况", text)
            self.assertIn("服务器 pet: 已接入 5 个工具", text)
            self.assertIn("连接失败,已跳过", text)

    async def test_send_message_renders_stream_reply(self):
        """输入消息回车:用户消息和模型流式回复都渲染到聊天记录。"""
        app = self.make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            input_widget = pilot.app.query_one("#input", Input)
            input_widget.value = "你好"
            await pilot.press("enter")
            # 等待后台对话任务完成
            for _ in range(50):
                await pilot.pause(0.05)
                if input_widget.disabled is False:
                    break
            text = log_text(pilot.app)
            self.assertIn("你: 你好", text)
            self.assertIn("你好,世界", text)
            # 对话结束后输入框恢复可用
            self.assertFalse(input_widget.disabled)

    async def test_exit_command_quits_app(self):
        """输入 exit 能退出程序(与 practice01/02 的交互习惯一致)。"""
        app = self.make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            input_widget = pilot.app.query_one("#input", Input)
            input_widget.value = "exit"
            await pilot.press("enter")
            await pilot.pause()
            self.assertTrue(app._exit)

    async def test_help_command_lists_slash_commands(self):
        """/help 直接在界面显示命令列表,不发给模型。"""
        agent = Agent(llm=FakeLLM([]), tools=ToolRegistry())
        app = ChatApp(agent=agent)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            input_widget = pilot.app.query_one("#input", Input)
            input_widget.value = "/help"
            await pilot.press("enter")
            await pilot.pause()
            text = log_text(pilot.app)
            self.assertIn("可用命令", text)
            self.assertIn("/status", text)
            self.assertIn("/skills", text)
            self.assertIn("/mcp", text)
            self.assertIn("/compact", text)
            self.assertIn("/save", text)
            self.assertIn("/resume", text)
            self.assertIn("/sessions", text)
            self.assertIn("/new", text)
            self.assertFalse(input_widget.disabled)

    async def test_skills_are_shown_in_welcome_and_command(self):
        """启动时发现的技能会出现在欢迎语和 /skills 输出里。"""
        agent = Agent(
            llm=FakeLLM([]),
            tools=ToolRegistry(),
            skill_names=["web-search", "pdf-reader"],
        )
        app = ChatApp(agent=agent)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            text = log_text(pilot.app)
            self.assertIn("已发现 2 个技能", text)
            self.assertIn("web-search", text)

            input_widget = pilot.app.query_one("#input", Input)
            input_widget.value = "/skills"
            await pilot.press("enter")
            await pilot.pause()
            text = log_text(pilot.app)
            self.assertIn("已发现 2 个技能", text)
            self.assertIn("pdf-reader", text)

    async def test_mcp_loader_updates_sidebar_in_background(self):
        """MCP 后台加载完成前界面已可用,完成后侧边栏显示真实状态。"""
        import time

        agent = Agent(llm=FakeLLM([]), tools=ToolRegistry())
        calls = []

        def loader() -> list[str]:
            time.sleep(0.15)  # 模拟连接服务器的耗时
            calls.append("done")
            return ["服务器 demo: 已接入 2 个工具(mcp_demo_a, mcp_demo_b)"]

        app = ChatApp(
            agent=agent,
            mcp_status=["MCP 服务器正在后台连接,工具就绪后会显示在右侧栏"],
            mcp_loader=loader,
        )
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            # 界面先挂载,没有被 MCP 连接阻塞
            self.assertTrue(pilot.app.query_one("#input", Input))
            self.assertIn("正在后台连接", log_text(pilot.app))

            for _ in range(50):
                await pilot.pause(0.05)
                if "MCP 后台加载完成" in log_text(pilot.app):
                    break
            self.assertEqual(calls, ["done"])
            self.assertIn("MCP 后台加载完成", log_text(pilot.app))
            self.assertIn("服务器 demo: 已接入 2 个工具", log_text(pilot.app))
            sidebar_mcp = pilot.app.query_one("#side-mcp", Static)
            self.assertIn("demo", str(sidebar_mcp.content))
            self.assertIn("已接入 2 个工具", str(sidebar_mcp.content))

    async def test_startup_notice_opens_and_closes_modal(self):
        """config.ini 自动生成时,启动后弹出说明,按 Esc 可关闭。"""
        agent = Agent(llm=FakeLLM([]), tools=ToolRegistry())
        app = ChatApp(agent=agent, startup_notice="已自动生成 config.ini")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            self.assertIsInstance(pilot.app.screen, ConfigNoticeScreen)
            message = pilot.app.screen.query_one("#config-message", Static)
            self.assertIn("已自动生成 config.ini", str(message.content))

            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(pilot.app.screen, ConfigNoticeScreen)

    async def test_unknown_slash_command_gets_hint(self):
        """未知斜杠命令不发给模型,只提示查看 /help。"""
        agent = Agent(llm=FakeLLM([]), tools=ToolRegistry())
        app = ChatApp(agent=agent)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            input_widget = pilot.app.query_one("#input", Input)
            input_widget.value = "/nope"
            await pilot.press("enter")
            await pilot.pause()
            text = log_text(pilot.app)
            self.assertIn("未知命令", text)
            self.assertIn("/help", text)
            self.assertFalse(input_widget.disabled)

    async def test_tool_call_is_rendered(self):
        """模型调用工具时,界面渲染出"调用工具 / 工具结果"提示。"""
        from core.tools import Tool

        tools = ToolRegistry()
        tools.register(Tool(
            name="echo",
            description="回声工具",
            parameters={"type": "object", "properties": {}},
            handler=lambda: "工具结果内容",
        ))
        agent = Agent(
            llm=FakeLLM([
                events.ToolCallProposal(
                    call_id="call_1", name="echo", arguments="{}"
                ),
                events.StreamEnd("tool_calls"),
                events.TextChunk("完成"),
                events.StreamEnd("stop"),
            ]),
            tools=tools,
        )
        app = ChatApp(agent=agent)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            input_widget = pilot.app.query_one("#input", Input)
            input_widget.value = "调用工具试试"
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.05)
                if input_widget.disabled is False:
                    break
            text = log_text(pilot.app)
            self.assertIn("🔧 调用工具: echo", text)
            self.assertIn("✅ 工具结果: 工具结果内容", text)
            self.assertIn("完成", text)


class ContextTuiTest(unittest.IsolatedAsyncioTestCase):
    """TUI 的上下文压缩、会话保存 / 恢复和状态栏 Token 展示。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archive_dir = Path(self._tmp.name) / "sessions"

    def make_agent(
        self, turns: int, *, with_summary: bool = True, archive: bool = True
    ) -> Agent:
        script = make_chat_turns(turns)
        if with_summary:
            script.append(
                [events.TextChunk("摘要:测试对话内容"), events.StreamEnd("stop")]
            )
        return Agent(
            llm=TurnFakeLLM(script),
            tools=ToolRegistry(),
            archive_dir=self.archive_dir if archive else None,
            keep_recent_turns=2,
            max_context_tokens=1000,
        )

    @staticmethod
    def status_text(app) -> str:
        return str(app.query_one("#status", Static).content)

    async def submit(self, pilot, text: str, until=None) -> Input:
        """输入一条消息并等待后台任务完成(或 until 条件成立)。"""
        input_widget = pilot.app.query_one("#input", Input)
        input_widget.value = text
        await pilot.press("enter")
        for _ in range(120):
            await pilot.pause(0.05)
            if until is not None:
                if until():
                    break
            elif not input_widget.disabled:
                break
        return input_widget

    async def test_status_and_suggester_show_context(self):
        agent = self.make_agent(1)
        app = ChatApp(agent=agent)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            input_widget = pilot.app.query_one("#input", Input)
            self.assertIsNotNone(input_widget.suggester)
            status = self.status_text(pilot.app)
            self.assertIn("上下文 0 条", status)
            self.assertIn("tokens", status)
            sidebar = pilot.app.query_one("#side-context", Static)
            self.assertIn("上下文", str(sidebar.content))
            self.assertIn("0 / 1000 tokens", str(sidebar.content))

            await self.submit(
                pilot, "你好", until=lambda: "回答1" in log_text(pilot.app)
            )
            status = self.status_text(pilot.app)
            self.assertIn("上下文 2 条", status)
            self.assertIn("对话 2 条", str(sidebar.content))

    async def test_compact_command_compresses_history(self):
        agent = self.make_agent(4)
        app = ChatApp(agent=agent)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            for index in range(1, 5):
                await self.submit(
                    pilot,
                    f"问题{index}",
                    until=lambda index=index: f"回答{index}" in log_text(pilot.app),
                )
            self.assertEqual(agent.context_stats.conversation_messages, 8)

            input_widget = await self.submit(
                pilot,
                "/compact 2",
                until=lambda: (
                    "上下文已压缩" in log_text(pilot.app)
                    and not pilot.app.query_one("#input", Input).disabled
                ),
            )

            text = log_text(pilot.app)
            self.assertIn("🗜️ 正在压缩上下文", text)
            self.assertIn("上下文已压缩", text)
            self.assertIn("保留最近 2 轮", text)
            self.assertIn("完整历史归档", text)
            self.assertEqual(agent.context_stats.summary_messages, 1)
            self.assertEqual(agent.context_stats.conversation_messages, 5)
            self.assertIn("摘要:测试对话内容", agent.messages[0]["content"])
            self.assertIn("摘要 1 条", self.status_text(pilot.app))
            self.assertFalse(input_widget.disabled)
            self.assertEqual(len(list(self.archive_dir.glob("*.json"))), 1)

    async def test_save_sessions_and_resume_commands(self):
        agent = self.make_agent(3, with_summary=False)
        app = ChatApp(agent=agent)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            await self.submit(
                pilot, "第一轮问题", until=lambda: "回答1" in log_text(pilot.app)
            )

            await self.submit(pilot, "/save 演示会话")
            text = log_text(pilot.app)
            self.assertIn("已保存当前会话", text)
            self.assertIn("恢复命令:/resume", text)
            self.assertEqual(len(list(self.archive_dir.glob("*.json"))), 1)
            sidebar_sessions = pilot.app.query_one("#side-sessions", Static)
            self.assertIn("最近会话(1)", str(sidebar_sessions.content))
            self.assertIn("演示会话", str(sidebar_sessions.content))

            await self.submit(pilot, "/new")
            self.assertIn("已清空对话历史", log_text(pilot.app))
            self.assertEqual(agent.context_stats.conversation_messages, 0)

            await self.submit(pilot, "/sessions")
            text = log_text(pilot.app)
            self.assertIn("已保存", text)
            self.assertIn("个会话(归档目录", text)
            self.assertIn("演示会话", text)
            self.assertIn("/resume last", text)

            await self.submit(pilot, "/resume last")
            text = log_text(pilot.app)
            self.assertIn("已恢复会话", text)
            self.assertEqual(agent.context_stats.conversation_messages, 2)
            self.assertIn("第一轮问题", log_text(pilot.app))

            await self.submit(pilot, "/status")
            text = log_text(pilot.app)
            self.assertIn("上下文:", text)
            self.assertIn("会话归档目录", text)

    async def test_compact_argument_validation(self):
        agent = self.make_agent(1)
        app = ChatApp(agent=agent)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()

            await self.submit(pilot, "/compact abc")
            self.assertIn("不是整数", log_text(pilot.app))
            await self.submit(pilot, "/compact 0")
            self.assertIn("1 到 20 之间", log_text(pilot.app))
            self.assertFalse(pilot.app.query_one("#input", Input).disabled)
            self.assertEqual(agent.context_stats.summary_messages, 0)

    async def test_compact_without_archive_reports_error(self):
        agent = self.make_agent(1, archive=False)
        app = ChatApp(agent=agent)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            await self.submit(
                pilot,
                "/compact",
                until=lambda: (
                    "未配置上下文归档目录" in log_text(pilot.app)
                    and not pilot.app.query_one("#input", Input).disabled
                ),
            )
            self.assertIn("未配置上下文归档目录", log_text(pilot.app))


if __name__ == "__main__":
    unittest.main()
