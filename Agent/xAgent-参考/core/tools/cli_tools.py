"""命令行执行工具：让 Agent 能调本机任意 CLI 程序。

用 subprocess 以参数列表方式执行，shell=False，不拼接命令字符串。
程序名和参数分开传，参数里的 & | > ; 等符号不会被 shell 解释。

返回值统一带上退出码、stdout、stderr，超长输出会截断并标明。
"""
import os
import shutil
import subprocess

from .registry import Tool

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120
MAX_OUTPUT_CHARS = 4000


def decode_output(raw):
    """把命令输出解码成文本。

    Windows 命令的编码不统一：有些输出 UTF-8，有些是 GBK（系统代码页 936）。
    依次尝试，都不行就宽松解码，不抛异常。
    """
    if not raw:
        return ""
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def truncate(text, limit=MAX_OUTPUT_CHARS):
    """超长输出截断，返回 (文本, 是否截断, 原始长度)。"""
    if len(text) <= limit:
        return text, False, len(text)
    return text[:limit].rstrip(), True, len(text)


def resolve_program(command):
    """确认程序能不能找到。返回 (可执行路径或原名, 错误信息)。"""
    # 带路径的直接看文件在不在
    if os.sep in command or "/" in command:
        if os.path.isfile(command):
            return command, None
        return None, "找不到这个程序：%s" % command

    found = shutil.which(command)
    if found:
        return found, None

    # 给点提示，帮模型自己纠正
    common = ["cmd", "powershell", "python", "pip", "git", "dir", "curl", "node", "npm"]
    return None, (
        "找不到程序「%s」，它可能没有安装，或者不在 PATH 里。"
        "常见可用的有：%s" % (command, "、".join(common))
    )


def normalize_args(args):
    """把 args 规范成字符串列表。

    模型有时会把参数写成字符串（比如 "-la"），这里当成单个参数，
    不按空格切分 —— 一旦切分就等于在做命令拼接，容易出错。
    """
    if args is None:
        return [], None

    if isinstance(args, str):
        return [args], None

    if isinstance(args, (list, tuple)):
        out = []
        for item in args:
            if item is None:
                continue
            out.append(str(item))
        return out, None

    return None, "args 必须是字符串数组，例如 [\"-la\", \"/tmp\"]。收到的是 %s" % type(args).__name__


def format_result(command, args, code, stdout, stderr, timed_out=False, truncated=None):
    """把执行结果拼成给模型看的文本。

    结构化输出，让模型能清楚判断命令成没成功。
    """
    shown = " ".join([command] + list(args))
    lines = ["命令：%s" % shown]
    lines.append("退出码：%s" % ("超时终止" if timed_out else code))

    if timed_out:
        lines.append("说明：命令执行超时，已被终止。")
    elif code == 0:
        lines.append("说明：执行成功。")
    else:
        lines.append("说明：执行失败，请根据 stderr 判断原因。")

    out_text, out_cut, out_len = truncated["stdout"]
    err_text, err_cut, err_len = truncated["stderr"]

    lines.append("")
    lines.append("--- 标准输出 ---")
    if out_text.strip():
        lines.append(out_text)
        if out_cut:
            lines.append("……（输出过长已截断，原始长度 %d 字符，只显示前 %d 字符）"
                         % (out_len, MAX_OUTPUT_CHARS))
    else:
        lines.append("（空）")

    lines.append("")
    lines.append("--- 标准错误 ---")
    if err_text.strip():
        lines.append(err_text)
        if err_cut:
            lines.append("……（输出过长已截断，原始长度 %d 字符，只显示前 %d 字符）"
                         % (err_len, MAX_OUTPUT_CHARS))
    else:
        lines.append("（空）")

    return "\n".join(lines)


def run_cli(command=None, args=None, timeout=DEFAULT_TIMEOUT):
    """执行一个命令行程序。

    command 是程序名（如 git、python、dir），args 是参数列表。
    只在本机当前用户权限下执行，不做白名单过滤。
    """
    if not command or not str(command).strip():
        return "缺少 command 参数。请给出要执行的程序名，例如 \"dir\" 或 \"python\"。"

    command = str(command).strip().strip('"').strip("'")
    if not command:
        return "command 不能为空。"

    arg_list, err = normalize_args(args)
    if err:
        return err

    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    if timeout < 1:
        timeout = DEFAULT_TIMEOUT
    if timeout > MAX_TIMEOUT:
        timeout = MAX_TIMEOUT

    program, err = resolve_program(command)
    if err:
        return err

    cmd = [program] + arg_list

    try:
        proc = subprocess.run(
            cmd,
            shell=False,             # 关键：不经 shell，无法被参数注入
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        # 超时也要把已有输出给模型，方便判断卡在哪
        out_raw = e.stdout or b""
        err_raw = e.stderr or b""
        truncated = {
            "stdout": truncate(decode_output(out_raw)),
            "stderr": truncate(decode_output(err_raw)),
        }
        return format_result(
            command, arg_list, None, "", "", timed_out=True, truncated=truncated
        )
    except FileNotFoundError:
        return "找不到程序「%s」。请确认它已安装并且在 PATH 里。" % command
    except PermissionError:
        return "没有权限执行「%s」。请换一个程序或检查文件权限。" % command
    except OSError as e:
        return "执行命令失败：%s: %s" % (type(e).__name__, e)

    stdout = decode_output(proc.stdout)
    stderr = decode_output(proc.stderr)

    truncated = {
        "stdout": truncate(stdout),
        "stderr": truncate(stderr),
    }

    return format_result(
        command, arg_list, proc.returncode, stdout, stderr, truncated=truncated
    )


def build_tools():
    """返回命令行执行类工具。"""
    return [
        Tool(
            name="RunCli",
            description=(
                "在本机执行一个命令行程序（CLI），返回标准输出、标准错误和退出码。"
                "command 传程序名（如 git、python、dir、curl），args 传参数数组（如 [\"--version\"]）。"
                "用参数数组而不是拼接完整命令字符串，参数里的等号、空格、路径都不需要转义。"
                "退出码 0 表示成功，非 0 表示失败，请看 stderr 判断原因。"
                "执行超时或输出过长时会截断，并会明确说明。"
            ),
            func=run_cli,
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的程序名，例如 \"dir\"、\"git\"、\"python\"、\"curl\"。不要带参数，参数放 args。",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "程序的参数列表，每个参数一个元素，例如 [\"--version\"] 或 "
                            "[\"-la\", \"./docs\"]。不需要引号包裹带空格的参数，整个参数作为一个元素传入。"
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "超时秒数，默认 30，最大 %d。"
                            "执行耗时长的命令（如安装、编译）时可以调大。" % MAX_TIMEOUT
                        ),
                    },
                },
            },
            required=["command"],
        ),
    ]
