# practice02(三):带 12 个工具调用的流式对话程序(本地大模型)
# 用法:
#   python chat-stream-tool-fileOps.py
# 输入 exit 或 退出 结束对话
#
# 与 chat-stream-tool-isLeapYear.py 的区别:
#   在 getDate / isLeapYear 的基础上,新增 10 个本地文件操作工具:
#     getCurrentDirectory  获取当前工作目录
#     listDirectory        列出目录下所有文件和子目录(可选递归)
#     getFileType          获取文件类型(扩展名、MIME、是否纯文本)
#     readFile             读取纯文本文件内容(UTF-8 / GBK 自适应)
#     createFile           根据指定目录和文件名创建新文件
#     editFile             编辑已有文件(整体覆盖 / 追加)
#     createDirectory      创建新目录(父目录自动创建)
#     deleteDirectory      删除目录(非空也会连内容一起删)
#     deleteFile           删除单个文件
#     renamePath           重命名或移动文件、目录
#   共 12 个工具,模型按需调用。
import configparser
import json
import mimetypes
import os
import shutil
import subprocess
import sys
from datetime import datetime

# Windows 下统一按 UTF-8 读写终端,避免中文乱码
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from openai import OpenAI

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")
client = OpenAI(
    base_url=config["llm"]["base_url"],
    api_key=config["llm"]["api_key"],
)
MODEL = config["llm"]["model"]
MAX_TOOL_ROUNDS = 5  # 工具调用最多来回几轮,防止模型一直调工具停不下来

# 单次读取文件的最大行数(超出部分截断,防止大文件撑爆上下文)
MAX_READ_LINES = 200
# 列目录时最多输出的条目数
MAX_LIST_ENTRIES = 200


# ---------- 时间工具 ----------

def get_date(format: str = "%Y-%m-%d %H:%M:%S") -> str:
    """调用本机命令获取当前日期和时间(固定命令,不拼接参数)。"""
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'",
            ],
            shell=False,
            capture_output=True,
            timeout=10,
        )
    except FileNotFoundError:
        return "错误:找不到 powershell 程序,无法获取日期时间"
    except subprocess.TimeoutExpired:
        return "错误:获取日期时间超时"

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        return f"错误:获取日期时间失败:{err}"

    text = proc.stdout.decode("utf-8", errors="replace").strip()
    try:
        moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return f"当前日期时间:{text}"
    return f"当前日期时间:{moment.strftime(format or '%Y-%m-%d %H:%M:%S')}"


def is_leap_year(year: int = 0) -> str:
    """判断某年是不是闰年(纯 Python 计算);不传年份时用本机当前年份。"""
    if not year:
        year = datetime.now().year
    leap = (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
    return f"{year} 年{'是' if leap else '不是'}闰年"


# ---------- 文件工具的小辅助 ----------

def _decode_text(data: bytes):
    """把文件字节解码成文本:UTF-8 / GBK 依次尝试,都不行返回 (None, None)。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None, None


def _format_size(size: int) -> str:
    """把字节数格式化成便于阅读的大小。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


# ---------- 文件工具实现 ----------

def get_current_directory() -> str:
    """返回当前工作目录的绝对路径。"""
    return f"当前工作目录:{os.getcwd()}"


def list_directory(path: str = ".", recursive: bool = False) -> str:
    """列出指定目录下的文件和子目录;recursive=True 时递归列出。"""
    if not os.path.isdir(path):
        return f"错误:目录不存在:{path}"

    lines = []
    if recursive:
        for root, dirs, files in os.walk(path):
            dirs.sort()
            rel = os.path.relpath(root, path)
            prefix = "" if rel == "." else rel + os.sep
            for d in dirs:
                lines.append(f"- [目录] {prefix}{d}/")
            for f in sorted(files):
                full = os.path.join(root, f)
                lines.append(f"- [文件] {prefix}{f} ({_format_size(os.path.getsize(full))})")
    else:
        entries = sorted(os.listdir(path))
        for name in entries:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                lines.append(f"- [目录] {name}/")
            else:
                lines.append(f"- [文件] {name} ({_format_size(os.path.getsize(full))})")

    if not lines:
        return "目录是空的"
    if len(lines) > MAX_LIST_ENTRIES:
        lines = lines[:MAX_LIST_ENTRIES] + [
            f"...(共 {len(lines)} 项,只显示前 {MAX_LIST_ENTRIES} 项)"
        ]
    return "目录内容:\n" + "\n".join(lines)


def get_file_type(path: str) -> str:
    """返回文件类型信息:扩展名、MIME 类型、是否纯文本。"""
    if not os.path.isfile(path):
        return f"错误:文件不存在:{path}"
    ext = os.path.splitext(path)[1].lower() or "(无扩展名)"
    mime, _ = mimetypes.guess_type(path)
    # 判断是否纯文本:读取文件开头一小段,能按 UTF-8/GBK 解码且不含空字节
    try:
        with open(path, "rb") as f:
            sample = f.read(4096)
        is_text = b"\x00" not in sample and _decode_text(sample)[0] is not None
    except OSError as e:
        return f"错误:读取文件失败:{e}"
    return (
        f"文件:{path}\n"
        f"扩展名:{ext}\n"
        f"MIME:{mime or '未知'}\n"
        f"是否纯文本:{'是' if is_text else '否(二进制文件)'}"
    )


def read_file(path: str, max_lines: int = MAX_READ_LINES) -> str:
    """读取纯文本文件内容(UTF-8 / GBK 自适应),最多 max_lines 行。"""
    if not os.path.isfile(path):
        return f"错误:文件不存在:{path}"
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return f"错误:读取失败:{e}"
    text, encoding = _decode_text(data)
    if text is None:
        return f"错误:{path} 不是纯文本文件(无法按 UTF-8/GBK 解码)"
    lines = text.splitlines()
    truncated = len(lines) > max_lines
    shown = "\n".join(lines[:max_lines])
    note = f"\n...(共 {len(lines)} 行,只显示前 {max_lines} 行)" if truncated else ""
    # 替换非法字符,防止某些终端显示问题
    _ = encoding
    return f"文件内容({path},{len(lines)} 行,{encoding} 编码):\n{shown}{note}"


def create_file(directory: str, file_name: str, content: str = "") -> str:
    """在指定目录下创建新文件;已存在时不覆盖,请改用 editFile。"""
    if not file_name.strip():
        return "错误:file_name 不能为空"
    target = os.path.join(directory, file_name)
    if os.path.exists(target):
        return f"错误:文件已存在:{target}(要修改已有文件请用 editFile)"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return f"错误:创建文件失败:{e}"
    return f"成功:已创建 {target} ({len(content)} 字符)"


def edit_file(path: str, content: str, mode: str = "overwrite") -> str:
    """编辑已有文件:mode=overwrite 整体替换,mode=append 追加到末尾。"""
    if mode not in ("overwrite", "append"):
        return "错误:mode 只能是 overwrite 或 append"
    if not os.path.isfile(path):
        return f"错误:文件不存在:{path}(要新建文件请用 createFile)"
    try:
        if mode == "append":
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            return f"成功:已向 {path} 追加 {len(content)} 字符"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"成功:已覆盖写入 {path} ({len(content)} 字符)"
    except OSError as e:
        return f"错误:编辑文件失败:{e}"


def create_directory(path: str) -> str:
    """创建新目录(父目录自动创建);已存在时直接说明。"""
    if os.path.isdir(path):
        return f"目录已存在:{path}"
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        return f"错误:创建目录失败:{e}"
    return f"成功:已创建目录 {path}"


def delete_directory(path: str) -> str:
    """删除目录;非空目录会连同内容一起删除。"""
    if not os.path.isdir(path):
        return f"错误:目录不存在:{path}"
    try:
        shutil.rmtree(path)
    except OSError as e:
        return f"错误:删除目录失败:{e}"
    return f"成功:已删除目录 {path}(含其中的全部内容)"


def delete_file(path: str) -> str:
    """删除单个文件。"""
    if not os.path.isfile(path):
        return f"错误:文件不存在:{path}"
    try:
        os.remove(path)
    except OSError as e:
        return f"错误:删除文件失败:{e}"
    return f"成功:已删除文件 {path}"


def rename_path(old_path: str, new_path: str) -> str:
    """重命名或移动文件、目录。"""
    if not os.path.exists(old_path):
        return f"错误:路径不存在:{old_path}"
    if os.path.exists(new_path):
        return f"错误:目标已存在:{new_path}"
    try:
        os.rename(old_path, new_path)
    except OSError as e:
        return f"错误:重命名失败:{e}"
    return f"成功:已把 {old_path} 重命名为 {new_path}"


# ---------- 工具说明(OpenAI function calling 格式,给模型看的"说明书") ----------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "description": "输出格式(可选),Python strftime 写法,默认 %Y-%m-%d %H:%M:%S",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断一个年份是不是闰年。不传年份时判断本机当前年份。",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "要判断的年份,如 2024"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCurrentDirectory",
            "description": "获取当前工作目录的绝对路径。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listDirectory",
            "description": "列出指定目录下的所有文件和子目录,标出类型和大小;可选递归。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要列出的目录,默认当前目录"},
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归列出子目录内容,默认 false",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getFileType",
            "description": "获取指定文件的类型:扩展名、MIME 类型、是否纯文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要检查的文件路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readFile",
            "description": "读取纯文本文件内容(支持 UTF-8 和 GBK),默认最多 200 行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读取的文件路径"},
                    "max_lines": {
                        "type": "integer",
                        "description": "最多读取的行数,默认 200",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "在指定目录下创建新文件并写入初始内容;文件已存在时不覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "目录路径,如 ."},
                    "file_name": {"type": "string", "description": "文件名,如 hello.txt"},
                    "content": {"type": "string", "description": "文件初始内容,可为空"},
                },
                "required": ["directory", "file_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editFile",
            "description": "编辑已有文件内容:overwrite 整体替换,append 追加到末尾。",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createDirectory",
            "description": "创建新目录,父目录会自动创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要创建的目录路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteDirectory",
            "description": "删除目录;非空目录会连同其中的内容一起删除,请谨慎使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的目录路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteFile",
            "description": "删除单个文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的文件路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "renamePath",
            "description": "重命名或移动文件、目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": "原路径"},
                    "new_path": {"type": "string", "description": "新路径"},
                },
                "required": ["old_path", "new_path"],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict) -> str:
    """执行模型要求的工具调用,返回执行结果字符串。"""
    if name == "getDate":
        return get_date(arguments.get("format") or "%Y-%m-%d %H:%M:%S")
    if name == "isLeapYear":
        try:
            year = int(arguments.get("year") or 0)
        except (TypeError, ValueError):
            return "错误:year 必须是整数年份,如 2024"
        return is_leap_year(year)
    if name == "getCurrentDirectory":
        return get_current_directory()
    if name == "listDirectory":
        return list_directory(
            arguments.get("path") or ".", bool(arguments.get("recursive"))
        )
    if name == "getFileType":
        return get_file_type(arguments.get("path", ""))
    if name == "readFile":
        try:
            max_lines = int(arguments.get("max_lines") or MAX_READ_LINES)
        except (TypeError, ValueError):
            max_lines = MAX_READ_LINES
        return read_file(arguments.get("path", ""), max_lines)
    if name == "createFile":
        return create_file(
            arguments.get("directory") or ".", arguments.get("file_name", ""),
            arguments.get("content", ""),
        )
    if name == "editFile":
        return edit_file(
            arguments.get("path", ""), arguments.get("content", ""),
            arguments.get("mode") or "overwrite",
        )
    if name == "createDirectory":
        return create_directory(arguments.get("path", ""))
    if name == "deleteDirectory":
        return delete_directory(arguments.get("path", ""))
    if name == "deleteFile":
        return delete_file(arguments.get("path", ""))
    if name == "renamePath":
        return rename_path(arguments.get("old_path", ""), arguments.get("new_path", ""))
    return f"错误:没有名为 {name} 的工具"


# ---------- 主循环 ----------

# 聊天历史:每次对话都带上,让模型记住上下文
messages = []

print("带 12 个工具的大模型对话程序 [流式]")
print("模型可调用工具: getDate / isLeapYear / 文件操作(getCurrentDirectory、listDirectory、")
print("  getFileType、readFile、createFile、editFile、createDirectory、deleteDirectory、")
print("  deleteFile、renamePath)")
print("输入 exit 或 退出 结束对话")

while True:
    user_input = input("你: ").strip()
    if user_input.lower() in ("exit", "quit", "退出"):
        break
    if not user_input:
        continue

    messages.append({"role": "user", "content": user_input})

    # 工具调用循环:模型每回答一次,如果还想调用工具,就执行工具并把结果送回,再让它回答
    for _ in range(MAX_TOOL_ROUNDS):
        print("AI: ", end="", flush=True)
        reply = ""
        # 流式过程中,一次工具调用会分成多个增量(chunk)到达,先按 index 合并
        tool_calls = {}

        stream = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS, stream=True
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                reply += delta.content
                print(delta.content, end="", flush=True)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    entry = tool_calls.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["name"] += tc.function.name
                        if tc.function.arguments:
                            entry["arguments"] += tc.function.arguments
        print()

        # 模型没有要求调用工具:正常回答结束
        if not tool_calls:
            messages.append({"role": "assistant", "content": reply})
            break

        # 模型要求调用工具:把"带工具调用的回复"记入历史
        messages.append(
            {
                "role": "assistant",
                "content": reply or None,
                "tool_calls": [
                    {
                        "id": entry["id"],
                        "type": "function",
                        "function": {
                            "name": entry["name"],
                            "arguments": entry["arguments"],
                        },
                    }
                    for entry in tool_calls.values()
                ],
            }
        )

        # 逐个执行工具,并把结果作为 tool 消息送回模型
        for entry in tool_calls.values():
            try:
                arguments = json.loads(entry["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            print(f"  [调用工具 {entry['name']}({json.dumps(arguments, ensure_ascii=False)})]")
            result = execute_tool(entry["name"], arguments)
            print(f"  [工具结果] {result[:200]}")
            messages.append(
                {"role": "tool", "tool_call_id": entry["id"], "content": result}
            )
