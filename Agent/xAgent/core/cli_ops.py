"""命令行执行工具(RunCli):让模型可以调用本地已安装的任意命令行程序。

对应工程需求:
1. 入参为 程序名 command + 参数列表 args;用 subprocess 以"列表形式"执行,
   shell=False,禁止直接拼接 shell 字符串,防止注入风险;
2. 执行结束后把 标准输出 stdout、标准错误 stderr、退出码 exitcode
   一并返回给大模型,方便 LLM 判断执行结果、规划后续动作;
3. 带执行超时控制(默认 30 秒),超时强制终止;
   对过长的输出做截断,并在结果中明确标注"已截断";
4. 安全说明:本工具面向受控教学实验环境,**暂不实现**命令白名单/黑名单/审批机制,
   命令以启动 xAgent 的当前用户权限运行。请只在受控环境使用,
   不要把它暴露给不可信的输入源。

扩展:照本文件的样子,在别的模块里写一个新工具并 register 即可。
"""
from __future__ import annotations

import json
import subprocess
from typing import Optional

from .text_utils import decode_bytes as _decode, truncate_text as _truncate
from .tools import Tool, ToolRegistry

# 单次命令执行的最长秒数(超时会被强制终止)
DEFAULT_TIMEOUT_SECONDS = 30.0
# stdout / stderr 各自的最大保留字符数(超出部分截断,防止撑爆上下文)
DEFAULT_MAX_OUTPUT_CHARS = 4000


def register_cli_ops(
    registry: ToolRegistry,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> None:
    """把命令行工具 RunCli 注册进工具注册中心。

    :param registry: 目标工具注册中心
    :param timeout_seconds: 单次命令执行超时时间(秒)
    :param max_output_chars: stdout/stderr 各自的最大保留字符数
    """

    # ---- 工具实现:执行一条本地命令 ----
    def run_cli(command: str, args: Optional[list] = None) -> str:
        """执行本地命令行程序,返回 exitcode + stdout + stderr。

        :param command: 程序名,如 "ping"、"python"、"git"
        :param args: 参数列表(按顺序),如 ["127.0.0.1", "-n", "1"]
        """
        # 1) 参数校验:command 必须是合法程序名
        if not isinstance(command, str) or not command.strip():
            return "错误:参数 command 必须是程序名(非空字符串),如 ping、python、git"

        # 2) 参数校验:args 必须是列表;每个元素统一转成字符串
        if args is None:
            args = []
        if not isinstance(args, list):
            return "错误:参数 args 必须是列表,如 [\"-n\", \"1\"];没有参数请传 []"

        # 3) 组装成"列表形式"调用 —— 不经 shell,不拼字符串,杜绝注入
        cmd_parts = [command, *(str(a) for a in args)]

        # 4) 执行命令(带超时)
        try:
            proc = subprocess.run(
                cmd_parts,
                shell=False,           # 关键安全点:绝不经过 shell 解析
                capture_output=True,   # 捕获 stdout/stderr(二进制,稍后统一解码)
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            return (
                f"错误:找不到程序 {command!r}。"
                "请确认程序已安装,且该命令在启动 xAgent 的终端环境(PATH)中可用。"
            )
        except subprocess.TimeoutExpired as exc:
            # 超时:把超时前已有的部分输出也一并返回,帮助模型了解执行到哪了
            partial = _decode(exc.stdout or b"")
            note = f"错误:命令执行超过 {timeout_seconds} 秒,已被强制终止。"
            if partial:
                note += "\n超时前的部分输出:\n" + _truncate(partial, max_output_chars)
            return note
        except Exception as exc:  # noqa: BLE001 —— 统一转成文本返回,不中断对话
            return f"错误:命令执行失败:{exc}"

        # 5) 组装返回结果:退出码 + 标准输出 + 标准错误
        stdout = _truncate(_decode(proc.stdout or b""), max_output_chars)
        stderr = _truncate(_decode(proc.stderr or b""), max_output_chars)

        lines = [
            f"命令:{json.dumps(cmd_parts, ensure_ascii=False)}",
            f"exitcode = {proc.returncode}",
            "",
            "[stdout]",
            stdout if stdout else "(空)",
            "",
            "[stderr]",
            stderr if stderr else "(空)",
        ]
        return "\n".join(lines)

    # ---- 注册:模型能看到的只有 name / description / parameters ----
    registry.register(
        Tool(
            # 工具名按需求命名为 RunCli(Python 函数名遵循 Python 惯例用 run_cli)
            name="RunCli",
            description=(
                "执行本地已安装的任意命令行程序,返回退出码 exitcode、标准输出 stdout "
                "和标准错误 stderr。适合查询系统信息(如 ping、ipconfig)、"
                f"运行本地脚本等。单次执行最长 {timeout_seconds} 秒,"
                "输出过长会被截断(结果中会标注)。"
                "注意:Windows 的 dir、copy 等 cmd 内置命令不能直接运行,"
                "请改用 powershell -Command 或 python 脚本实现。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的程序名称,如 ping、python、git、ipconfig",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "按顺序传给程序的参数列表,如 [\"127.0.0.1\", \"-n\", \"1\"];"
                            "没有参数时传 []"
                        ),
                    },
                },
                "required": ["command"],
            },
            handler=run_cli,
        )
    )
