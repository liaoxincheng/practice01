"""本地文件操作工具，共 10 个。

全部用 Python 标准库实现（os / os.path / shutil / mimetypes），
不调用系统命令 —— 命令行在这类任务上只会让代码更长更脆。
"""
import mimetypes
import os
import shutil

from .fsutil import human_size, looks_textual, read_text, resolve
from .registry import Tool


def get_current_directory():
    return "当前工作目录：%s" % os.getcwd()


def list_directory(path=None, recursive=False):
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


def get_file_type(path=None):
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
        % (
            target,
            ext,
            mime or "未知",
            size,
            "是，可以用 readFile 读取" if textual else "否，不能按纯文本读取",
        )
    )


def read_file(path=None, max_lines=200):
    if not path:
        return "缺少 path 参数，要读取哪个文件？"
    target = resolve(path)
    if not os.path.exists(target):
        return "文件不存在：%s" % target
    if os.path.isdir(target):
        return "这是目录不是文件：%s（要看里面有什么请用 listDirectory）" % target
    if not looks_textual(target):
        return "不支持读取：%s 不是纯文本文件。" % target

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
        tail = "\n…… 共 %d 行，只显示了前 %d 行，需要更多可以调大 max_lines。" % (
            len(lines),
            len(shown),
        )
    header = "文件 %s（共 %d 行，编码 %s）%s\n" % (
        target,
        len(lines),
        enc,
        "，" + note if note else "",
    )
    return header + "---\n" + "\n".join(shown) + tail


def create_file(path=None, content="", overwrite=False):
    if not path:
        return "缺少 path 参数，要创建哪个文件？"
    target = resolve(path)
    if os.path.isdir(target):
        return "创建失败：%s 已经是一个目录了。" % target
    if os.path.exists(target) and not overwrite:
        return (
            "创建失败：文件已存在 %s。要修改内容请用 editFile；"
            "确实要覆盖就再调用一次并把 overwrite 设为 true。" % target
        )
    parent = os.path.dirname(target)
    try:
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(content or "")
    except OSError as e:
        return "创建失败：%s" % e
    return "已创建文件：%s（%s）" % (target, human_size(os.path.getsize(target)))


def edit_file(path=None, content="", mode="overwrite"):
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
        # 模型经常把「改完之后的完整内容」当成要追加的内容传进来，
        # 导致原来的行被重复写一遍，这里检测并剥掉重复的前缀。
        try:
            existing, _, _ = read_text(target)
        except OSError:
            existing = ""
        if existing and content.startswith(existing):
            content = content[len(existing):]
            note = "（已自动去掉与原文重复的部分）"
        # 原文件末尾没有换行时直接接上去会和最后一行粘在一起。
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


def create_directory(path=None):
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


def delete_directory(path=None):
    if not path:
        return "缺少 path 参数，要删除哪个目录？"
    target = resolve(path)
    if not os.path.exists(target):
        return "删除失败：目录不存在 %s。" % target
    if not os.path.isdir(target):
        return "删除失败：%s 是文件不是目录，删单个文件请用 deleteFile。" % target
    try:
        count = sum(len(fs) + len(ds) for _, ds, fs in os.walk(target))
        shutil.rmtree(target)
    except OSError as e:
        return "删除失败：%s" % e
    return "已删除目录：%s（连带 %d 个文件和子目录）" % (target, count)


def delete_file(path=None):
    if not path:
        return "缺少 path 参数，要删除哪个文件？"
    target = resolve(path)
    if not os.path.exists(target):
        return "删除失败：文件不存在 %s。" % target
    if os.path.isdir(target):
        return "删除失败：%s 是目录不是文件，删目录请用 deleteDirectory。" % target
    try:
        size = human_size(os.path.getsize(target))
        os.remove(target)
    except OSError as e:
        return "删除失败：%s" % e
    return "已删除文件：%s（原来 %s）" % (target, size)


def rename_path(old_path=None, new_path=None):
    if not old_path or not new_path:
        return "缺少参数：old_path 和 new_path 都要给。"
    src = resolve(old_path)
    dst = resolve(new_path)
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


def _path_prop(desc):
    return {"type": "string", "description": desc}


def build_tools():
    """返回 10 个文件操作工具。"""
    return [
        Tool(
            name="getCurrentDirectory",
            description=(
                "获取程序当前的工作目录（绝对路径）。想知道“我现在在哪个目录”时调用它。"
                "注意它只回答当前位置，不列出目录里有什么文件；要看内容请用 listDirectory。"
            ),
            func=get_current_directory,
        ),
        Tool(
            name="listDirectory",
            description=(
                "列出指定目录下的所有文件和子目录，返回每项的名称、类型（文件还是目录）、大小。"
                "要查看某个目录里有什么内容时调用它。"
            ),
            func=list_directory,
            parameters={
                "type": "object",
                "properties": {
                    "path": _path_prop(
                        "要列出的目录路径，绝对或相对当前工作目录均可。不传则列当前目录。"
                    ),
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归列出所有层级，默认 false 只列一层。",
                    },
                },
            },
        ),
        Tool(
            name="getFileType",
            description=(
                "获取指定文件的类型信息，返回按扩展名识别的类型、MIME 类型、"
                "以及它是不是纯文本文件。读文件内容前可以先用它确认能不能按文本读。"
            ),
            func=get_file_type,
            parameters={
                "type": "object",
                "properties": {"path": _path_prop("要判断类型的文件路径。")},
            },
            required=["path"],
        ),
        Tool(
            name="readFile",
            description=(
                "读取指定文件的内容。仅支持纯文本格式（源码、markdown、配置、json、csv 等），"
                "遇到图片、二进制文件会拒绝读取。"
            ),
            func=read_file,
            parameters={
                "type": "object",
                "properties": {
                    "path": _path_prop("要读取的文件路径。"),
                    "max_lines": {
                        "type": "integer",
                        "description": "最多返回多少行，默认 200 行。",
                    },
                },
            },
            required=["path"],
        ),
        Tool(
            name="createFile",
            description=(
                "创建一个新的纯文本文件并写入初始内容。文件已存在时默认报错不覆盖，"
                "确实要覆盖请把 overwrite 设为 true。"
            ),
            func=create_file,
            parameters={
                "type": "object",
                "properties": {
                    "path": _path_prop("要创建的文件的完整路径，父目录不存在会自动创建。"),
                    "content": {"type": "string", "description": "文件初始内容，不传则建空文件。"},
                    "overwrite": {
                        "type": "boolean",
                        "description": "文件已存在时是否覆盖，默认 false。",
                    },
                },
            },
            required=["path"],
        ),
        Tool(
            name="editFile",
            description=(
                "修改已存在的纯文本文件。mode 为 overwrite 时整体替换成 content（默认），"
                "为 append 时把 content 追加到末尾。文件不存在请改用 createFile。"
            ),
            func=edit_file,
            parameters={
                "type": "object",
                "properties": {
                    "path": _path_prop("要修改的文件路径。"),
                    "content": {"type": "string", "description": "新内容，或要追加的内容。"},
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "overwrite 覆盖整个文件（默认），append 追加到末尾。",
                    },
                },
            },
            required=["path", "content"],
        ),
        Tool(
            name="createDirectory",
            description="创建一个新目录，父目录不存在时一并创建（相当于 mkdir -p）。",
            func=create_directory,
            parameters={
                "type": "object",
                "properties": {"path": _path_prop("要创建的目录路径。")},
            },
            required=["path"],
        ),
        Tool(
            name="deleteDirectory",
            description=(
                "删除一个目录，非空也会连里面的所有内容一起删掉。"
                "这是不可恢复的破坏性操作，调用前务必确认路径正确。"
            ),
            func=delete_directory,
            parameters={
                "type": "object",
                "properties": {"path": _path_prop("要删除的目录路径。")},
            },
            required=["path"],
        ),
        Tool(
            name="deleteFile",
            description=(
                "删除一个文件。这是不可恢复的破坏性操作，调用前务必确认路径正确。"
                "要删整个目录请用 deleteDirectory。"
            ),
            func=delete_file,
            parameters={
                "type": "object",
                "properties": {"path": _path_prop("要删除的文件路径。")},
            },
            required=["path"],
        ),
        Tool(
            name="renamePath",
            description=(
                "重命名文件或目录，也可以用它移动文件"
                "（新路径和原路径不在同一目录时就等于移动）。"
            ),
            func=rename_path,
            parameters={
                "type": "object",
                "properties": {
                    "old_path": _path_prop("原来的路径。"),
                    "new_path": _path_prop(
                        "新的路径。只给名字就改在原来所在目录；给目录就移动到那个目录。"
                    ),
                },
            },
            required=["old_path", "new_path"],
        ),
    ]
