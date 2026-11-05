"""createSkill 工具(技能创建)的行为测试。

覆盖:正常创建目录与 SKILL.md、名称格式校验、已存在时拒绝覆盖、
Frontmatter 规范校验(name/description 必填、name 与参数一致、正文非空)、
以及 Agent 完整调用回路。

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
from core.skill_ops import register_skill_ops
from core.tools import ToolRegistry
from tests.test_core import FakeLLM


def make_registry(skills_root: Path) -> ToolRegistry:
    tools = ToolRegistry()
    register_skill_ops(tools, skills_dir=skills_root / ".agents" / "skills")
    return tools


def execute(tools: ToolRegistry, name: str, arguments: dict) -> str:
    return asyncio.run(tools.execute(name, json.dumps(arguments, ensure_ascii=False)))


def skill_content(name: str = "web-search", description: str = "网页搜索技能") -> str:
    """一份符合规范的 SKILL.md 完整内容。"""
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n"
        "\n"
        "# 操作步骤\n"
        "1. 调用 searchWeb 工具搜索;\n"
        "2. 用 fetchWeb 抓取来源网页;\n"
        "3. 汇总结果回答用户。\n"
    )


class TestCreateSkill(unittest.TestCase):
    """createSkill 的创建与校验行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.tools = make_registry(self.root)
        self.skills = self.root / ".agents" / "skills"

    def test_creates_skill_directory_and_skill_md(self):
        """正常调用:创建 <名称> 目录并把内容写入 SKILL.md。"""
        result = execute(
            self.tools, "createSkill",
            {"name": "web-search", "skillContext": skill_content()},
        )
        self.assertTrue(result.startswith("成功"), result)
        self.assertIn("web-search", result)
        self.assertIn("SKILL.md", result)

        target = self.skills / "web-search" / "SKILL.md"
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), skill_content())

    def test_existing_skill_is_not_overwritten(self):
        """同名 Skill 已存在:明确拒绝,内容保持不变。"""
        execute(
            self.tools, "createSkill",
            {"name": "web-search", "skillContext": skill_content()},
        )
        target = self.skills / "web-search" / "SKILL.md"
        original = target.read_text(encoding="utf-8")

        result = execute(
            self.tools, "createSkill",
            {"name": "web-search",
             "skillContext": skill_content(description="想覆盖的新版本")},
        )
        self.assertIn("已存在", result)
        self.assertIn("避免覆盖", result)
        self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_existing_dir_without_skill_md_is_usable(self):
        """目录已存在但没有 SKILL.md(如模型先建了目录):照常写入。"""
        (self.skills / "web-search").mkdir(parents=True)
        result = execute(
            self.tools, "createSkill",
            {"name": "web-search", "skillContext": skill_content()},
        )
        self.assertTrue(result.startswith("成功"), result)
        self.assertTrue((self.skills / "web-search" / "SKILL.md").is_file())

    def test_invalid_names_are_rejected(self):
        """名称必须是小写字母、数字、连字符;非法名称一律拒绝且不落盘。"""
        bad_names = [
            "", "Web Search", "WEB-SEARCH", "web_search", "-web",
            "web-", "web--search", "web.搜索", "web/../x", "..", "搜索",
        ]
        for name in bad_names:
            result = execute(
                self.tools, "createSkill",
                {"name": name, "skillContext": skill_content(name or "x")},
            )
            self.assertTrue(result.startswith("错误"), f"name={name!r}: {result}")
            self.assertIn("小写字母", result)
        # 没有任何技能目录被创建
        self.assertEqual(list(self.skills.iterdir()), [], "非法名称不应创建任何技能目录")

    def test_missing_or_empty_skill_context_is_rejected(self):
        result = execute(self.tools, "createSkill", {"name": "web-search"})
        self.assertIn("不能为空", result)

        result = execute(
            self.tools, "createSkill", {"name": "web-search", "skillContext": "  "}
        )
        self.assertIn("不能为空", result)

    def test_missing_frontmatter_is_rejected(self):
        """没有 YAML Frontmatter 的内容被拒绝,并提示正确格式。"""
        result = execute(
            self.tools, "createSkill",
            {"name": "web-search", "skillContext": "1. 搜索\n2. 回答"},
        )
        self.assertIn("Frontmatter", result)
        self.assertIn("---", result)

    def test_frontmatter_requires_name_and_description(self):
        no_name = "---\ndescription: 搜索技能\n---\n正文内容\n"
        result = execute(
            self.tools, "createSkill", {"name": "web-search", "skillContext": no_name}
        )
        self.assertIn("缺少 name", result)

        no_desc = "---\nname: web-search\n---\n正文内容\n"
        result = execute(
            self.tools, "createSkill", {"name": "web-search", "skillContext": no_desc}
        )
        self.assertIn("缺少 description", result)

    def test_frontmatter_name_must_match_parameter(self):
        mismatched = skill_content(name="pdf-reader")
        result = execute(
            self.tools, "createSkill",
            {"name": "web-search", "skillContext": mismatched},
        )
        self.assertIn("不一致", result)

    def test_body_after_frontmatter_is_required(self):
        no_body = "---\nname: web-search\ndescription: 搜索技能\n---\n"
        result = execute(
            self.tools, "createSkill",
            {"name": "web-search", "skillContext": no_body},
        )
        self.assertIn("正文", result)

    def test_schema_is_registered(self):
        """schema 正确注册:name/skillContext 均为必填。"""
        self.assertIn("createSkill", self.tools.names())
        schemas = {
            s["function"]["name"]: s["function"] for s in self.tools.openai_schemas()
        }
        self.assertEqual(schemas["createSkill"]["parameters"]["required"],
                         ["name", "skillContext"])


class TestCreateSkillWithAgent(unittest.TestCase):
    """Agent 完整回路:模型提议调用 createSkill → 执行 → 技能落盘。"""

    def test_agent_executes_create_skill_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp) / ".agents" / "skills"
            tools = ToolRegistry()
            register_skill_ops(tools, skills_dir=skills)

            agent = Agent(
                llm=FakeLLM(
                    [
                        [
                            events.TextChunk("好的,我来创建这个技能。"),
                            events.ToolCallProposal(
                                call_id="call_1",
                                name="createSkill",
                                arguments=json.dumps(
                                    {"name": "web-search",
                                     "skillContext": skill_content()},
                                    ensure_ascii=False,
                                ),
                            ),
                            events.StreamEnd("tool_calls"),
                        ],
                        [
                            events.TextChunk("技能 web-search 已创建完成。"),
                            events.StreamEnd("stop"),
                        ],
                    ]
                ),
                tools=tools,
            )

            async def run():
                return [ev async for ev in agent.chat("帮我创建一个网页搜索技能")]

            stream = asyncio.run(run())

            finished = [
                ev for ev in stream if isinstance(ev, events.ToolCallFinished)
            ]
            self.assertEqual(len(finished), 1)
            self.assertEqual(finished[0].name, "createSkill")
            self.assertTrue(finished[0].ok, finished[0].result)
            self.assertIn("成功", finished[0].result)

            # 技能真的落盘,内容与模型给的一致
            target = skills / "web-search" / "SKILL.md"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), skill_content())


if __name__ == "__main__":
    unittest.main()
