"""工作区外只读文件工具:readExternalFile。

普通文件工具(read_file / write_file 等)始终被限制在工作区内;
本工具提供一条显式的、只读的例外通道,只在用户明确要求读取工作区外
文件时使用:

- 只读:没有写入、删除、重命名能力;
- 只支持纯文本:二进制文件、设备路径会被拒绝;
- 单文件最多读取 200 KB,超长内容截断到 5 万字符;
- 仍然要求模型不要主动读取密钥、凭据、浏览器数据等隐私文件。
"""
from __future__ import annotations

import os
from pathlib import Path

from .tools import Tool, ToolRegistry

# 单个文件最多读取的字节数(防止一次读入超大文件)
MAX_FILE_BYTES = 200_000
# 返回给模型的最大字符数
MAX_OUTPUT_CHARS = 50_000
# 判断“像不像纯文本”时最多检查的字节数
_TEXT_SAMPLE_BYTES = 65_536

_DEVICE_PREFIXES = ("\\\\.\\", "//./")
_GLOBAL_ROOT_PREFIX = "\\\\?\\globalroot"


def _detect_encoding(data: bytes) -> str | None:
    """识别常见纯文本编码;无法识别时返回 None。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    for encoding in ("utf-8", "gb18030"):
        try:
            data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return encoding
    return None


def _looks_like_text(sample: bytes) -> bool:
    """根据 BOM、空字节和控制字符比例判断是否像纯文本。"""
    if not sample:
        return True
    if sample.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in sample:
        return False

    text = ""
    for encoding in ("utf-8", "gb18030"):
        try:
            text = sample.decode(encoding)
        except UnicodeDecodeError:
            text = sample.decode(encoding, errors="ignore")
        if text:
            break
    if not text:
        return False

    allowed = {"\n", "\r", "\t", "\f", "\b"}
    controls = sum(1 for char in text if ord(char) < 32 and char not in allowed)
    return controls / max(len(text), 1) < 0.05


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _resolve(path: str) -> Path:
    """解析用户给的路径:支持 ~ 和环境变量,相对路径基于当前目录。"""
    if not isinstance(path, str):
        raise ValueError("path 必须是文件路径字符串")
    text = path.strip()
    if not text:
        raise ValueError("path 不能为空")
    if "\x00" in text:
        raise ValueError("path 不能包含空字符")

    lowered = text.casefold()
    if lowered.startswith(_DEVICE_PREFIXES) or lowered.startswith(_GLOBAL_ROOT_PREFIX):
        raise ValueError("不支持读取设备、管道或全局命名空间路径")

    candidate = Path(os.path.expandvars(os.path.expanduser(text)))
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"无法解析路径:{text}:{exc}") from exc


def register_external_ops(registry: ToolRegistry) -> None:
    """注册 readExternalFile 工具(只读,不受工作区限制)。"""

    def read_external_file(path: str, max_chars: int | None = None) -> str:
        """读取工作区之外的纯文本文件,返回带编码/大小信息的正文。"""
        try:
            target = _resolve(path)
            if not target.exists():
                return f"错误:文件不存在:{target}"
            if target.is_dir():
                return f"错误:这是一个目录,不是文件:{target}"

            size = target.stat().st_size
            if size > MAX_FILE_BYTES:
                return (
                    f"错误:文件过大({_human_size(size)}),"
                    f"超过 {_human_size(MAX_FILE_BYTES)} 的读取上限"
                )

            data = target.read_bytes()
            if not _looks_like_text(data[:_TEXT_SAMPLE_BYTES]):
                return f"错误:不是纯文本文件:{target}(本工具只能读取纯文本)"

            encoding = _detect_encoding(data)
            if encoding is None:
                return f"错误:无法识别文件编码(仅支持 UTF-8 / GBK 等纯文本):{target}"
            text = data.decode(encoding)

            try:
                limit = MAX_OUTPUT_CHARS if max_chars is None else int(max_chars)
            except (TypeError, ValueError):
                return "错误:max_chars 必须是整数"
            limit = max(1, limit)

            truncated = len(text) > limit
            shown = text[:limit]
            header = [
                f"文件:{target}",
                "读取通道:工作区外只读(readExternalFile)",
                f"编码:{encoding};大小:{_human_size(size)};字符数:{len(text)}",
                "安全提示:该工具可读取工作区之外的路径,本次仅按用户要求读取。",
                "--- 内容开始 ---",
            ]
            footer = (
                [f"--- 内容已截断,仅显示前 {limit} 个字符(共 {len(text)} 个)---"]
                if truncated
                else ["--- 内容结束 ---"]
            )
            body = ["(空文件)"] if not text else [shown]
            return "\n".join(header + body + footer)
        except (ValueError, OSError) as exc:
            return f"错误:{exc}"
        except Exception as exc:  # noqa: BLE001 —— 工具错误统一转成文本
            return f"错误:读取失败:{exc}"

    registry.register(
        Tool(
            name="readExternalFile",
            description=(
                "读取工作区之外的纯文本文件(只读)。"
                "当用户明确要求查看、读取或总结工作区以外路径的文件时使用,"
                "例如桌面、下载目录、其他项目或绝对路径下的文件。"
                "path 可以是绝对路径,也可以是相对于当前工作目录的路径;"
                "普通文件工具仍然只能访问工作区,本工具不修改任何文件。"
                "只支持纯文本,会自动识别编码,内容过长时截断。"
                "不要用它读取与任务无关的私密文件,如密钥、凭据、浏览器数据。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径,支持绝对路径或相对当前工作目录的路径",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "最多读取的字符数,默认 50000",
                        "default": 50_000,
                    },
                },
                "required": ["path"],
            },
            handler=read_external_file,
        )
    )
