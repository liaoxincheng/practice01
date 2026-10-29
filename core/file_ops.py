"""文件操作工具(fileOps):把"读文件 / 写文件 / 列目录"注册成模型可调用的工具。

对应 practice02 原始版的 read_file / write_file / list_dir 三个函数,
功能完整保留,并额外做了两处工程化改进:

1. 安全沙箱:所有路径都限制在"工作目录"(work_dir)内,
   防止模型(以及通过模型下达指令的人)读写工作目录之外的任意系统文件;
2. 错误友好:文件不存在、编码错误等都以文本形式返回,
   模型能看懂错误原因并自己换一个路径重试。

未来扩展:在别的模块里照葫芦画瓢写一个新工具函数 + register 进来即可。
"""
from __future__ import annotations

from pathlib import Path

from .tools import Tool, ToolRegistry

# 单次读取文件的最大字符数,防止一次读入超大文件撑爆上下文
MAX_READ_CHARS = 20_000
# 列目录时最多列出的条目数
MAX_LIST_ENTRIES = 200


class FileOpsError(Exception):
    """文件操作的安全/参数错误。"""


def register_file_ops(registry: ToolRegistry, work_dir: Path) -> Path:
    """把三个文件工具注册进 registry,并确保工作目录存在。

    :param registry: 目标工具注册中心
    :param work_dir: 工作目录(所有文件操作都限制在这个目录内)
    :return: 解析后的工作目录绝对路径
    """
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    # ---- 安全校验:把模型给的路径解析到工作目录内 ----
    def _resolve(path: str) -> Path:
        """把路径解析为工作目录内的绝对路径;越界直接拒绝。"""
        p = Path(path)
        if not p.is_absolute():
            p = work_dir / p
        resolved = p.resolve()
        # 注意:不能简单用 startswith 比较字符串,要用相对关系比较路径本身
        if resolved != work_dir and work_dir not in resolved.parents:
            raise FileOpsError(
                f"越界拒绝:路径 {path!r} 不在工作目录 {work_dir} 内"
            )
        return resolved

    # ---- 工具 1:读文件 ----
    def read_file(path: str) -> str:
        """读取一个文本文件,返回文件内容。"""
        try:
            target = _resolve(path)
            if not target.is_file():
                return f"错误:文件不存在:{path}"
            content = target.read_text(encoding="utf-8")
            if len(content) > MAX_READ_CHARS:
                return (
                    content[:MAX_READ_CHARS]
                    + f"\n...(文件太长,已截断,共 {len(content)} 字符)"
                )
            return content
        except FileOpsError as exc:
            return f"错误:{exc}"
        except Exception as exc:
            return f"错误:读取失败:{exc}"

    # ---- 工具 2:写文件 ----
    def write_file(path: str, content: str) -> str:
        """把内容写入文本文件(覆盖写),目录不存在时自动创建。"""
        try:
            target = _resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"成功:已写入 {target.relative_to(work_dir)} ({len(content)} 字符)"
        except FileOpsError as exc:
            return f"错误:{exc}"
        except Exception as exc:
            return f"错误:写入失败:{exc}"

    # ---- 工具 3:列目录 ----
    def list_dir(path: str = ".") -> str:
        """列出目录下的文件和文件夹(标出类型)。"""
        try:
            target = _resolve(path)
            if not target.is_dir():
                return f"错误:目录不存在:{path}"
            entries = sorted(target.iterdir(), key=lambda p: p.name)
            if not entries:
                return "目录是空的"
            lines = [
                f"- [目录] {p.name}/" if p.is_dir() else f"- [文件] {p.name}"
                for p in entries[:MAX_LIST_ENTRIES]
            ]
            if len(entries) > MAX_LIST_ENTRIES:
                lines.append(
                    f"...(共 {len(entries)} 项,只显示前 {MAX_LIST_ENTRIES} 项)"
                )
            return "目录内容:\n" + "\n".join(lines)
        except FileOpsError as exc:
            return f"错误:{exc}"
        except Exception as exc:
            return f"错误:列出失败:{exc}"

    # ---- 注册进工具注册中心(模型能看到的只有 name/description/parameters) ----
    registry.register(
        Tool(
            name="read_file",
            description="读取工作目录中的一个文本文件,返回文件内容(最多 20000 字符)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径(相对或绝对,限制在工作目录内)",
                    },
                },
                "required": ["path"],
            },
            handler=read_file,
        )
    )
    registry.register(
        Tool(
            name="write_file",
            description="把内容写入工作目录中的一个文本文件(覆盖写,目录不存在时自动创建)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件路径(相对或绝对,限制在工作目录内)",
                    },
                    "content": {"type": "string", "description": "要写入的文件内容"},
                },
                "required": ["path", "content"],
            },
            handler=write_file,
        )
    )
    registry.register(
        Tool(
            name="list_dir",
            description="列出工作目录中某个目录下的文件和文件夹",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要列出的目录路径,默认工作目录根目录",
                    },
                },
            },
            handler=list_dir,
        )
    )

    return work_dir
