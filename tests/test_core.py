"""core 模块的单元测试:不联网、不依赖 LM Studio。

用"假 LLM"(FakeLLM)验证 Agent 的完整对话流程:
- 纯文本流式回复;
- 模型调用工具(读文件)→ 工具执行 → 模型继续回答;
- LLM 出错时产出 AgentError 并回滚历史;
- 文件工具的安全限制(路径越界被拒绝)。

运行方式(在项目根目录):
    venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Optional

from core import events
from core.agent import Agent
from core.file_ops import register_file_ops
from core.llm import LLMClient
from core.tools import Tool, ToolRegistry


class FakeLLM(LLMClient):
    """按剧本回放的假 LLM:turns 是"每个回合一个事件列表"。

    每被调用一次(stream_chat),就按顺序回放下一回合的剧本。
    这正体现了依赖注入的价值:测试时不需要真实模型。
    """

    # 类属性(满足 ABC 抽象属性要求),实例上可再覆盖具体值
    model_name: str = "fake-model"

    def __init__(self, turns: list, model_name: str = "fake-model"):
        self.turns = turns
        self.model_name = model_name
        self.calls: list[list[dict]] = []  # 记录每次调用时收到的聊天历史,供断言
        self._turn = 0

    def stream_chat(self, messages, tools: Optional[list[dict]] = None):
        self.calls.append(list(messages))
        if self._turn >= len(self.turns):
            raise RuntimeError("FakeLLM 剧本用完了(LLM 被调用的次数超过剧本回合数)")
        script = list(self.turns[self._turn])
        self._turn += 1

        async def _gen():
            for ev in script:
                yield ev

        return _gen()


class BoomLLM(LLMClient):
    """一调用就抛异常的假 LLM,用于测试错误处理。"""

    model_name = "boom-model"

    def stream_chat(self, messages, tools: Optional[list[dict]] = None):
        async def _gen():
            raise RuntimeError("连接被拒绝")
            yield  # pragma: no cover —— 让本函数成为生成器

        return _gen()


def make_agent(llm: LLMClient, work_dir: Path) -> Agent:
    """组装一个测试用 Agent(注册真实文件工具 + 假 LLM)。"""
    tools = ToolRegistry()
    register_file_ops(tools, work_dir=work_dir)
    return Agent(llm=llm, tools=tools, max_tool_rounds=3)


def collect(agent: Agent, text: str):
    """把 agent.chat() 的事件流收集成列表,方便断言。"""
    async def _run():
        return [ev async for ev in agent.chat(text)]

    import asyncio
    return asyncio.run(_run())


class TestAgentChat(unittest.TestCase):
    """Agent 对话主流程测试。"""

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp(prefix="xagent_test_"))

    def test_plain_stream_reply(self):
        """纯文本流式回复:事件顺序与聊天历史正确。"""
        llm = FakeLLM([[
            events.TextChunk("你好"),
            events.TextChunk(",世界"),
            events.StreamEnd("stop"),
        ]])
        agent = make_agent(llm, self.work_dir)

        evs = collect(agent, "你好")

        self.assertEqual(
            [type(ev) for ev in evs],
            [events.TextChunk, events.TextChunk, events.ReplyDone],
        )
        self.assertEqual(evs[-1].text, "你好,世界")
        # 历史 = user + assistant
        self.assertEqual(
            [m["role"] for m in agent.messages], ["user", "assistant"]
        )

    def test_tool_call_round_trip(self):
        """工具调用:模型要求读文件 → 工具执行 → 模型继续回答。"""
        (self.work_dir / "notes.txt").write_text("文件内容是:今晚吃火锅", encoding="utf-8")

        # 第一回合:先说话,然后要求调用 read_file;第二回合:拿到结果后给出最终回答
        llm = FakeLLM([
            [
                events.TextChunk("让我看一下笔记。"),
                events.ToolCallProposal(
                    call_id="call_1",
                    name="read_file",
                    arguments='{"path": "notes.txt"}',
                ),
                events.StreamEnd("tool_calls"),
            ],
            [
                events.TextChunk("笔记里写着:"),
                events.TextChunk("今晚吃火锅"),
                events.StreamEnd("stop"),
            ],
        ])
        agent = make_agent(llm, self.work_dir)

        evs = collect(agent, "笔记里有什么?")

        kinds = [type(ev) for ev in evs]
        self.assertIn(events.ToolCallStarted, kinds)
        self.assertIn(events.ToolCallFinished, kinds)
        # 工具调用参数正确解析
        started = next(ev for ev in evs if isinstance(ev, events.ToolCallStarted))
        self.assertEqual(started.name, "read_file")
        self.assertEqual(started.arguments, {"path": "notes.txt"})
        # 工具执行成功
        finished = next(ev for ev in evs if isinstance(ev, events.ToolCallFinished))
        self.assertTrue(finished.ok)
        self.assertIn("今晚吃火锅", finished.result)
        # 最终回复正确,历史完整:user / assistant(tool_calls) / tool / assistant
        self.assertEqual(evs[-1].text, "笔记里写着:今晚吃火锅")
        self.assertEqual(
            [m["role"] for m in agent.messages],
            ["user", "assistant", "tool", "assistant"],
        )
        # 第二回合 LLM 收到的历史里,包含工具执行结果(tool 消息)
        second_call_roles = [m["role"] for m in llm.calls[1]]
        self.assertIn("tool", second_call_roles)

    def test_llm_error_rolls_back_history(self):
        """LLM 出错:产出 AgentError,且历史回滚到本轮开始前。"""
        agent = make_agent(BoomLLM(), self.work_dir)

        evs = collect(agent, "你好")

        self.assertEqual(len(evs), 1)
        self.assertIsInstance(evs[0], events.AgentError)
        self.assertIn("连接被拒绝", evs[0].message)
        self.assertEqual(agent.messages, [])  # 已回滚,没有残留半截消息

    def test_reset_clears_history(self):
        """reset 清空聊天历史。"""
        llm = FakeLLM([[events.TextChunk("ok"), events.StreamEnd("stop")]])
        agent = make_agent(llm, self.work_dir)
        collect(agent, "第一句")
        self.assertEqual(len(agent.messages), 2)

        agent.reset()
        self.assertEqual(agent.messages, [])


class TestFileOpsTools(unittest.TestCase):
    """文件工具测试(含安全限制)。"""

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp(prefix="xagent_fileops_"))
        self.tools = ToolRegistry()
        register_file_ops(self.tools, work_dir=self.work_dir)

    def execute(self, name: str, arguments: str) -> str:
        import asyncio
        return asyncio.run(self.tools.execute(name, arguments))

    def test_write_then_read_round_trip(self):
        """写文件 → 读文件,内容一致。"""
        result = self.execute(
            "write_file", '{"path": "a.txt", "content": "你好,世界"}'
        )
        self.assertTrue(result.startswith("成功"), result)

        result = self.execute("read_file", '{"path": "a.txt"}')
        self.assertEqual(result, "你好,世界")

        self.assertTrue((self.work_dir / "a.txt").is_file())

    def test_list_dir(self):
        """列目录:能看到刚写的文件,并标出类型。"""
        (self.work_dir / "b.txt").write_text("x", encoding="utf-8")
        (self.work_dir / "sub").mkdir()

        result = self.execute("list_dir", "{}")
        self.assertIn("[文件] b.txt", result)
        self.assertIn("[目录] sub", result)

    def test_path_escape_is_rejected(self):
        """越界路径(../ 逃出工作目录)必须被拒绝。"""
        outside = self.work_dir.parent / "secret.txt"
        outside.write_text("机密", encoding="utf-8")

        result = self.execute("read_file", '{"path": "../secret.txt"}')
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("越界", result)

    def test_absolute_path_outside_is_rejected(self):
        """绝对路径指向工作目录外:拒绝;指向工作目录内:放行。"""
        outside = self.work_dir.parent / "secret.txt"
        outside.write_text("机密", encoding="utf-8")

        result = self.execute("read_file", f'{{"path": "{outside.as_posix()}"}}')
        self.assertTrue(result.startswith("错误"), result)

        inside = self.work_dir / "ok.txt"
        inside.write_text("可以读", encoding="utf-8")
        result = self.execute("read_file", f'{{"path": "{inside.as_posix()}"}}')
        self.assertEqual(result, "可以读")

    def test_missing_file_returns_friendly_error(self):
        """文件不存在:返回可读的错误文本而不是抛异常。"""
        result = self.execute("read_file", '{"path": "没有这个文件.txt"}')
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("不存在", result)


class TestToolRegistry(unittest.TestCase):
    """工具注册中心本身的错误处理。"""

    def test_unknown_tool(self):
        import asyncio
        tools = ToolRegistry()
        result = asyncio.run(tools.execute("不存在", "{}"))
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("不存在", result)

    def test_bad_arguments_json(self):
        import asyncio
        tools = ToolRegistry()
        tools.register(Tool(
            name="dummy", description="测试用", parameters={},
            handler=lambda **kwargs: "ok",
        ))
        result = asyncio.run(tools.execute("dummy", "这不是JSON"))
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("JSON", result)


if __name__ == "__main__":
    unittest.main()
