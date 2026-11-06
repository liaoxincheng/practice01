"""Agent Skills 自动发现、loadSkill 和系统提示词的测试。

覆盖:多根目录扫描、Frontmatter 解析、同名技能去重、按需加载 SKILL.md
和附件、越界路径拒绝、同名附件候选列表、系统提示词注入、会话重置,
以及用户取消对话时 Agent 回滚聊天历史。
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core import events
from core.agent import Agent
from core.llm import LLMClient
from core.prompts import build_system_prompt
from core.skill_ops import register_skill_ops
from core.skills_catalog import SkillsCatalog
from core.tools import ToolRegistry
from tests.test_core import FakeLLM


def write_skill(root: Path, name: str, description: str = "示例技能",
                body: str = "1. 按步骤执行。") -> Path:
    directory = root / ".agents" / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def execute(tools: ToolRegistry, name: str, arguments: dict) -> str:
    return asyncio.run(tools.execute(name, json.dumps(arguments, ensure_ascii=False)))


class TestSkillsCatalog(unittest.TestCase):
    """技能目录扫描与 Frontmatter 解析。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_discovers_skill_and_reads_metadata(self):
        write_skill(self.root, "web-search", "网页搜索与总结")
        catalog = SkillsCatalog.discover([self.root])

        self.assertEqual(len(catalog), 1)
        skill = catalog.skills[0]
        self.assertEqual(skill.name, "web-search")
        self.assertEqual(skill.description, "网页搜索与总结")
        self.assertEqual(skill.directory.name, "web-search")
        self.assertTrue(skill.skill_file.is_file())

    def test_multiline_block_description(self):
        directory = self.root / ".agents" / "skills" / "report"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\n"
            "name: report\n"
            "description: |\n"
            "  生成周报\n"
            "  并检查格式\n"
            "---\n"
            "正文\n",
            encoding="utf-8",
        )
        catalog = SkillsCatalog.discover([self.root])
        self.assertEqual(catalog.skills[0].description, "生成周报\n并检查格式")

    def test_quoted_description(self):
        directory = self.root / ".agents" / "skills" / "quoted"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            '---\nname: quoted\ndescription: "含冒号: 的说明"\n---\n正文\n',
            encoding="utf-8",
        )
        catalog = SkillsCatalog.discover([self.root])
        self.assertEqual(catalog.skills[0].description, "含冒号: 的说明")

    def test_invalid_skill_is_reported_not_crashing(self):
        directory = self.root / ".agents" / "skills" / "broken"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: broken\n---\n正文\n", encoding="utf-8"
        )
        catalog = SkillsCatalog.discover([self.root])
        self.assertEqual(catalog.skills, ())
        self.assertTrue(any("description" in issue for issue in catalog.issues))

    def test_same_name_in_later_root_is_skipped(self):
        other = Path(self._tmp.name) / "other"
        write_skill(self.root, "demo", "第一个")
        write_skill(other, "demo", "第二个")

        catalog = SkillsCatalog.discover([self.root, other])
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog.skills[0].description, "第一个")
        self.assertTrue(any("名称重复" in issue for issue in catalog.issues))

    def test_custom_skill_directory_is_scanned_and_loadable(self):
        custom = self.root / "my-skills"
        directory = custom / "demo"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: demo\ndescription: 自定义目录技能\n---\n正文\n",
            encoding="utf-8",
        )

        catalog = SkillsCatalog.discover(skill_dirs=[custom])
        self.assertIsNotNone(catalog.get("demo"))

        tools = ToolRegistry()
        register_skill_ops(tools, skills_dir=custom, catalog=catalog)
        result = execute(tools, "loadSkill", {"name": "demo"})
        self.assertIn("自定义目录技能", result)

    def test_get_accepts_directory_alias_and_casefold(self):
        write_skill(self.root, "web-search", "网页搜索")
        catalog = SkillsCatalog.discover([self.root])

        self.assertIsNotNone(catalog.get("web-search"))
        self.assertIsNotNone(catalog.get("WEB-SEARCH"))
        # Frontmatter 名称与目录名不同时,目录名也可以作为别名
        directory = self.root / ".agents" / "skills" / "dir-name"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: display-name\ndescription: 说明\n---\n正文\n",
            encoding="utf-8",
        )
        catalog = SkillsCatalog.discover([self.root])
        self.assertIsNotNone(catalog.get("dir-name"))


class TestLoadSkill(unittest.TestCase):
    """loadSkill 工具。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.skill_file = write_skill(
            self.root, "web-search", "网页搜索与总结",
            body="# 操作步骤\n1. 调用 searchWeb。\n2. 用 fetchWeb 抓取来源。",
        )
        self.catalog = SkillsCatalog.discover([self.root])
        self.tools = ToolRegistry()
        register_skill_ops(
            self.tools,
            skills_dir=self.root / ".agents" / "skills",
            catalog=self.catalog,
        )

    def test_loads_skill_md(self):
        result = execute(self.tools, "loadSkill", {"name": "web-search"})
        self.assertIn("Skill:web-search", result)
        self.assertIn("类型:SKILL.md", result)
        self.assertIn("调用 searchWeb", result)
        self.assertNotIn("错误", result)

    def test_loads_attachment(self):
        reference = (
            self.root / ".agents" / "skills" / "web-search" / "references"
        )
        reference.mkdir()
        (reference / "guide.md").write_text("附件内容:先搜索再抓取", encoding="utf-8")

        result = execute(
            self.tools, "loadSkill",
            {"name": "web-search", "filename": "references/guide.md"},
        )
        self.assertIn("类型:附件", result)
        self.assertIn("附件内容", result)
        self.assertIn("references/guide.md", result)

    def test_attachment_outside_skill_directory_is_rejected(self):
        secret = self.root / "secret.txt"
        secret.write_text("这是项目根目录的机密", encoding="utf-8")

        result = execute(
            self.tools, "loadSkill",
            {"name": "web-search", "filename": "../secret.txt"},
        )
        self.assertIn("错误", result)
        self.assertIn("技能目录之外", result)
        self.assertNotIn("机密", result)

    def test_ambiguous_attachment_returns_candidates(self):
        skill_dir = self.root / ".agents" / "skills" / "web-search"
        (skill_dir / "a").mkdir()
        (skill_dir / "b").mkdir()
        (skill_dir / "a" / "notes.md").write_text("A", encoding="utf-8")
        (skill_dir / "b" / "notes.md").write_text("B", encoding="utf-8")

        result = execute(
            self.tools, "loadSkill", {"name": "web-search", "filename": "notes.md"}
        )
        self.assertIn("多个", result)
        self.assertIn("a/notes.md", result)
        self.assertIn("b/notes.md", result)

    def test_unknown_skill_lists_available(self):
        result = execute(self.tools, "loadSkill", {"name": "不存在"})
        self.assertIn("未找到 Skill", result)
        self.assertIn("web-search", result)

    def test_gbk_skill_file_is_readable(self):
        target = self.root / ".agents" / "skills" / "gbk"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(
            "---\nname: gbk\ndescription: 中文技能\n---\n正文\n".encode("gbk")
        )
        catalog = SkillsCatalog.discover([self.root])
        tools = ToolRegistry()
        register_skill_ops(
            tools,
            skills_dir=self.root / ".agents" / "skills",
            catalog=catalog,
        )
        result = execute(tools, "loadSkill", {"name": "gbk"})
        self.assertIn("中文技能", result)
        self.assertIn("gb18030", result)


class TestSystemPrompt(unittest.TestCase):
    """系统提示词内容与会话注入。"""

    def test_prompt_contains_workspace_rules_skills_and_mcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(root, "web-search", "网页搜索与总结")
            catalog = SkillsCatalog.discover([root])

            prompt = build_system_prompt(
                workspace=root / "workspace",
                skills=catalog.skills,
                mcp_status=["服务器 pet: 已接入 5 个工具"],
            )

            self.assertIn(str(root / "workspace"), prompt)
            self.assertIn("web-search", prompt)
            self.assertIn("网页搜索与总结", prompt)
            self.assertIn("loadSkill", prompt)
            self.assertIn("createMcp", prompt)
            self.assertIn("readExternalFile", prompt)
            self.assertIn("服务器 pet", prompt)
            self.assertIn("mcp_", prompt)

    def test_agent_injects_prompt_then_agents_md(self):
        agent = Agent(
            llm=FakeLLM([[events.TextChunk("x"), events.StreamEnd("stop")]]),
            tools=ToolRegistry(),
            system_prompt="系统规则:用中文回答",
            agents_md="# 项目约定",
            skill_names=["web-search"],
        )
        self.assertTrue(agent.has_system_prompt)
        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertIn("系统规则", agent.messages[0]["content"])
        self.assertEqual(agent.messages[1]["role"], "system")
        self.assertIn("# 项目约定", agent.messages[1]["content"])
        self.assertEqual(agent.skill_names, ("web-search",))

        agent.reset()
        self.assertEqual(len(agent.messages), 2)
        self.assertIn("系统规则", agent.messages[0]["content"])
        self.assertIn("# 项目约定", agent.messages[1]["content"])


class SlowLLM(LLMClient):
    """先输出一段文字,然后长时间等待的假 LLM。"""

    model_name = "slow-model"

    def stream_chat(self, messages, tools=None):
        async def _gen():
            yield events.TextChunk("半截回复")
            await asyncio.sleep(30)
            yield events.StreamEnd("stop")

        return _gen()


class TestCancellation(unittest.IsolatedAsyncioTestCase):
    """用户取消对话时,Agent 回滚本轮聊天历史。"""

    async def test_cancel_rolls_back_history(self):
        agent = Agent(llm=SlowLLM(), tools=ToolRegistry())

        async def consume():
            async for _ in agent.chat("你好"):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(agent.messages, [])


if __name__ == "__main__":
    unittest.main()
