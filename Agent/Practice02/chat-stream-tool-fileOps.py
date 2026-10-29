# practice02: 带工具调用的流式对话程序(本地大模型 + 文件操作工具)
# 用法:
#   python chat-stream-tool-fileOps.py
# 输入 exit 或 退出 结束对话
#
# 与 practice01 的区别:
#   1. 模型除了回答文字,还可以主动"调用工具";
#   2. 本程序提供 3 个文件操作工具(fileOps),模型按需使用:
#        read_file  读取文件内容
#        write_file 把内容写入文件
#        list_dir   列出目录内容
#   3. 工具执行结果会送回模型,模型再继续回答(工具调用循环)。
import configparser
import json
import os
import sys

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


# ---------- 文件操作工具(给模型调用,模型只能通过工具说明了解它们) ----------

def read_file(path: str) -> str:
    """读取一个文本文件,返回文件内容。"""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if len(content) > 5000:
            return content[:5000] + f"\n...(文件太长,已截断,共 {len(content)} 字符)"
        return content
    except FileNotFoundError:
        return f"错误:文件不存在:{path}"
    except Exception as e:
        return f"错误:读取失败:{e}"


def write_file(path: str, content: str) -> str:
    """把内容写入文本文件(覆盖写),目录不存在时自动创建。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"成功:已写入 {path} ({len(content)} 字符)"
    except Exception as e:
        return f"错误:写入失败:{e}"


def list_dir(path: str = ".") -> str:
    """列出目录下的文件和文件夹。"""
    try:
        entries = sorted(os.listdir(path))
        if not entries:
            return "目录是空的"
        return "目录内容:\n" + "\n".join(f"- {e}" for e in entries)
    except Exception as e:
        return f"错误:列出失败:{e}"


# ---------- 工具说明(OpenAI function calling 格式,相当于给模型看的"说明书") ----------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取一个文本文件的内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读取的文件路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把内容写入文本文件(覆盖写)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要写入的文件路径"},
                    "content": {"type": "string", "description": "要写入的内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录下的文件和文件夹",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要列出的目录,默认当前目录"}
                },
            },
        },
    },
]


def execute_tool(name: str, arguments: dict) -> str:
    """执行模型要求的工具调用,返回执行结果字符串。"""
    if name == "read_file":
        return read_file(arguments.get("path", ""))
    if name == "write_file":
        return write_file(arguments.get("path", ""), arguments.get("content", ""))
    if name == "list_dir":
        return list_dir(arguments.get("path", "."))
    return f"错误:没有名为 {name} 的工具"


# ---------- 主循环 ----------

# 聊天历史:每次对话都带上,让模型记住上下文
messages = []

print("带文件工具的大模型对话程序 [流式]")
print("模型可调用工具: read_file 读文件 / write_file 写文件 / list_dir 列目录")
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
