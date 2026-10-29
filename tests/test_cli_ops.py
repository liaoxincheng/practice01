"""命令行工具(RunCli)的单元测试:不联网、不需要 LM Studio。

覆盖:正常执行、非零退出码、stderr 捕获、超时终止、输出截断、
shell 注入防护(shell=False 列表形式调用)、参数校验、schema 生成、
以及与 Agent 的工具调用集成。

运行方式(在项目根目录):
    venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
import unittest
from pathlib import Path

from core import events
from core.agent import Agent
from core.cli_ops import register_cli_ops
from core.tools import Tool, ToolRegistry
from tests.test_core import FakeLLM


def call_args(command: str, args: list) -> str:
    """构造 RunCli 的 arguments JSON 字符串。

    统一用 json.dumps 转义,避免手工拼 JSON 时
    路径反斜杠、引号等转义出错(这正是模型发来的真实格式)。
    """
    return json.dumps({"command": command, "args": args}, ensure_ascii=False)


def make_registry(timeout_seconds=10.0, max_output_chars=4000) -> ToolRegistry:
    tools = ToolRegistry()
    register_cli_ops(
        tools,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
    )
    return tools


def execute(tools: ToolRegistry, name: str, arguments: str) -> str:
    return asyncio.run(tools.execute(name, arguments))


class TestRunCliTool(unittest.TestCase):
    """RunCli 工具本身的行为测试。"""

    def setUp(self):
        self.tools = make_registry()

    def test_success_returns_stdout_and_exitcode(self):
        """正常执行:exitcode=0,stdout 有内容,stderr 为空。"""
        result = execute(
            self.tools, "RunCli",
            call_args(sys.executable, ["-c", "print(12345)"]),
        )
        self.assertIn("exitcode = 0", result)
        self.assertIn("12345", result)
        self.assertIn("[stderr]", result)
        self.assertIn("(空)", result)

    def test_nonzero_exitcode(self):
        """程序返回非零退出码:结果里能看到 exitcode。"""
        result = execute(
            self.tools, "RunCli",
            call_args(sys.executable, ["-c", "import sys; sys.exit(3)"]),
        )
        self.assertIn("exitcode = 3", result)

    def test_stderr_is_captured(self):
        """程序写标准错误:stderr 内容被捕获返回。"""
        result = execute(
            self.tools, "RunCli",
            call_args(
                sys.executable,
                ["-c", "import sys; print('错误信息', file=sys.stderr)"],
            ),
        )
        self.assertIn("错误信息", result)

    def test_timeout_kills_command(self):
        """超过超时时间:强制终止并给出明确提示。"""
        tools = make_registry(timeout_seconds=1.0)
        result = execute(
            tools, "RunCli",
            call_args(sys.executable, ["-c", "import time; time.sleep(30)"]),
        )
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("超过 1.0 秒", result)

    def test_output_truncation_is_marked(self):
        """输出过长:被截断,且结果中明确标注。"""
        tools = make_registry(max_output_chars=50)
        result = execute(
            tools, "RunCli",
            call_args(sys.executable, ["-c", "print('x' * 500)"]),
        )
        # 只保留前 50 个字符,且截断标注里写明原文总字符数
        self.assertIn("x" * 50 + "\n...[输出过长,已截断", result)
        self.assertRegex(result, r"原文 \d+ 字符")

    def test_shell_injection_is_neutralized(self):
        """注入防护:参数里的 shell 元字符被当作普通参数,不产生 shell 行为。"""
        tools = make_registry()
        # 恶意参数:试图通过 && 追加执行写文件命令
        evil = "hello && echo pwned > pwned_test.txt"
        result = execute(
            tools, "RunCli",
            call_args(
                sys.executable,
                ["-c", "import sys; print(sys.argv[1])", evil],
            ),
        )
        # 原样回显:证明参数没有被 shell 解析
        self.assertIn(evil, result)
        # 追加的"写文件命令"没有生效
        self.assertFalse(
            (Path.cwd() / "pwned_test.txt").exists(),
            "注入命令被执行了!文件不该被创建",
        )

    def test_numbers_in_args_are_coerced(self):
        """模型偶发传数字参数:自动转成字符串正常执行。"""
        result = execute(
            self.tools, "RunCli",
            call_args(sys.executable, ["-c", "import sys; print(sys.argv[1])", 42]),
        )
        self.assertIn("exitcode = 0", result)
        self.assertIn("42", result)

    def test_command_not_found(self):
        """程序不存在:返回友好错误,不抛异常。"""
        result = execute(
            self.tools, "RunCli",
            '{"command": "不存在的程序xyz123", "args": []}',
        )
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("找不到程序", result)

    def test_bad_arguments_are_rejected(self):
        """参数校验:command 非字符串、args 非列表都给出友好错误。"""
        result = execute(self.tools, "RunCli", '{"command": "", "args": []}')
        self.assertTrue(result.startswith("错误"), result)

        result = execute(self.tools, "RunCli", '{"command": "ping", "args": "-n"}')
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("列表", result)

    def test_schema_is_registered(self):
        """function calling 的 schema 正确:名字、入参、必填项。"""
        schemas = self.tools.openai_schemas()
        runcli = next(
            s for s in schemas if s["function"]["name"] == "RunCli"
        )
        params = runcli["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertEqual(params["required"], ["command"])
        self.assertEqual(set(params["properties"]), {"command", "args"})
        self.assertEqual(params["properties"]["args"]["items"]["type"], "string")


class TestRunCliWithAgent(unittest.TestCase):
    """RunCli 与 Agent 编排的集成测试(假 LLM)。"""

    def test_agent_executes_runcli_round_trip(self):
        """模型提议调用 RunCli → Agent 执行 → 结果送回模型。"""
        tools = make_registry()
        agent = Agent(
            llm=FakeLLM([
                [
                    events.ToolCallProposal(
                        call_id="call_1",
                        name="RunCli",
                        arguments=call_args(
                            sys.executable, ["-c", "print(999)"]
                        ),
                    ),
                    events.StreamEnd("tool_calls"),
                ],
                [
                    events.TextChunk("命令执行成功。"),
                    events.StreamEnd("stop"),
                ],
            ]),
            tools=tools,
        )

        evs = asyncio.run(_collect(agent, "帮我执行一条命令"))

        finished = next(
            ev for ev in evs if isinstance(ev, events.ToolCallFinished)
        )
        self.assertTrue(finished.ok, finished.result)
        self.assertIn("exitcode = 0", finished.result)
        self.assertIn("999", finished.result)
        # 历史完整:user / assistant(tool_calls) / tool / assistant
        self.assertEqual(
            [m["role"] for m in agent.messages],
            ["user", "assistant", "tool", "assistant"],
        )


async def _collect(agent: Agent, text: str):
    return [ev async for ev in agent.chat(text)]


class TestSyncHandlerNonBlocking(unittest.IsolatedAsyncioTestCase):
    """同步 handler 必须在线程池执行,不能阻塞事件循环。

    否则 RunCli 一条最长 30 秒的命令,会把整个 TUI 界面卡死。
    """

    async def test_loop_stays_responsive_during_slow_handler(self):
        tools = ToolRegistry()

        def slow_handler(**kwargs):
            time.sleep(1.0)  # 模拟 RunCli 的长命令
            return "done"

        tools.register(Tool(
            name="slow", description="慢工具", parameters={},
            handler=slow_handler,
        ))

        # 后台心跳任务:事件循环每 0.1 秒跳一下,记录跳了多少次
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.1)
                ticks += 1

        task = asyncio.create_task(ticker())
        try:
            start = time.monotonic()
            result = await tools.execute("slow", "{}")
            elapsed = time.monotonic() - start
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # 工具确实执行了整整 1 秒(线程里在睡)……
        self.assertEqual(result, "done")
        self.assertGreaterEqual(elapsed, 0.9)
        # ……但事件循环没有被卡住,心跳一直在跳
        self.assertGreaterEqual(
            ticks, 8,
            f"事件循环被阻塞了(1 秒内只跳了 {ticks} 次):同步 handler 没走线程池",
        )


if __name__ == "__main__":
    unittest.main()
