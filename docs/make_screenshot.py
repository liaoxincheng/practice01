r"""生成界面截图(Textual 无头模式,不需要 LM Studio)。

用假 LLM 演示一次完整对话:模型先调用 write_file 写文件,
再调用 read_file 读回来,最后给出回答 —— 正好展示 TUI 的全部界面元素。

用法(在项目根目录):
    venv\Scripts\python docs\make_screenshot.py
输出:
    docs\xAgent界面截图.svg(可用浏览器打开,或转成图片放进作业报告)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 让脚本能 import 到项目根目录下的 core / tui / tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from textual.widgets import Input  # noqa: E402

from core import events  # noqa: E402
from core.agent import Agent  # noqa: E402
from core.file_ops import register_file_ops  # noqa: E402
from core.tools import ToolRegistry  # noqa: E402
from tests.test_core import FakeLLM  # noqa: E402
from tui.app import ChatApp  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main() -> None:
    # 注册真实的文件工具(与 main.py 相同的组装方式),让演示里工具真的执行成功
    tools = ToolRegistry()
    register_file_ops(tools, work_dir=PROJECT_ROOT / "workspace")

    # 三回合剧本:写文件 → 读文件 → 总结回答
    agent = Agent(
        llm=FakeLLM(
            [
                [
                    events.TextChunk("好的,我先把这句话写入文件。"),
                    events.ToolCallProposal(
                        call_id="call_1",
                        name="write_file",
                        arguments='{"path": "hello.txt", "content": "你好,世界"}',
                    ),
                    events.StreamEnd("tool_calls"),
                ],
                [
                    events.TextChunk("写入成功,再读出来给你听。"),
                    events.ToolCallProposal(
                        call_id="call_2",
                        name="read_file",
                        arguments='{"path": "hello.txt"}',
                    ),
                    events.StreamEnd("tool_calls"),
                ],
                [
                    events.TextChunk("文件内容为:你好,世界"),
                    events.StreamEnd("stop"),
                ],
            ],
            model_name="qwen3.5-4b",  # 与真实 config.ini 保持一致
        ),
        tools=tools,
    )

    app = ChatApp(agent=agent)
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        # 模拟用户输入并回车
        input_widget = pilot.app.query_one("#input", Input)
        input_widget.value = "帮我把\"你好,世界\"写入 hello.txt,再读出来念给我听"
        await pilot.press("enter")
        # 等待后台对话任务跑完
        for _ in range(80):
            await pilot.pause(0.05)
            if not input_widget.disabled:
                break
        await pilot.pause(0.2)

        output = Path(__file__).resolve().parent / "xAgent界面截图.svg"
        app.save_screenshot(str(output))
        print(f"截图已保存:{output}")


if __name__ == "__main__":
    asyncio.run(main())
