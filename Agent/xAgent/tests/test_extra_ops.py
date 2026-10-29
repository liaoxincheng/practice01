"""时间工具(get_date / is_leap_year)与新增文件工具的测试。

覆盖:取时间走本机命令、闰年规则、文件类型识别、追加/覆盖编辑、
建目录、删目录(非空)、删文件、重命名、沙箱越界拒绝、工作目录保护。

运行方式(在项目根目录):
    venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core.file_ops import register_file_ops
from core.time_ops import register_time_ops
from core.tools import ToolRegistry


def make_registry(work_dir: Path) -> ToolRegistry:
    tools = ToolRegistry()
    register_file_ops(tools, work_dir=work_dir)
    register_time_ops(tools)
    return tools


def execute(tools: ToolRegistry, name: str, arguments: dict) -> str:
    return asyncio.run(tools.execute(name, json.dumps(arguments, ensure_ascii=False)))


class TestTimeOps(unittest.TestCase):
    """时间工具的行为测试。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tools = make_registry(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_get_date_default_format(self):
        """get_date 返回真实日期时间(通过本机命令获取)。"""
        result = execute(self.tools, "get_date", {})
        self.assertRegex(
            result, r"^当前日期时间:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        )

    def test_get_date_custom_format(self):
        """get_date 支持 strftime 自定义格式;非法格式给出错误而不是崩溃。"""
        result = execute(self.tools, "get_date", {"format": "%Y年%m月%d日"})
        self.assertRegex(result, r"^当前日期时间:\d{4}年\d{2}月\d{2}日$")

        result = execute(self.tools, "get_date", {"format": "%Q"})
        self.assertTrue(result.startswith("错误"), result)

    def test_is_leap_year_rules(self):
        """闰年规则:4 年一闰,百年不闰,四百年再闰;不传年份用当前年份。"""
        cases = {2024: True, 2023: False, 1900: False, 2000: True, 2100: False}
        for year, expected in cases.items():
            result = execute(self.tools, "is_leap_year", {"year": year})
            self.assertIn("是闰年" if expected else "不是闰年", result)
            self.assertIn(str(year), result)

        result = execute(self.tools, "is_leap_year", {})
        self.assertRegex(result, r"^\d{4} 年(是|不是)闰年$")

    def test_time_schemas(self):
        """两个时间工具的 schema 正确注册。"""
        names = set(self.tools.names())
        self.assertTrue({"get_date", "is_leap_year"} <= names)


class TestExtraFileOps(unittest.TestCase):
    """新增文件工具的行为测试(每个用例独立工作目录)。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.tools = make_registry(self.work)

    def test_create_directory_and_nested(self):
        """create_directory 自动创建父目录,重复创建给出提示。"""
        result = execute(self.tools, "create_directory", {"path": "a/b/c"})
        self.assertTrue((self.work / "a/b/c").is_dir(), result)
        result = execute(self.tools, "create_directory", {"path": "a/b/c"})
        self.assertIn("已存在", result)

    def test_edit_file_append_and_overwrite(self):
        """edit_file:append 追加、overwrite 覆盖;不存在时报错。"""
        execute(self.tools, "write_file", {"path": "t.txt", "content": "第一行\n"})
        result = execute(
            self.tools, "edit_file",
            {"path": "t.txt", "content": "第二行\n", "mode": "append"},
        )
        self.assertIn("追加", result)
        self.assertEqual(
            (self.work / "t.txt").read_text(encoding="utf-8"), "第一行\n第二行\n"
        )
        execute(
            self.tools, "edit_file",
            {"path": "t.txt", "content": "全部替换", "mode": "overwrite"},
        )
        self.assertEqual(
            (self.work / "t.txt").read_text(encoding="utf-8"), "全部替换"
        )
        result = execute(
            self.tools, "edit_file", {"path": "nope.txt", "content": "x"}
        )
        self.assertIn("文件不存在", result)

    def test_get_file_type(self):
        """get_file_type 区分纯文本与二进制。"""
        execute(self.tools, "write_file", {"path": "t.txt", "content": "你好"})
        result = execute(self.tools, "get_file_type", {"path": "t.txt"})
        self.assertIn("text/plain", result)
        self.assertIn("是否纯文本:是", result)

        (self.work / "bin.dat").write_bytes(b"\x00\x01\x02")
        result = execute(self.tools, "get_file_type", {"path": "bin.dat"})
        self.assertIn("是否纯文本:否", result)

        result = execute(self.tools, "get_file_type", {"path": "ghost.txt"})
        self.assertIn("文件不存在", result)

    def test_rename_and_delete_file(self):
        """rename_path 改名,delete_file 删除。"""
        execute(self.tools, "write_file", {"path": "a.txt", "content": "x"})
        result = execute(
            self.tools, "rename_path", {"old_path": "a.txt", "new_path": "b.txt"}
        )
        self.assertIn("成功", result)
        self.assertTrue((self.work / "b.txt").is_file())
        self.assertFalse((self.work / "a.txt").exists())

        result = execute(self.tools, "delete_file", {"path": "b.txt"})
        self.assertIn("成功", result)
        self.assertFalse((self.work / "b.txt").exists())

    def test_delete_nonempty_directory(self):
        """delete_directory 能删非空目录;删除后目录和内容都不在。"""
        execute(self.tools, "write_file", {"path": "d/x.txt", "content": "x"})
        result = execute(self.tools, "delete_directory", {"path": "d"})
        self.assertIn("成功", result)
        self.assertFalse((self.work / "d").exists())

    def test_cannot_delete_work_dir_itself(self):
        """delete_directory 拒绝删除工作目录本身。"""
        result = execute(self.tools, "delete_directory", {"path": "."})
        self.assertIn("不能删除工作目录", result)
        self.assertTrue(self.work.is_dir())

    def test_sandbox_escape_rejected(self):
        """新增工具同样受沙箱保护:越界路径全部拒绝。"""
        cases = [
            ("get_file_type", {"path": "../../outside.txt"}),
            ("edit_file", {"path": "../evil.txt", "content": "x"}),
            ("create_directory", {"path": "../evil_dir"}),
            ("delete_directory", {"path": "../evil_dir"}),
            ("delete_file", {"path": "../evil.txt"}),
            ("rename_path", {"old_path": "a.txt", "new_path": "../evil.txt"}),
        ]
        for name, args in cases:
            result = execute(self.tools, name, args)
            self.assertTrue(result.startswith("错误"), f"{name}: {result}")
            self.assertIn("越界", result, f"{name}: {result}")

    def test_rename_target_exists_rejected(self):
        """rename_path 目标已存在时拒绝,不覆盖已有文件。"""
        execute(self.tools, "write_file", {"path": "a.txt", "content": "a"})
        execute(self.tools, "write_file", {"path": "b.txt", "content": "b"})
        result = execute(
            self.tools, "rename_path", {"old_path": "a.txt", "new_path": "b.txt"}
        )
        self.assertIn("目标已存在", result)
        self.assertEqual((self.work / "b.txt").read_text(encoding="utf-8"), "b")


if __name__ == "__main__":
    unittest.main()
