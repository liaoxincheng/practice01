"""路径与文件读取的公共辅助。"""
import os

MAX_READ_BYTES = 200 * 1024

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".py", ".pyw", ".js", ".ts", ".json", ".ini", ".cfg",
    ".conf", ".yml", ".yaml", ".toml", ".xml", ".html", ".htm", ".css", ".csv", ".tsv",
    ".log", ".bat", ".cmd", ".sh", ".ps1", ".c", ".h", ".cpp", ".hpp", ".java", ".go",
    ".rs", ".rb", ".php", ".sql", ".mmd", ".gitignore", ".env", ".properties", ".tcss",
}


def resolve(path):
    """把用户给的路径变成绝对路径。相对路径一律相对当前工作目录。"""
    path = str(path).strip().strip('"').strip("'")
    if not path:
        path = "."
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    return os.path.normpath(path)


def human_size(n):
    """字节数转成可读的单位。"""
    for unit in ("字节", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "字节" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f GB" % n


def looks_textual(path):
    """靠扩展名判断是不是纯文本；没有扩展名时按二进制探测。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_SUFFIXES:
        return True
    if ext:
        return False
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(4096)
    except OSError:
        return False


def read_text(path):
    """读文本文件，依次尝试 UTF-8 和 GBK。

    返回 (文本, 编码, 附注)。文本为 None 时说明读不了，附注是原因。
    """
    with open(path, "rb") as f:
        raw = f.read(MAX_READ_BYTES + 1)
    truncated = len(raw) > MAX_READ_BYTES
    raw = raw[:MAX_READ_BYTES]
    if b"\x00" in raw:
        return None, None, "这个文件是二进制格式，不能按纯文本读取。"
    for enc in ("utf-8-sig", "gbk"):
        try:
            note = "内容超过 %s，只读取了前面部分" % human_size(MAX_READ_BYTES) if truncated else ""
            return raw.decode(enc), enc, note
        except UnicodeDecodeError:
            continue
    return None, None, "无法按 UTF-8 或 GBK 解码，可能不是纯文本文件。"
