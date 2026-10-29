import json
import os
import subprocess
import sys
import urllib.request

cfg = {}
for line in open("config.ini", encoding="utf-8"):
    if "=" in line:
        k, v = line.split("=", 1)
        cfg[k.strip().lower()] = v.strip()

headers = {"Content-Type": "application/json"}
if cfg.get("api_key"):
    headers["Authorization"] = "Bearer " + cfg["api_key"]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前这台电脑的本地日期和时间。内部通过调用本地 date 命令取得，返回命令输出的时间字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "description": "可选，date 命令的格式化串，例如 +%Y-%m-%d %H:%M:%S。不传则用 +%Y-%m-%d %H:%M:%S %A",
                    }
                },
                "required": [],
            },
        },
    }
]

DEFAULT_FORMAT = "+%Y-%m-%d %H:%M:%S %A"
DATE_EXE_FALLBACKS = (
    r"D:\Git\usr\bin\date.exe",
    r"C:\Program Files\Git\usr\bin\date.exe",
    r"C:\Program Files (x86)\Git\usr\bin\date.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "usr", "bin", "date.exe"),
)


def find_date():
    """在 PATH 和 Git 常见安装位置里找真正的 date 命令。
    Windows 自带的 date 是改日期的命令，不能用来取时间，所以优先用 Git 附带的 date.exe。
    """
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder:
            continue
        for name in ("date.exe", "date"):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    for path in DATE_EXE_FALLBACKS:
        if os.path.isfile(path):
            return path
    return None


def run(cmd):
    out = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return out.stdout.strip()


def get_date(fmt=None):
    """调用本地 date 命令取当前日期时间，不在 Python 里自己算时间。"""
    fmt = (fmt or DEFAULT_FORMAT).strip()
    if not fmt.startswith("+"):
        fmt = "+" + fmt
    exe = find_date()
    if exe:
        text = run([exe, fmt])
        if text:
            return text
    # 没装 Git 时退回 Windows 自带的 date / time 命令
    return run(["cmd", "/c", "date /t"]) + " " + run(["cmd", "/c", "time /t"])


history = []
while True:
    try:
        prompt = input("你：")
    except (EOFError, KeyboardInterrupt):
        break
    if not prompt.strip():
        continue
    history.append({"role": "user", "content": prompt})
    print("---")

    for _ in range(5):
        body = json.dumps(
            {"model": cfg["model"], "messages": history, "stream": True, "tools": TOOLS}
        ).encode("utf-8")
        resp = urllib.request.urlopen(
            urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions", body, headers)
        )

        answer = ""
        calls = {}
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            delta = json.loads(line[5:]).get("choices", [{}])[0].get("delta", {})
            piece = delta.get("content") or ""
            if piece:
                answer += piece
                sys.stdout.write(piece)
                sys.stdout.flush()
            for call in delta.get("tool_calls") or []:
                slot = calls.setdefault(
                    call.get("index", 0), {"id": "", "name": "", "arguments": ""}
                )
                if call.get("id"):
                    slot["id"] = call["id"]
                fn = call.get("function") or {}
                if fn.get("name") and not slot["name"]:
                    slot["name"] = fn["name"]
                slot["arguments"] += fn.get("arguments") or ""
        print()

        if not calls:
            history.append({"role": "assistant", "content": answer})
            break

        ordered = [calls[i] for i in sorted(calls)]
        history.append(
            {
                "role": "assistant",
                "content": answer or None,
                "tool_calls": [
                    {
                        "id": s["id"],
                        "type": "function",
                        "function": {"name": s["name"], "arguments": s["arguments"] or "{}"},
                    }
                    for s in ordered
                ],
            }
        )
        for slot in ordered:
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            print("[tool] %s(%s)" % (slot["name"], json.dumps(args, ensure_ascii=False)))
            if slot["name"] == "getDate":
                result = get_date(args.get("format"))
            else:
                result = "未知工具：" + slot["name"]
            print("[tool] -> " + result)
            history.append({"role": "tool", "tool_call_id": slot["id"], "content": result})
