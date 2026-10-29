"""时间工具:get_date(当前日期时间)与 is_leap_year(闰年判断)。

对应课程 practice02 的两个时间工具,按 core 层的约定注册进 ToolRegistry:
- get_date:课程要求"调用本地命令"取时间而不是用 Python 直接读系统时间。
  Windows 上没有 Unix 的 date 程序,最接近的本地命令是 PowerShell 的 Get-Date;
  命令本身固定(不拼接模型传来的任何字符串),再用 Python 做格式重排,
  因此不存在命令注入风险。
- is_leap_year:按课程要求用 Python 代码计算,不传年份时用本机当前年份。
"""
from __future__ import annotations

import subprocess
from datetime import datetime

from .tools import Tool, ToolRegistry

# 取时间的超时(秒)
DATE_TIMEOUT_SECONDS = 10.0
# 默认输出格式(Python strftime 写法)
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def register_time_ops(registry: ToolRegistry) -> None:
    """把时间工具注册进工具注册中心。"""

    def get_date(format: str = DEFAULT_DATE_FORMAT) -> str:
        """调用本机命令获取当前日期时间,按 format 重排后返回。

        :param format: 输出格式(Python strftime 写法),默认 %Y-%m-%d %H:%M:%S
        """
        # PowerShell 的命令固定为 ISO 格式;格式化在 Python 里做,
        # 这样模型传入的 format 永远不会进入命令行,彻底避免注入
        try:
            proc = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'",
                ],
                shell=False,
                capture_output=True,
                timeout=DATE_TIMEOUT_SECONDS,
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
            # 输出不是预期格式:原样返回,至少让模型拿到时间信息
            return f"当前日期时间:{text}"

        fmt = (format or DEFAULT_DATE_FORMAT).strip() or DEFAULT_DATE_FORMAT
        try:
            return f"当前日期时间:{moment.strftime(fmt)}"
        except (ValueError, TypeError):
            return f"错误:format 不是合法的 strftime 格式:{format!r}"

    def is_leap_year(year: int = 0) -> str:
        """判断某年是否闰年;不传年份(0)时用本机当前年份。

        规则:能被 4 整除且不能被 100 整除,或能被 400 整除。
        """
        try:
            year = int(year or 0)
        except (TypeError, ValueError):
            return "错误:year 必须是整数年份,如 2024"
        if year <= 0:
            year = datetime.now().year
        leap = (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
        return f"{year} 年{'是' if leap else '不是'}闰年"

    registry.register(
        Tool(
            name="get_date",
            description=(
                "获取当前电脑的日期和时间(通过本机命令获取,不是模型编造)。"
                "回答日期/时间相关问题前先调用它。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "description": (
                            "输出格式(可选),Python strftime 写法,"
                            "默认 %Y-%m-%d %H:%M:%S"
                        ),
                    },
                },
            },
            handler=get_date,
        )
    )
    registry.register(
        Tool(
            name="is_leap_year",
            description="判断一个年份是不是闰年;不传年份时判断本机当前年份。",
            parameters={
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份,如 2024;不传则用当前年份",
                    },
                },
            },
            handler=is_leap_year,
        )
    )
