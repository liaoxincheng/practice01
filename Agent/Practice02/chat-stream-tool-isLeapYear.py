import json
import os
import re
import subprocess
import sys
import urllib.error
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
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断某个年份是不是闰年。凡涉及闰年、2 月有多少天、某年有 365 还是 366 天这类问题，都应该调用它，不要自己心算。规则：能被 4 整除且不能被 100 整除，或者能被 400 整除，就是闰年。",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份，四位数字，例如 2024。用户问的是“今年”“当前年份”时，不要猜年份，直接不传这个参数，由本机日期决定；不确定年份时也先不传。",
                    }
                },
                "required": [],
            },
        },
    },
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
    """跑命令拿 stdout。先按 UTF-8 解，解不开再按 GBK 解。
    Git 的 date.exe 输出是 UTF-8，Windows 自带的 date /t 输出是 GBK，
    统一用默认编码解会把中文变成乱码，所以这里自己按字节解。
    """
    out = subprocess.run(cmd, capture_output=True)
    data = out.stdout
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").strip()


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


def is_leap(year):
    """闰年判断：能被 4 整除且不能被 100 整除，或者能被 400 整除。"""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def is_leap_year(year=None):
    """用 Python 代码算出闰年结果，再拼成给模型看的文字。"""
    if year is None or str(year).strip() == "":
        year = get_date("+%Y")
    try:
        year = int(str(year).strip())
    except ValueError:
        return "年份不合法：%s，请传四位数字的年份，例如 2024。" % year
    if year < 1:
        return "年份不合法：%s，请传四位数字的年份，例如 2024。" % year
    if is_leap(year):
        return "%d 年是闰年，2 月有 29 天，全年 366 天。" % year
    return "%d 年不是闰年，2 月有 28 天，全年 365 天。" % year


def normalize_args(name, args, said=""):
    """把模型给的参数校正成真正要用的参数，日志和执行都用这一份，避免显示与实际不一致。"""
    if name != "isLeapYear":
        return args
    args = dict(args)
    # 用户这句话里没提到年份时，模型容易凭先验猜一个（比如 2024），
    # 这种情况一律按本机当前年份算，避免答错。
    if not re.search(r"\d{3,4}", said):
        args["year"] = int(get_date("+%Y"))
    return args


def call_tool(name, args):
    if name == "getDate":
        return get_date(args.get("format"))
    if name == "isLeapYear":
        return is_leap_year(args.get("year"))
    return "未知工具：" + name


history = []
while True:
    try:
        prompt = input("你：")
    except (EOFError, KeyboardInterrupt):
        break
    if not prompt.strip():
        continue
    history.append({"role": "user", "content": prompt})
    base = len(history) - 1  # 这一轮开始前的历史长度，出错时回滚到这里
    print("---")

    for _ in range(5):
        body = json.dumps(
            {"model": cfg["model"], "messages": history, "stream": True, "tools": TOOLS}
        ).encode("utf-8")
        try:
            resp = urllib.request.urlopen(
                urllib.request.Request(
                    cfg["base_url"].rstrip("/") + "/chat/completions", body, headers
                )
            )
        except urllib.error.URLError as e:
            # 服务没起来时给一句人话，不要甩一屏异常栈
            print("连不上模型服务 %s" % cfg["base_url"])
            print("原因：%s" % e.reason)
            print("先确认服务已启动：netstat -ano | findstr :11437")
            del history[base:]
            break

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
            args = normalize_args(slot["name"], args, prompt)
            print("[tool] %s(%s)" % (slot["name"], json.dumps(args, ensure_ascii=False)))
            result = call_tool(slot["name"], args)
            print("[tool] -> " + result)
            history.append({"role": "tool", "tool_call_id": slot["id"], "content": result})
