# practice02(一):带 getDate 工具调用的流式对话程序(本地大模型)
# 用法:
#   python chat-stream-tool-getDate.py
# 输入 exit 或 退出 结束对话
#
# 与 chat.py(基础流式对话)的区别:
#   1. 模型除了回答文字,还可以主动"调用工具";
#   2. 本程序提供 1 个工具:getDate,获取当前电脑的日期和时间;
#   3. 课程要求:取时间必须调用本地命令,不能直接用 Python 的 datetime。
#      Windows 上没有 Unix 的 date 程序,最接近的本地命令是 PowerShell 的 Get-Date。
import configparser
import json
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


# ---------- 工具实现 ----------

def get_date(format: str = "%Y-%m-%d %H:%M:%S") -> str:
    """调用本机命令获取当前日期和时间。

    做法说明:
    - 命令本身是固定的(不拼接模型传来的参数),不存在命令注入风险;
    - 先用 Get-Date 取标准格式,再按调用者要求的格式在 Python 里重排,
      这样既满足"调用本地命令取时间",又能支持自定义格式。
    """
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
        # 命令输出不是预期格式:原样返回,至少让模型拿到时间信息
        return f"当前日期时间:{text}"
    return f"当前日期时间:{moment.strftime(format or '%Y-%m-%d %H:%M:%S')}"


# ---------- 工具说明(OpenAI function calling 格式,给模型看的"说明书") ----------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间。回答与日期、时间相关的问题前先调用它,不要自己编。",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "description": (
                            "输出格式(可选),用 Python strftime 写法,"
                            "默认 %Y-%m-%d %H:%M:%S,例如 %Y年%m月%d日 %H:%M"
                        ),
                    }
                },
            },
        },
    },
]


def execute_tool(name: str, arguments: dict) -> str:
    """执行模型要求的工具调用,返回执行结果字符串。"""
    if name == "getDate":
        return get_date(arguments.get("format") or "%Y-%m-%d %H:%M:%S")
    return f"错误:没有名为 {name} 的工具"


# ---------- 主循环 ----------

# 聊天历史:每次对话都带上,让模型记住上下文
messages = []

print("带 getDate 工具的大模型对话程序 [流式]")
print("模型可调用工具: getDate 获取当前日期时间")
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
