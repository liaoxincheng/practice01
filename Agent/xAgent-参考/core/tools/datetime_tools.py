"""时间与历法工具。

getDate 走本地 date 命令（Windows 没有取时间的 Python 内置接口），
isLeapYear 走纯 Python 取模运算。两种实现方式形成对照。
"""
import os
import subprocess

from .registry import Tool

DEFAULT_FORMAT = "+%Y-%m-%d %H:%M:%S %A"

DATE_EXE_FALLBACKS = (
    r"D:\Git\usr\bin\date.exe",
    r"C:\Program Files\Git\usr\bin\date.exe",
    r"C:\Program Files (x86)\Git\usr\bin\date.exe",
    os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "usr", "bin", "date.exe"
    ),
)


def find_date():
    """在 PATH 和 Git 常见位置里找真正的 date 命令。

    Windows 自带的 date 是「修改日期」命令，不能用来取时间，
    所以优先用 Git 附带的 date.exe。
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


def run_command(cmd):
    """跑命令拿 stdout。先按 UTF-8 解，解不开再按 GBK 解。

    Git 的 date.exe 输出 UTF-8，cmd 的 date /t 输出 GBK，
    用默认编码统一解会把中文变成乱码。
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
    """调用本地 date 命令取当前日期时间。"""
    fmt = (fmt or DEFAULT_FORMAT).strip()
    if not fmt.startswith("+"):
        fmt = "+" + fmt
    exe = find_date()
    if exe:
        text = run_command([exe, fmt])
        if text:
            return text
    return run_command(["cmd", "/c", "date /t"]) + " " + run_command(["cmd", "/c", "time /t"])


def is_leap(year):
    """闰年判断：能被 4 整除且不能被 100 整除，或者能被 400 整除。"""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def is_leap_year(year=None):
    """算出闰年结果并拼成给模型看的文字。不传年份时用本机当前年份。"""
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


def build_tools():
    """返回时间与历法类的工具列表。"""
    return [
        Tool(
            name="getDate",
            description=(
                "获取当前这台电脑的本地日期和时间。内部通过调用本地 date 命令取得，"
                "返回命令输出的时间字符串。"
            ),
            func=lambda format=None: get_date(format),
            parameters={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "description": (
                            "可选，date 命令的格式化串，例如 +%Y-%m-%d %H:%M:%S。"
                            "不传则用 " + DEFAULT_FORMAT
                        ),
                    }
                },
            },
        ),
        Tool(
            name="isLeapYear",
            description=(
                "判断某个年份是不是闰年。凡涉及闰年、2 月有多少天、"
                "某年有 365 还是 366 天这类问题，都应该调用它，不要自己心算。"
                "规则：能被 4 整除且不能被 100 整除，或者能被 400 整除，就是闰年。"
            ),
            func=is_leap_year,
            parameters={
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": (
                            "要判断的年份，四位数字，例如 2024。用户问的是“今年”“当前年份”时，"
                            "不要猜年份，直接不传这个参数，由本机日期决定。"
                        ),
                    }
                },
            },
        ),
    ]
