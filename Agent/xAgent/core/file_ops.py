"""文件操作工具(fileOps):把 9 个文件操作注册成模型可调用的工具。

read_file / write_file / list_dir 三个来自 practice02 原始版,
另新增 get_file_type / edit_file / create_directory / delete_directory /
delete_file / rename_path 六个,覆盖课程 practice02 文件工具任务的全部要求。
所有工具都做了两处工程化改进:

1. 安全沙箱:所有路径都限制在"工作目录"(work_dir)内,
   防止模型(以及通过模型下达指令的人)读写工作目录之外的任意系统文件;
2. 错误友好:文件不存在、编码错误等都以文本形式返回,
   模型能看懂错误原因并自己换一个路径重试。

未来扩展:在别的模块里照葫芦画瓢写一个新工具函数 + register 进来即可。
"""
from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path

from .tools import Tool, ToolRegistry

# 单次读取文件的最大字符数,防止一次读入超大文件撑爆上下文
MAX_READ_CHARS = 20_000
# 列目录时最多列出的条目数
MAX_LIST_ENTRIES = 200


class FileOpsError(Exception):
    """文件操作的安全/参数错误。"""


def _try_decode(data: bytes) -> str | None:
    """把字节按 UTF-8 / GBK 依次尝试解码,都失败返回 None(用于判断纯文本)。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


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

    # ---- 工具 3:获取文件类型 ----
    def get_file_type(path: str) -> str:
        """获取指定文件的类型:扩展名、MIME 类型、是否纯文本。"""
        try:
            target = _resolve(path)
            if not target.is_file():
                return f"错误:文件不存在:{path}"
            suffix = target.suffix.lower() or "(无扩展名)"
            mime, _ = mimetypes.guess_type(target.name)
            # 判断纯文本:读开头 4KB,能按 UTF-8/GBK 解码且没有空字节
            with open(target, "rb") as f:
                sample = f.read(4096)
            is_text = b"\x00" not in sample and _try_decode(sample) is not None
            return (
                f"文件:{path}\n"
                f"扩展名:{suffix}\n"
                f"MIME:{mime or '未知'}\n"
                f"是否纯文本:{'是' if is_text else '否(二进制文件)'}"
            )
        except FileOpsError as exc:
            return f"错误:{exc}"
        except Exception as exc:
            return f"错误:读取失败:{exc}"

    # ---- 工具 4:编辑已有文件 ----
    def edit_file(path: str, content: str, mode: str = "overwrite") -> str:
        """编辑已有文件:mode=overwrite 整体替换,mode=append 追加末尾。"""
        if mode not in ("overwrite", "append"):
            return "错误:mode 只能是 overwrite 或 append"
        try:
            target = _resolve(path)
            if not target.is_file():
                return f"错误:文件不存在:{path}(要新建文件请用 write_file)"
            if mode == "append":
                with open(target, "a", encoding="utf-8") as f:
                    f.write(content)
                return f"成功:已向 {target.relative_to(work_dir)} 追加 {len(content)} 字符"
            target.write_text(content, encoding="utf-8")
            return f"成功:已覆盖写入 {target.relative_to(work_dir)} ({len(content)} 字符)"
        except FileOpsError as exc:
            return f"错误:{exc}"
        except Exception as exc:
            return f"错误:编辑失败:{exc}"

    # ---- 工具 5:创建目录 ----
    def create_directory(path: str) -> str:
        """创建新目录(父目录自动创建)。"""
        try:
            target = _resolve(path)
            if target.is_dir():
                return f"目录已存在:{path}"
            target.mkdir(parents=True, exist_ok=True)
            return f"成功:已创建目录 {target.relative_to(work_dir)}"
        except FileOpsError as exc:
            return f"错误:{exc}"
        except Exception as exc:
            return f"错误:创建目录失败:{exc}"

    # ---- 工具 6:删除目录(非空也删) ----
    def delete_directory(path: str) -> str:
        """删除目录,非空目录会连同内容一起删除(不能删工作目录本身)。"""
        try:
            target = _resolve(path)
            if not target.is_dir():
                return f"错误:目录不存在:{path}"
            if target == work_dir:
                return "错误:不能删除工作目录本身"
            shutil.rmtree(target)
            return f"成功:已删除目录 {path}(含其中的全部内容)"
        except FileOpsError as exc:
            return f"错误:{exc}"
        except Exception as exc:
            return f"错误:删除目录失败:{exc}"

    # ---- 工具 7:删除文件 ----
    def delete_file(path: str) -> str:
        """删除单个文件。"""
        try:
            target = _resolve(path)
            if not target.is_file():
                return f"错误:文件不存在:{path}"
            target.unlink()
            return f"成功:已删除文件 {path}"
        except FileOpsError as exc:
            return f"错误:{exc}"
        except Exception as exc:
            return f"错误:删除文件失败:{exc}"

    # ---- 工具 8:重命名 / 移动 ----
    def rename_path(old_path: str, new_path: str) -> str:
        """重命名或移动文件、目录(新旧路径都必须位于工作目录内)。"""
        try:
            old = _resolve(old_path)
            new = _resolve(new_path)
            if not old.exists():
                return f"错误:路径不存在:{old_path}"
            if old == work_dir:
                return "错误:不能重命名工作目录本身"
            if new.exists():
                return f"错误:目标已存在:{new_path}"
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            return f"成功:已把 {old_path} 重命名为 {new_path}"
        except FileOpsError as exc:
            return f"错误:{exc}"
        except Exception as exc:
            return f"错误:重命名失败:{exc}"

    # ---- 工具 9:列目录 ----
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
    registry.register(
        Tool(
            name="get_file_type",
            description="获取工作目录中指定文件的类型:扩展名、MIME 类型、是否纯文本",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要检查的文件路径"},
                },
                "required": ["path"],
            },
            handler=get_file_type,
        )
    )
    registry.register(
        Tool(
            name="edit_file",
            description=(
                "编辑工作目录中已有的文本文件:overwrite 整体替换,append 追加到末尾。"
                "文件不存在时请改用 write_file 新建"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要编辑的文件路径"},
                    "content": {"type": "string", "description": "要写入的内容"},
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "编辑方式,默认 overwrite",
                    },
                },
                "required": ["path", "content"],
            },
            handler=edit_file,
        )
    )
    registry.register(
        Tool(
            name="create_directory",
            description="在工作目录中创建新目录,父目录自动创建",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要创建的目录路径"},
                },
                "required": ["path"],
            },
            handler=create_directory,
        )
    )
    registry.register(
        Tool(
            name="delete_directory",
            description=(
                "删除工作目录中的一个目录;非空目录会连同其中的内容一起删除,请谨慎使用"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的目录路径"},
                },
                "required": ["path"],
            },
            handler=delete_directory,
        )
    )
    registry.register(
        Tool(
            name="delete_file",
            description="删除工作目录中的单个文件",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的文件路径"},
                },
                "required": ["path"],
            },
            handler=delete_file,
        )
    )
    registry.register(
        Tool(
            name="rename_path",
            description="重命名或移动工作目录中的文件、目录",
            parameters={
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": "原路径"},
                    "new_path": {"type": "string", "description": "新路径"},
                },
                "required": ["old_path", "new_path"],
            },
            handler=rename_path,
        )
    )

    return work_dir
