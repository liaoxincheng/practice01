"""readExternalFile(工作区外只读文件工具)的测试。"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core.external_ops import register_external_ops
from core.tools import ToolRegistry


class TestReadExternalFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.tools = ToolRegistry()
        register_external_ops(self.tools)

    def execute(self, arguments: dict) -> str:
        return asyncio.run(
            self.tools.execute("readExternalFile", json.dumps(arguments,
                                                              ensure_ascii=False))
        )

    def test_reads_file_outside_workspace(self):
        target = self.root / "notes.txt"
        target.write_text("工作区外的笔记", encoding="utf-8")

        result = self.execute({"path": str(target)})
        self.assertIn("工作区外只读", result)
        self.assertIn("工作区外的笔记", result)
        self.assertIn("utf-8", result)

    def test_reads_gbk_file(self):
        target = self.root / "gbk.txt"
        target.write_bytes("中文内容".encode("gbk"))

        result = self.execute({"path": str(target)})
        self.assertIn("中文内容", result)
        self.assertIn("gb18030", result)

    def test_rejects_binary_file(self):
        target = self.root / "data.bin"
        target.write_bytes(b"\x00\x01\x02" + "二进制".encode("utf-8") + b"\x00")

        result = self.execute({"path": str(target)})
        self.assertIn("错误", result)
        self.assertIn("不是纯文本", result)

    def test_truncates_long_content(self):
        target = self.root / "long.txt"
        target.write_text("a" * 100, encoding="utf-8")

        result = self.execute({"path": str(target), "max_chars": 10})
        self.assertIn("已截断", result)
        self.assertIn("共 100 个", result)
        self.assertIn("a" * 10, result)

    def test_missing_file(self):
        result = self.execute({"path": str(self.root / "missing.txt")})
        self.assertIn("不存在", result)

    def test_directory_is_rejected(self):
        result = self.execute({"path": str(self.root)})
        self.assertIn("目录", result)

    def test_device_path_is_rejected(self):
        result = self.execute({"path": "\\\\.\\pipe\\demo"})
        self.assertIn("错误", result)
        self.assertIn("设备", result)

    def test_empty_path_is_rejected(self):
        result = self.execute({"path": "   "})
        self.assertIn("错误", result)
        self.assertIn("不能为空", result)

    def test_registered_schema(self):
        self.assertIn("readExternalFile", self.tools.names())
        schemas = {
            s["function"]["name"]: s["function"]
            for s in self.tools.openai_schemas()
        }
        self.assertEqual(
            schemas["readExternalFile"]["parameters"]["required"], ["path"]
        )


if __name__ == "__main__":
    unittest.main()
