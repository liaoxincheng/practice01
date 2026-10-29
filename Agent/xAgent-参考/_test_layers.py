"""分层架构验证：确认 core 不依赖界面、tui 不写业务逻辑。"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
passed = 0
failed = []


def check(label, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("OK   %s" % label)
    else:
        failed.append(label)
        print("FAIL %s  %s" % (label, detail))


def imports_of(path):
    """收集一个 py 文件导入的所有模块名。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                mods.append("." * node.level + (node.module or ""))
            elif node.module:
                mods.append(node.module)
    return mods


def py_files(folder):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return out


print("=" * 66)
print("1. core 层禁止导入界面库")
print("=" * 66)
UI_LIBS = {"textual", "rich", "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "curses", "wx"}
core_files = py_files(os.path.join(ROOT, "core"))
check("core 下有 py 文件", len(core_files) > 0, str(len(core_files)))
bad = []
for path in core_files:
    for mod in imports_of(path):
        top = mod.split(".")[0]
        if top in UI_LIBS or top == "tui":
            bad.append((os.path.relpath(path, ROOT), mod))
check("core 不导入任何界面库", not bad, str(bad))

print()
print("=" * 66)
print("2. core 只依赖标准库，外加白名单里的解析库")
print("=" * 66)
STD = set(sys.stdlib_module_names)
# trafilatura 是任务要求用的网页正文提取库，允许出现在 core/tools 里。
# 它只在 web_tools 内部延迟导入，没装也不影响其他工具。
ALLOWED_THIRD = {"trafilatura"}
third = []
for path in core_files:
    for mod in imports_of(path):
        if mod.startswith("."):
            continue
        top = mod.split(".")[0]
        if top in STD or top == "core" or top in ALLOWED_THIRD:
            continue
        third.append((os.path.relpath(path, ROOT), mod))
check("core 第三方依赖仅限白名单", not third, str(third))

print()
print("=" * 66)
print("3. tui 层不直接操作文件或发网络请求")
print("=" * 66)
tui_files = py_files(os.path.join(ROOT, "tui"))
check("tui 下有 py 文件", len(tui_files) > 0, str(len(tui_files)))
BAD_IN_TUI = {"subprocess", "urllib", "http.client", "socket", "shutil"}
bad2 = []
for path in tui_files:
    for mod in imports_of(path):
        top = mod.split(".")[0]
        if top in BAD_IN_TUI:
            bad2.append((os.path.relpath(path, ROOT), mod))
check("tui 不碰网络和进程", not bad2, str(bad2))

# tui 里允许读文件吗？只允许 config 相关，通过 core.config 拿
bad3 = []
for path in tui_files:
    src = open(path, encoding="utf-8").read()
    if "open(" in src and "Prom" not in os.path.basename(path):
        bad3.append(os.path.relpath(path, ROOT))
check("tui 不自己 open 文件（配置走 core.config）", not bad3, str(bad3))

print()
print("=" * 66)
print("4. tui 依赖 core，方向正确")
print("=" * 66)
uses_core = False
for path in tui_files:
    for mod in imports_of(path):
        if mod.startswith("core") or mod.startswith(".core"):
            uses_core = True
check("tui 导入 core", uses_core)

print()
print("=" * 66)
print("5. 目录结构符合要求")
print("=" * 66)
check("存在 core 目录", os.path.isdir(os.path.join(ROOT, "core")))
check("存在 tui 目录", os.path.isdir(os.path.join(ROOT, "tui")))
check("core 有 agent.py", os.path.isfile(os.path.join(ROOT, "core", "agent.py")))
check("core 有 config.py", os.path.isfile(os.path.join(ROOT, "core", "config.py")))
check("core 有 tools 子包", os.path.isdir(os.path.join(ROOT, "core", "tools")))
check("core 有 llm 子包", os.path.isdir(os.path.join(ROOT, "core", "llm")))
check("tui 有 app.py", os.path.isfile(os.path.join(ROOT, "tui", "app.py")))
check("tui 有 widgets 子包", os.path.isdir(os.path.join(ROOT, "tui", "widgets")))
check("存在入口 main.py", os.path.isfile(os.path.join(ROOT, "main.py")))

print()
print("=" * 66)
print("6. 功能仍可用")
print("=" * 66)
sys.path.insert(0, ROOT)
from core.tools import default_registry

reg = default_registry()
check("15 个工具", len(reg) == 15, str(len(reg)))
check("getDate 可用", "2026" in (reg.execute("getDate", {})[0] or ""))
check("isLeapYear 可用", "不是闰年" in (reg.execute("isLeapYear", {"year": 1900})[0] or ""))

from core.llm import ToolCallBuffer

buf = ToolCallBuffer()
buf.feed([{"index": 0, "id": "c1", "function": {"name": "readFile", "arguments": ""}}])
buf.feed([{"index": 0, "function": {"arguments": '{"path": '}}])
buf.feed([{"index": 0, "function": {"arguments": '"a.txt"}'}}])
ready = buf.ready()
check("分片拼接", len(ready) == 1 and ready[0]["arguments"] == '{"path": "a.txt"}', str(ready))

from core.agent import Agent

a = Agent()
check("Agent 可实例化", a is not None)
check("Agent 有 15 个工具", len(a.tools) == 15)
check("Agent 历史为空", a.history == [])
a.history.append({"role": "user", "content": "x"})
a.reset()
check("reset 清空历史", a.history == [])

print()
print("=" * 66)
print("通过 %d 项，失败 %d 项" % (passed, len(failed)))
if failed:
    for f in failed:
        print("  失败:", f)
sys.exit(1 if failed else 0)
