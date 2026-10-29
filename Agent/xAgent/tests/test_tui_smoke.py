"""TUI 冒烟测试:用 Textual 的 headless(无头)模式验证界面能正常启动和交互。

不需要真实终端、不需要 LM Studio:
- 界面能挂载、输入框存在;
- 输入消息回车后,core 的事件流被正确渲染到聊天记录里;
- exit 命令能退出程序。

运行方式(在项目根目录):
    venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from typing import Optional

from textual.widgets import Input, RichLog

from core import events
from core.agent import Agent
from core.llm import LLMClient
from core.tools import ToolRegistry
from tui.app import ChatApp


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


if __name__ == "__main__":
    unittest.main()
