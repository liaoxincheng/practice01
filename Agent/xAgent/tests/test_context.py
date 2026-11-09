"""上下文压缩、会话归档与 recallContext 的测试。

覆盖:
- Token 粗估与消息文本提取;
- ArchiveStore 的保存、列出、读取、检索和异常文件处理;
- Agent.compact 的归档、模型摘要、本地摘要兜底和最近轮数保留;
- Agent.save_session / resume_session / list_sessions;
- recallContext 工具;
- [context] 配置的默认值与相对路径解析。

运行方式(在项目根目录):
    venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core import events
from core.agent import Agent
from core.config import ensure_config_file, load_config
from core.context import (
    ArchiveStore,
    estimate_messages_tokens,
    estimate_tokens,
    message_text,
)
from core.context_ops import register_context_ops
from core.tools import ToolRegistry
from tests.test_core import FakeLLM


def make_chat_turns(count: int) -> list[list]:
    """生成 count 轮普通对话的假 LLM 剧本。"""
    return [
        [events.TextChunk(f"回答{index}"), events.StreamEnd("stop")]
        for index in range(1, count + 1)
    ]


async def drain(agent: Agent, text: str) -> str:
    """跑完一轮对话,返回模型文本。"""
    parts = []
    async for event in agent.chat(text):
        if isinstance(event, events.TextChunk):
            parts.append(event.text)
    return "".join(parts)


class TestTokenEstimate(unittest.TestCase):
    """Token 粗估函数。"""

    def test_ascii_and_chinese(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("中文"), 2)
        self.assertGreater(estimate_tokens("中文"), estimate_tokens("abcd"))

    def test_message_text_includes_tool_calls(self):
        message = {
            "role": "assistant",
            "content": "我先查一下",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "a.txt"}',
                    },
                }
            ],
        }
        text = message_text(message)
        self.assertIn("我先查一下", text)
        self.assertIn("read_file", text)
        self.assertIn("a.txt", text)
        self.assertEqual(message_text("不是字典"), "")

    def test_messages_estimate_has_per_message_overhead(self):
        messages = [
            {"role": "user", "content": "abcd"},
            {"role": "assistant", "content": "abcd"},
        ]
        self.assertEqual(estimate_messages_tokens(messages), 10)


class TestArchiveStore(unittest.TestCase):
    """会话归档文件的读写与检索。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = ArchiveStore(self.root / "sessions")

    def test_save_list_load_and_search(self):
        messages = [
            {"role": "user", "content": "记住:项目代号蓝鲸,配置文件在 C:/tmp/x.ini"},
            {"role": "assistant", "content": "好的,已经记住蓝鲸项目。"},
        ]
        path = self.store.save(
            messages, name="我的 会话/1", summary="讨论蓝鲸项目的配置路径"
        )

        self.assertTrue(path.is_file())
        self.assertTrue(path.name.endswith("-我的-会话-1.json"))
        self.assertIn("蓝鲸", path.read_text(encoding="utf-8"))

        sessions = self.store.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].message_count, 2)
        self.assertIn("蓝鲸", sessions[0].summary_preview)

        record, loaded = self.store.load("last")
        self.assertEqual(loaded, messages)
        self.assertEqual(record["name"], path.name)

        # 文件名 / 文件名片段 / 名字片段都能定位到同一个会话
        for ref in (path.name, path.stem, "我的-会话"):
            _, again = self.store.load(ref)
            self.assertEqual(again, messages)

        hits = self.store.search("蓝鲸", limit=5)
        self.assertTrue(hits)
        self.assertTrue(any(hit.role == "user" for hit in hits))
        self.assertTrue(any("蓝鲸" in hit.snippet for hit in hits))
        self.assertTrue(any(hit.role == "摘要" for hit in hits))

    def test_invalid_files_are_ignored(self):
        self.store.directory.mkdir(parents=True, exist_ok=True)
        (self.store.directory / "broken.json").write_text(
            "不是 JSON", encoding="utf-8"
        )
        self.store.save([{"role": "user", "content": "正常会话"}], name="ok")
        sessions = self.store.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertIn("ok", sessions[0].name)

    def test_ambiguous_name_is_reported(self):
        self.store.save([{"role": "user", "content": "第一次"}], name="dup")
        self.store.save([{"role": "user", "content": "第二次"}], name="dup")
        with self.assertRaises(ValueError) as raised:
            self.store.load("dup")
        self.assertIn("多个", str(raised.exception))

    def test_missing_name_and_empty_save(self):
        with self.assertRaises(ValueError):
            self.store.load("不存在")
        with self.assertRaises(ValueError):
            self.store.save([])
        with self.assertRaises(ValueError):
            self.store.search("   ")

    def test_load_skips_invalid_messages(self):
        path = self.store.save([{"role": "user", "content": "合法消息"}], name="mixed")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["messages"].append({"role": "unknown", "content": "非法角色"})
        path.write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        _, loaded = self.store.load("mixed")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["content"], "合法消息")


class TestCompact(unittest.IsolatedAsyncioTestCase):
    """Agent.compact 的行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.archive_dir = self.root / "sessions"

    def make_agent(self, turns: list[list], **kwargs) -> tuple[Agent, FakeLLM]:
        llm = FakeLLM(turns)
        options = dict(
            llm=llm,
            tools=ToolRegistry(),
            system_prompt="系统规则:用中文回答",
            agents_md="# 项目约定\n只改工作区。",
            archive_dir=self.archive_dir,
            keep_recent_turns=2,
            max_context_tokens=1000,
        )
        options.update(kwargs)
        return Agent(**options), llm

    async def test_compact_archives_then_keeps_recent_turns(self):
        turns = make_chat_turns(4)
        turns.append([events.TextChunk("摘要:用户测试了四轮对话。"), events.StreamEnd("stop")])
        agent, llm = self.make_agent(turns)

        for index in range(1, 5):
            await drain(agent, f"问题{index}")
        before = agent.context_stats
        self.assertEqual(before.conversation_messages, 8)

        result = await agent.compact()

        self.assertIn("上下文已压缩", result)
        self.assertIn("模型摘要", result)
        self.assertIn("压缩前", result)
        self.assertIn("压缩后", result)
        self.assertIn("完整历史归档", result)
        self.assertIn("问题1", llm.calls[-1][-1]["content"])  # 摘要请求包含旧历史
        self.assertEqual(llm.calls[-1][0]["role"], "system")

        # 系统提示词 + AGENTS.md + 历史摘要 + 最近两轮
        roles = [message["role"] for message in agent.messages]
        self.assertEqual(
            roles,
            ["system", "system", "system", "user", "assistant", "user", "assistant"],
        )
        self.assertIn("系统规则", agent.messages[0]["content"])
        self.assertIn("# 项目约定", agent.messages[1]["content"])
        self.assertIn("摘要:用户测试了四轮对话", agent.messages[2]["content"])
        self.assertIn("问题3", agent.messages[3]["content"])
        self.assertIn("问题4", agent.messages[5]["content"])
        self.assertNotIn("问题1", "".join(
            str(message.get("content")) for message in agent.messages
        ))

        stats = agent.context_stats
        self.assertEqual(stats.summary_messages, 1)
        self.assertEqual(stats.conversation_messages, 5)
        self.assertEqual(agent.archive_dir, self.archive_dir.resolve())

        sessions = agent.list_sessions()
        self.assertEqual(len(sessions), 1)
        _, archived = ArchiveStore(self.archive_dir).load("last")
        self.assertEqual(len(archived), 8)
        self.assertTrue(any(message.get("content") == "问题1" for message in archived))

    async def test_model_failure_falls_back_to_local_summary(self):
        # 没有给摘要回合:第 3 次 stream_chat 会抛错,触发本地摘要兜底
        agent, _ = self.make_agent(make_chat_turns(2))
        await drain(agent, "第一轮问题")
        await drain(agent, "第二轮问题")

        result = await agent.compact(keep_recent_turns=1)

        self.assertIn("上下文已压缩", result)
        self.assertIn("本地摘要", result)
        self.assertIn("模型摘要失败", result)
        summary_message = agent.messages[2]["content"]
        self.assertIn("本地生成", summary_message)
        self.assertIn("第一轮问题", summary_message)
        # 最近一轮完整保留
        self.assertEqual(agent.messages[-2]["content"], "第二轮问题")
        self.assertEqual(agent.messages[-1]["content"], "回答2")

    async def test_short_history_is_not_compacted(self):
        agent, _ = self.make_agent(make_chat_turns(1) + make_chat_turns(1))
        await drain(agent, "只有一个问题")

        result = await agent.compact(keep_recent_turns=3)

        self.assertIn("还不需要压缩", result)
        self.assertEqual(agent.context_stats.conversation_messages, 2)
        self.assertEqual(agent.list_sessions(), [])

    async def test_empty_summary_falls_back(self):
        turns = make_chat_turns(2) + [[events.StreamEnd("stop")]]
        agent, _ = self.make_agent(turns)
        await drain(agent, "第一轮问题")
        await drain(agent, "第二轮问题")

        result = await agent.compact(keep_recent_turns=1)

        self.assertIn("本地摘要", result)
        self.assertIn("模型返回空摘要", result)

    async def test_compact_without_archive_returns_error(self):
        agent, _ = self.make_agent(
            make_chat_turns(2) + [[events.TextChunk("摘要"), events.StreamEnd("stop")]],
            archive_dir=None,
        )
        await drain(agent, "第一轮")
        await drain(agent, "第二轮")

        result = await agent.compact(keep_recent_turns=1)

        self.assertIn("未配置上下文归档目录", result)
        self.assertEqual(agent.context_stats.conversation_messages, 4)


class TestSessionSaveResume(unittest.IsolatedAsyncioTestCase):
    """会话保存、恢复与列表。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.archive_dir = self.root / "sessions"

    def make_agent(self, turns: list[list]) -> Agent:
        return Agent(
            llm=FakeLLM(turns),
            tools=ToolRegistry(),
            system_prompt="系统规则",
            archive_dir=self.archive_dir,
        )

    async def test_save_reset_and_resume(self):
        agent = self.make_agent(make_chat_turns(3))
        await drain(agent, "第一轮问题")
        await drain(agent, "第二轮问题")

        saved = agent.save_session("我的会话")
        self.assertIn("已保存当前会话", saved)
        self.assertIn("恢复命令", saved)
        self.assertEqual(len(agent.list_sessions()), 1)

        agent.reset()
        self.assertEqual(agent.context_stats.conversation_messages, 0)

        restored = agent.resume_session("last")
        self.assertIn("已恢复会话", restored)
        self.assertEqual(agent.context_stats.conversation_messages, 4)
        self.assertEqual(agent.messages[0]["content"], "系统规则")
        self.assertEqual(agent.messages[1]["content"], "第一轮问题")

    async def test_resume_archives_current_conversation_first(self):
        agent = self.make_agent(make_chat_turns(3))
        await drain(agent, "旧会话第一轮")
        await drain(agent, "旧会话第二轮")
        agent.save_session("旧会话")

        await drain(agent, "新会话第一轮")
        result = agent.resume_session("last")

        self.assertIn("已恢复会话", result)
        self.assertIn("自动归档", result)
        self.assertEqual(len(agent.list_sessions()), 2)
        self.assertEqual(agent.context_stats.conversation_messages, 4)

    async def test_save_empty_and_resume_missing(self):
        agent = self.make_agent(make_chat_turns(1))

        self.assertIn("为空", agent.save_session())
        self.assertIn("找不到会话归档", agent.resume_session("不存在"))

    async def test_without_archive_returns_error(self):
        agent = Agent(llm=FakeLLM([]), tools=ToolRegistry())

        self.assertIn("未配置上下文归档目录", agent.save_session())
        self.assertIn("未配置上下文归档目录", agent.resume_session("last"))
        self.assertEqual(agent.list_sessions(), [])
        self.assertIsNone(agent.archive_dir)


class TestRecallContext(unittest.IsolatedAsyncioTestCase):
    """recallContext 工具。"""

    async def test_searches_archived_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArchiveStore(tmp)
            store.save(
                [
                    {
                        "role": "user",
                        "content": "项目代号蓝鲸,配置文件在 C:/tmp/x.ini",
                    },
                    {"role": "assistant", "content": "已记录蓝鲸项目。"},
                ],
                name="blue-whale",
                summary="蓝鲸项目的配置",
            )
            tools = ToolRegistry()
            register_context_ops(tools, tmp)

            result = await tools.execute(
                "recallContext",
                json.dumps({"query": "蓝鲸", "limit": 5}, ensure_ascii=False),
            )

            self.assertIn("找到", result)
            self.assertIn("blue-whale", result)
            self.assertIn("C:/tmp/x.ini", result)
            self.assertIn("归档", result)

    async def test_empty_query_and_no_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArchiveStore(tmp)
            store.save([{"role": "user", "content": "只讨论天气"}], name="weather")
            tools = ToolRegistry()
            register_context_ops(tools, tmp)

            empty = await tools.execute("recallContext", '{"query": "   "}')
            missing = await tools.execute("recallContext", '{"query": "量子力学"}')

            self.assertIn("不能为空", empty)
            self.assertIn("没有在归档会话中找到", missing)
            self.assertIn(str(Path(tmp).resolve()), missing)


class TestContextConfig(unittest.TestCase):
    """[context] 配置解析。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write_config(self, body: str) -> Path:
        path = self.root / "config.ini"
        path.write_text("[llm]\nbase_url = http://127.0.0.1:1/v1\n" + body, encoding="utf-8")
        return path

    def test_defaults_and_relative_archive_dir(self):
        config = load_config(self.write_config(""))
        self.assertEqual(
            config.context.archive_dir, (self.root / ".xagent/sessions").resolve()
        )
        self.assertEqual(config.context.keep_recent_turns, 3)
        self.assertEqual(config.context.max_tokens, 12000)
        self.assertEqual(config.context.summary_max_chars, 4000)
        self.assertEqual(config.file_ops.max_read_chars, 20_000)
        self.assertEqual(config.file_ops.max_list_entries, 200)
        self.assertEqual(config.external_ops.max_file_bytes, 200_000)
        self.assertEqual(config.external_ops.max_output_chars, 50_000)

    def test_values_are_clamped(self):
        config = load_config(
            self.write_config(
                "[context]\n"
                "archive_dir = archives\n"
                "keep_recent_turns = 99\n"
                "max_tokens = 10\n"
                "summary_max_chars = 99999\n"
                "[file_ops]\n"
                "max_read_chars = 1\n"
                "max_list_entries = 0\n"
                "[external_ops]\n"
                "max_file_bytes = 1\n"
                "max_output_chars = 1\n"
            )
        )
        self.assertEqual(config.context.archive_dir, (self.root / "archives").resolve())
        self.assertEqual(config.context.keep_recent_turns, 20)
        self.assertEqual(config.context.max_tokens, 1000)
        self.assertEqual(config.context.summary_max_chars, 20_000)
        self.assertEqual(config.file_ops.max_read_chars, 100)
        self.assertEqual(config.file_ops.max_list_entries, 1)
        self.assertEqual(config.external_ops.max_file_bytes, 1000)
        self.assertEqual(config.external_ops.max_output_chars, 100)

    def test_ensure_config_copies_example_but_clears_mcp(self):
        example = self.root / "config.example.ini"
        example.write_text(
            "; 示例配置\n"
            "[llm]\n"
            "base_url = http://example/v1\n"
            "model = demo\n"
            "api_key = DUMMY\n"
            "[mcp]\n"
            "; 示例服务器\n"
            "servers = pet=http://127.0.0.1:8081/mcp\n"
            "timeout_seconds = 30\n",
            encoding="utf-8",
        )
        target = self.root / "config.ini"

        self.assertTrue(ensure_config_file(target, example))
        text = target.read_text(encoding="utf-8")
        self.assertIn("; 示例配置", text)
        self.assertIn("base_url = http://example/v1", text)
        self.assertIn("servers =", text)
        self.assertNotIn("pet=", text)

        # 已存在时不覆盖用户配置
        target.write_text("[llm]\napi_key = 用户的密钥\n", encoding="utf-8")
        self.assertFalse(ensure_config_file(target, example))
        self.assertIn("用户的密钥", target.read_text(encoding="utf-8"))

    def test_ensure_config_falls_back_to_minimal_config(self):
        target = self.root / "config.ini"
        self.assertTrue(ensure_config_file(target, self.root / "missing.ini"))
        config = load_config(target)
        self.assertEqual(config.llm.model, "qwen3.5-4b")
        self.assertEqual(config.mcp_ops.servers, ())


if __name__ == "__main__":
    unittest.main()
