import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

cfg = {}
# config.ini 先在当前工作目录找，找不到再找脚本所在目录。
# 这样在别的目录里操作文件时也能正常启动，配置跟着脚本走。
_cfg_path = "config.ini"
if not os.path.isfile(_cfg_path):
    _alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    if os.path.isfile(_alt):
        _cfg_path = _alt
for line in open(_cfg_path, encoding="utf-8"):
    if "=" in line:
        k, v = line.split("=", 1)
        cfg[k.strip().lower()] = v.strip()

headers = {"Content-Type": "application/json"}
if cfg.get("api_key"):
    headers["Authorization"] = "Bearer " + cfg["api_key"]


def _tool(name, desc, props=None, required=None):
    """拼一个工具声明，省得十份样板代码手写重复。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props or {},
                "required": required or [],
            },
        },
    }


def _path_prop(desc):
    return {"type": "string", "description": desc}


TOOLS = [
    _tool(
        "getDate",
        "获取当前这台电脑的本地日期和时间。内部通过调用本地 date 命令取得，返回命令输出的时间字符串。",
        {
            "format": {
                "type": "string",
                "description": "可选，date 命令的格式化串，例如 +%Y-%m-%d %H:%M:%S。不传则用 +%Y-%m-%d %H:%M:%S %A",
            }
        },
    ),
    _tool(
        "isLeapYear",
        "判断某个年份是不是闰年。凡涉及闰年、2 月有多少天、某年有 365 还是 366 天这类问题，都应该调用它，不要自己心算。规则：能被 4 整除且不能被 100 整除，或者能被 400 整除，就是闰年。",
        {
            "year": {
                "type": "integer",
                "description": "要判断的年份，四位数字，例如 2024。用户问的是“今年”“当前年份”时，不要猜年份，直接不传这个参数，由本机日期决定；不确定年份时也先不传。",
            }
        },
    ),
    _tool(
        "getCurrentDirectory",
        "获取程序当前的工作目录（绝对路径）。想知道“我现在在哪个目录”时调用它。注意它只回答当前位置，不列出目录里有什么文件；要看内容请用 listDirectory。",
    ),
    _tool(
        "listDirectory",
        "列出指定目录下的所有文件和子目录，返回每项的名称、类型（文件还是目录）、大小。要查看某个目录里有什么内容时调用它，不要说“我无法访问文件系统”。",
        {
            "path": _path_prop(
                "要列出的目录路径。可以是绝对路径，也可以是相对当前工作目录的路径。不传则列出当前工作目录。"
            ),
            "recursive": {
                "type": "boolean",
                "description": "是否递归列出所有层级的子目录内容，默认 false 只列一层。",
            },
        },
    ),
    _tool(
        "getFileType",
        "获取指定文件的类型信息，返回按扩展名识别的类型名称、MIME 类型、以及它是不是纯文本文件。在读取文件内容前，可以先用它确认文件能不能按文本读。",
        {"path": _path_prop("要判断类型的文件路径，绝对或相对路径均可。")},
        ["path"],
    ),
    _tool(
        "readFile",
        "读取指定文件的内容。仅支持纯文本格式文件（源码、markdown、配置、json、csv 等），遇到图片、二进制文件会拒绝读取。要查看某个文件里写了什么时调用它。",
        {
            "path": _path_prop("要读取的文件路径，绝对或相对路径均可。"),
            "max_lines": {
                "type": "integer",
                "description": "最多返回多少行，默认 200 行，避免内容过长。",
            },
        },
        ["path"],
    ),
    _tool(
        "createFile",
        "创建一个新的纯文本文件并写入初始内容。如果文件已经存在，默认不覆盖而是报错；确实要覆盖请把 overwrite 设为 true。要新建文件时用它，不要用 createDirectory。",
        {
            "path": _path_prop(
                "要创建的文件的完整路径，可以只给文件名（建在当前工作目录），也可以带目录。父目录不存在时会自动创建。"
            ),
            "content": {
                "type": "string",
                "description": "文件初始内容，纯文本。不传则创建空文件。",
            },
            "overwrite": {
                "type": "boolean",
                "description": "文件已存在时是否覆盖，默认 false 会报错。",
            },
        },
        ["path"],
    ),
    _tool(
        "editFile",
        "修改一个已存在的纯文本文件的内容。mode 为 overwrite 时整个文件内容替换成 content（默认）；mode 为 append 时把 content 追加到文件末尾。文件不存在会报错，要新建请用 createFile。",
        {
            "path": _path_prop("要修改的文件路径，绝对或相对路径均可。"),
            "content": {"type": "string", "description": "新的内容，或要追加的内容。"},
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "description": "overwrite 覆盖整个文件（默认），append 追加到末尾。",
            },
        },
        ["path", "content"],
    ),
    _tool(
        "createDirectory",
        "创建一个新目录，父目录不存在时会一并创建（相当于 mkdir -p）。要建文件夹时用它，不要用 createFile。",
        {"path": _path_prop("要创建的目录路径，绝对或相对路径均可。")},
        ["path"],
    ),
    _tool(
        "deleteDirectory",
        "删除一个目录，即使目录非空也会连同里面的所有文件和子目录一起删掉。这是不可恢复的破坏性操作，调用前务必确认路径正确。只删单个文件请用 deleteFile。",
        {"path": _path_prop("要删除的目录路径，绝对或相对路径均可。")},
        ["path"],
    ),
    _tool(
        "deleteFile",
        "删除一个文件。这是不可恢复的破坏性操作，调用前务必确认路径正确。要删整个目录请用 deleteDirectory。",
        {"path": _path_prop("要删除的文件路径，绝对或相对路径均可。")},
        ["path"],
    ),
    _tool(
        "renamePath",
        "重命名文件或目录，也可以用它来移动文件（新路径和原路径不在同一个目录时就等于移动）。原路径和目标路径都可以是绝对或相对路径。",
        {
            "old_path": _path_prop("原来的路径，绝对或相对路径均可。"),
            "new_path": _path_prop(
                "新的路径。如果只给新名字，会改在原来所在的目录里；如果给了目录，就移动到那个目录。"
            ),
        },
        ["old_path", "new_path"],
    ),
]

DEFAULT_FORMAT = "+%Y-%m-%d %H:%M:%S %A"
DATE_EXE_FALLBACKS = (
    r"D:\Git\usr\bin\date.exe",
    r"C:\Program Files\Git\usr\bin\date.exe",
    r"C:\Program Files (x86)\Git\usr\bin\date.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "usr", "bin", "date.exe"),
)

MAX_READ_BYTES = 200 * 1024
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".py", ".pyw", ".js", ".ts", ".json", ".ini", ".cfg",
    ".conf", ".yml", ".yaml", ".toml", ".xml", ".html", ".htm", ".css", ".csv", ".tsv",
    ".log", ".bat", ".cmd", ".sh", ".ps1", ".c", ".h", ".cpp", ".hpp", ".java", ".go",
    ".rs", ".rb", ".php", ".sql", ".mmd", ".gitignore", ".env", ".properties",
}


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


def resolve(path):
    """把模型给的路径变成绝对路径。相对路径一律相对当前工作目录，不相对脚本位置。"""
    path = str(path).strip().strip('"').strip("'")
    if not path:
        path = "."
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    return os.path.normpath(path)


def human_size(n):
    for unit in ("字节", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "字节" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f GB" % n


def looks_textual(path):
    """靠扩展名判断是不是纯文本文件。没有扩展名时按二进制探测。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_SUFFIXES:
        return True
    if ext:
        return False
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)
        return b"\x00" not in chunk
    except OSError:
        return False


def read_text(path):
    """读文本文件，依次尝试 UTF-8、GBK。返回 (文本, 编码, 错误信息)。"""
    with open(path, "rb") as f:
        raw = f.read(MAX_READ_BYTES + 1)
    truncated = len(raw) > MAX_READ_BYTES
    raw = raw[:MAX_READ_BYTES]
    if b"\x00" in raw:
        return None, None, "这个文件是二进制格式，不能按纯文本读取。"
    for enc in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(enc), enc, "内容超过 %s，只读取了前面部分" % human_size(MAX_READ_BYTES) if truncated else ""
        except UnicodeDecodeError:
            continue
    return None, None, "无法按 UTF-8 或 GBK 解码，可能不是纯文本文件。"


def tool_get_current_directory():
    return "当前工作目录：%s" % os.getcwd()


def tool_list_directory(path=None, recursive=False):
    target = resolve(path or ".")
    if not os.path.exists(target):
        return "目录不存在：%s" % target
    if not os.path.isdir(target):
        return "这不是目录，是文件：%s（要看文件内容请用 readFile）" % target

    entries = []
    if recursive:
        for root, dirs, files in os.walk(target):
            dirs.sort()
            files.sort()
            rel = os.path.relpath(root, target)
            prefix = "" if rel == "." else rel + os.sep
            for d in dirs:
                entries.append("  [目录] %s%s" % (prefix, d))
            for f in files:
                full = os.path.join(root, f)
                try:
                    size = human_size(os.path.getsize(full))
                except OSError:
                    size = "未知大小"
                entries.append("  [文件] %s%s (%s)" % (prefix, f, size))
    else:
        for name in sorted(os.listdir(target)):
            full = os.path.join(target, name)
            if os.path.isdir(full):
                entries.append("  [目录] %s" % name)
            else:
                try:
                    size = human_size(os.path.getsize(full))
                except OSError:
                    size = "未知大小"
                entries.append("  [文件] %s (%s)" % (name, size))

    if not entries:
        return "目录 %s 是空的。" % target
    return "目录 %s 共 %d 项：\n%s" % (target, len(entries), "\n".join(entries))


def tool_get_file_type(path=None):
    if not path:
        return "缺少 path 参数，要判断哪个文件的类型？"
    target = resolve(path)
    if not os.path.exists(target):
        return "文件不存在：%s" % target
    if os.path.isdir(target):
        return "%s 是一个目录，不是文件。" % target

    ext = os.path.splitext(target)[1].lower() or "（无扩展名）"
    mime, _ = mimetypes.guess_type(target)
    textual = looks_textual(target)
    try:
        size = human_size(os.path.getsize(target))
    except OSError:
        size = "未知大小"
    return (
        "文件：%s\n扩展名：%s\nMIME 类型：%s\n大小：%s\n是否纯文本：%s"
        % (target, ext, mime or "未知", size, "是，可以用 readFile 读取" if textual else "否，不能按纯文本读取")
    )


def tool_read_file(path=None, max_lines=200):
    if not path:
        return "缺少 path 参数，要读取哪个文件？"
    target = resolve(path)
    if not os.path.exists(target):
        return "文件不存在：%s" % target
    if os.path.isdir(target):
        return "这是目录不是文件：%s（要看里面有什么请用 listDirectory）" % target
    if not looks_textual(target):
        return "不支持读取：%s 不是纯文本文件（只支持源码、markdown、配置、json、csv 等文本格式）。" % target

    try:
        text, enc, note = read_text(target)
    except OSError as e:
        return "读取失败：%s" % e
    if text is None:
        return "读取失败：%s" % note

    lines = text.splitlines()
    try:
        limit = int(max_lines)
    except (TypeError, ValueError):
        limit = 200
    if limit < 1:
        limit = 200
    shown = lines[:limit]
    tail = ""
    if len(lines) > len(shown):
        tail = "\n…… 共 %d 行，只显示了前 %d 行，需要更多可以调大 max_lines。" % (len(lines), len(shown))
    header = "文件 %s（共 %d 行，编码 %s）%s\n" % (
        target,
        len(lines),
        enc,
        "，" + note if note else "",
    )
    return header + "---\n" + "\n".join(shown) + tail


def tool_create_file(path=None, content="", overwrite=False):
    if not path:
        return "缺少 path 参数，要创建哪个文件？"
    target = resolve(path)
    if os.path.isdir(target):
        return "创建失败：%s 已经是一个目录了。" % target
    if os.path.exists(target) and not overwrite:
        return "创建失败：文件已存在 %s。要修改它的内容请用 editFile；确实要覆盖就再调用一次并把 overwrite 设为 true。" % target
    parent = os.path.dirname(target)
    try:
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(content or "")
    except OSError as e:
        return "创建失败：%s" % e
    return "已创建文件：%s（%s）" % (target, human_size(os.path.getsize(target)))


def tool_edit_file(path=None, content="", mode="overwrite"):
    if not path:
        return "缺少 path 参数，要修改哪个文件？"
    target = resolve(path)
    if not os.path.exists(target):
        return "修改失败：文件不存在 %s。要新建文件请用 createFile。" % target
    if os.path.isdir(target):
        return "修改失败：%s 是目录不是文件。" % target
    if not looks_textual(target):
        return "修改失败：%s 不是纯文本文件，不支持编辑。" % target

    mode = (mode or "overwrite").strip().lower()
    if mode not in ("overwrite", "append"):
        return "mode 只能是 overwrite 或 append，收到的是 %s。" % mode

    content = content or ""
    note = ""
    if mode == "append":
        # 模型经常把「改了之后的完整内容」当成要追加的内容传进来，
        # 导致原来的行被重复写一遍。这里检测并剥掉重复的前缀。
        try:
            existing, _, _ = read_text(target)
        except OSError:
            existing = ""
        if existing and content.startswith(existing):
            content = content[len(existing):]
            note = "（已自动去掉与原文重复的部分）"
        # 原文件结尾没有换行时，直接接上去会和最后一行粘在一起。
        # 用户说的是「追加一行」，所以这里补一个换行。
        if existing and not existing.endswith("\n") and content and not content.startswith("\n"):
            content = "\n" + content
            note += "（原文件末尾无换行，已自动补上）"

    try:
        with open(target, "a" if mode == "append" else "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        return "修改失败：%s" % e
    action = "追加到末尾" if mode == "append" else "整体覆盖"
    return "已修改文件：%s（%s%s，现在 %s）" % (
        target,
        action,
        note,
        human_size(os.path.getsize(target)),
    )


def tool_create_directory(path=None):
    if not path:
        return "缺少 path 参数，要创建哪个目录？"
    target = resolve(path)
    if os.path.isdir(target):
        return "目录已经存在：%s" % target
    if os.path.exists(target):
        return "创建失败：%s 已经是一个文件。" % target
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as e:
        return "创建失败：%s" % e
    return "已创建目录：%s" % target


def tool_delete_directory(path=None):
    if not path:
        return "缺少 path 参数，要删除哪个目录？"
    target = resolve(path)
    if not os.path.exists(target):
        return "删除失败：目录不存在 %s。" % target
    if not os.path.isdir(target):
        return "删除失败：%s 是文件不是目录，删除单个文件请用 deleteFile。" % target
    try:
        count = sum(len(fs) + len(ds) for _, ds, fs in os.walk(target))
        shutil.rmtree(target)
    except OSError as e:
        return "删除失败：%s" % e
    return "已删除目录：%s（连带 %d 个文件和子目录）" % (target, count)


def tool_delete_file(path=None):
    if not path:
        return "缺少 path 参数，要删除哪个文件？"
    target = resolve(path)
    if not os.path.exists(target):
        return "删除失败：文件不存在 %s。" % target
    if os.path.isdir(target):
        return "删除失败：%s 是目录不是文件，删除目录请用 deleteDirectory。" % target
    try:
        size = human_size(os.path.getsize(target))
        os.remove(target)
    except OSError as e:
        return "删除失败：%s" % e
    return "已删除文件：%s（原来 %s）" % (target, size)


def tool_rename_path(old_path=None, new_path=None):
    if not old_path or not new_path:
        return "缺少参数：old_path 和 new_path 都要给。"
    raw_new = str(new_path).strip()
    src = resolve(old_path)
    dst = resolve(raw_new)
    if not os.path.exists(src):
        return "重命名失败：原路径不存在 %s。" % src

    # 新路径指向一个已存在的目录时，等价于「把它移进去」，名字保持不变。
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))

    if os.path.exists(dst):
        return "重命名失败：目标已存在 %s，不覆盖以免误删。" % dst
    parent = os.path.dirname(dst)
    try:
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        shutil.move(src, dst)
    except OSError as e:
        return "重命名失败：%s" % e
    kind = "目录" if os.path.isdir(dst) else "文件"
    return "已重命名%s：%s -> %s" % (kind, src, dst)


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
    if name == "getCurrentDirectory":
        return tool_get_current_directory()
    if name == "listDirectory":
        return tool_list_directory(args.get("path"), args.get("recursive", False))
    if name == "getFileType":
        return tool_get_file_type(args.get("path"))
    if name == "readFile":
        return tool_read_file(args.get("path"), args.get("max_lines", 200))
    if name == "createFile":
        return tool_create_file(args.get("path"), args.get("content"), args.get("overwrite", False))
    if name == "editFile":
        return tool_edit_file(args.get("path"), args.get("content"), args.get("mode", "overwrite"))
    if name == "createDirectory":
        return tool_create_directory(args.get("path"))
    if name == "deleteDirectory":
        return tool_delete_directory(args.get("path"))
    if name == "deleteFile":
        return tool_delete_file(args.get("path"))
    if name == "renamePath":
        return tool_rename_path(args.get("old_path"), args.get("new_path"))
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

    for _ in range(12):
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
            print("[tool] %s(%s)" % (slot["name"], json.dumps(args, ensure_ascii=False)[:200]))
            result = call_tool(slot["name"], args)
            # 只打印第一行做摘要，结果太长会刷屏；完整内容已经回灌给模型了
            head = result.splitlines()[0][:160] if result else ""
            rest = len(result.splitlines()) - 1 if result else 0
            print("[tool] -> " + head + ("  …（共 %d 行）" % (rest + 1) if rest > 0 else ""))
            history.append({"role": "tool", "tool_call_id": slot["id"], "content": result})
