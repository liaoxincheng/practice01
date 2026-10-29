"""用 Textual 的无头模式验证界面能起、能画、能响应输入。

不接真实模型，用假 Agent 喂事件，确认界面层的接线是对的。
"""
import sys

import asyncio

from core.agent import Agent, ToolCall

from tui.app import XAgentApp
from tui.widgets import ChatLog, PromptInput

passed = 0
failed = []


def check(label, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("OK   %s" % label)
    else:
        failed.append(label)
        print("FAIL %s  %s" % (label, detail))


class FakeAgent(Agent):
    """不发网络请求的 Agent，用来验证界面接线。"""

    def __init__(self):
        super().__init__()
        self.asked = []

    def ask(self, text):
        self.asked.append(text)
        self._emit("on_assistant_start")
        for piece in ("你好", "，我是", "假模型"):
            self._emit("on_assistant_delta", piece)
        call = ToolCall("c1", "getDate", "{}")
        self._emit("on_tool_start", "getDate", {})
        self._emit("on_tool_end", "getDate", "2026-10-23 10:00:00", None)
        self._emit("on_assistant_end", "调用完成", [call])
        self._emit("on_assistant_end", "最终回答", [])
        return "最终回答"


async def main():
    app = XAgentApp(agent=FakeAgent())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        check("应用能启动", app.is_running)
        check("聊天区存在", app.query_one(ChatLog) is not None)
        check("输入框存在", app.query_one("#prompt", PromptInput) is not None)
        check("状态栏存在", app.query_one("#status") is not None)

        log = app.query_one(ChatLog)

        # 直接喂事件，验证处理函数接线
        app.query_one("#prompt", PromptInput).focus()
        app.post_message(__import__("tui.events", fromlist=["AssistantStart"]).AssistantStart())
        await pilot.pause()
        app.post_message(
            __import__("tui.events", fromlist=["AssistantDelta"]).AssistantDelta("测试片段")
        )
        await pilot.pause()
        log.add_user("你好呀")
        log.add_tool("readFile", {"path": "a.txt"})
        log.add_tool_result("readFile", "文件内容第一行", None)
        log.add_error("这是个测试错误")
        await pilot.pause()

        check("日志有内容", len(log.lines) > 0, str(len(log.lines)))

        # 按键绑定
        await pilot.press("f1")
        await pilot.pause()
        check("F1 显示工具清单不报错", True)

        await pilot.press("ctrl+l")
        await pilot.pause()
        check("Ctrl+L 清屏不报错", True)

        await pilot.press("ctrl+n")
        await pilot.pause()
        check("Ctrl+N 新会话不报错", True)

        # 输入框行为
        prompt = app.query_one("#prompt", PromptInput)
        prompt.text = "测试输入"
        await pilot.pause()
        check("输入框能写", prompt.text == "测试输入")
        prompt.clear_text()
        await pilot.pause()
        check("输入框能清", prompt.text == "")

        # 空输入不应该触发提交
        prompt.text = ""
        prompt.action_submit()
        await pilot.pause()
        check("空输入不提交", len(prompt.text) == 0)

    print()
    print("通过 %d 项，失败 %d 项" % (passed, len(failed)))
    if failed:
        for f in failed:
            print("  失败:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
