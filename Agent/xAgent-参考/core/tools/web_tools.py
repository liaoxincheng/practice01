"""网页抓取工具：用本地 curl 取 HTML，再用 trafilatura 抽正文转 Markdown。

curl 走 subprocess 参数列表方式（不用 shell=True），避免命令拼接被注入。
trafilatura 是可选依赖：没装就给出清晰的安装提示，不拖垮其他工具。
"""
import os
import shutil
import subprocess

from .registry import Tool

# 单次抓取上限，防止把超大页面整个塞进上下文
MAX_MARKDOWN_CHARS = 20000
DEFAULT_TIMEOUT = 30
CURL_TIMEOUT = 20

# curl 用 --fail 让 HTTP 4xx/5xx 返回非零，便于统一判错
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) xAgent/0.1"


def import_trafilatura():
    """延迟导入 trafilatura，返回 (模块, 错误信息)。"""
    try:
        import trafilatura
    except ImportError:
        return None, (
            "缺少依赖 trafilatura，无法提取网页正文。"
            "请先执行：pip install trafilatura"
        )
    return trafilatura, None


def find_curl():
    """找 curl 可执行文件。Windows 10 以上自带 curl.exe。"""
    found = shutil.which("curl")
    if found:
        return found
    for path in (r"C:\Windows\System32\curl.exe", r"C:\Windows\curl.exe"):
        if os.path.isfile(path):
            return path
    return None


def fetch_html(url, timeout=CURL_TIMEOUT):
    """调 curl 取 HTML。返回 (html, 错误信息, HTTP状态)。

    参数列表方式执行，不经 shell，URL 里的特殊字符不会被解释。
    """
    curl = find_curl()
    if not curl:
        return None, "系统里找不到 curl。Windows 10 及以上自带 curl.exe，请确认它在 PATH 中。", None

    cmd = [
        curl,
        "--location",           # 跟随重定向
        "--silent",             # 不输出进度
        "--show-error",         # 出错时给信息
        "--compressed",         # 要 gzip，省流量
        "--max-time", str(timeout),
        "--user-agent", UA,
        "--write-out", "\n__HTTP_CODE__%{http_code}",
        url,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout + 10,   # 比 curl 自己的超时略长
        )
    except FileNotFoundError:
        return None, "找不到 curl 可执行文件（%s）。" % curl, None
    except subprocess.TimeoutExpired:
        return None, "抓取超时（超过 %d 秒）：%s" % (timeout, url), None
    except OSError as e:
        return None, "调用 curl 失败：%s" % e, None

    raw = proc.stdout
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()

    # 拆出 --write-out 附加的状态码
    status = None
    marker = b"\n__HTTP_CODE__"
    idx = raw.rfind(marker)
    if idx >= 0:
        try:
            status = int(raw[idx + len(marker):].strip() or 0)
        except ValueError:
            status = None
        raw = raw[:idx]

    if proc.returncode != 0 and not raw:
        detail = stderr or "curl 返回码 %d" % proc.returncode
        if "timed out" in detail.lower() or proc.returncode == 28:
            return None, "抓取超时（超过 %d 秒）：%s" % (timeout, url), status
        if "could not resolve" in detail.lower() or "resolve host" in detail.lower():
            return None, "域名解析失败，请检查网址是否正确：%s" % url, status
        if "connection refused" in detail.lower() or "failed to connect" in detail.lower():
            return None, "连接被拒绝，目标服务器可能不可达：%s" % url, status
        return None, "抓取失败：%s" % detail, status

    if not raw:
        if status and status >= 400:
            return None, "服务器返回 HTTP %d：%s" % (status, url), status
        return None, "抓取到的内容为空：%s" % url, status

    if status and status >= 400:
        return None, "服务器返回 HTTP %d：%s" % (status, url), status

    return raw, None, status


def decode_html(raw):
    """把字节解成文本。先试 UTF-8，再试 GBK，最后宽松解码。"""
    if not raw:
        return ""
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def html_to_markdown(html, url=""):
    """用 trafilatura 抽正文并转 Markdown。返回 (markdown, 错误信息)。"""
    trafilatura, err = import_trafilatura()
    if err:
        return None, err

    try:
        text = trafilatura.extract(
            html,
            url=url or None,
            output_format="markdown",
            include_links=True,      # 保留链接
            include_tables=True,     # 保留表格
            include_formatting=True, # 保留标题、列表等结构
            include_comments=False,  # 去掉评论区
            favor_precision=True,    # 宁可少抓也不要广告噪音
        )
    except Exception as e:
        return None, "HTML 解析失败：%s: %s" % (type(e).__name__, e)

    if not text:
        return None, (
            "没能从页面里提取出正文。可能是纯图片页、动态渲染页（需要 JS 才出内容），"
            "或者正文被反爬机制挡住了。"
        )
    return text.strip(), None


def truncate(text, limit=MAX_MARKDOWN_CHARS):
    """超长内容截断，避免把上下文撑爆。"""
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "\n\n……（内容过长，已截断）", True


def fetch_web(url=None, max_chars=MAX_MARKDOWN_CHARS, timeout=DEFAULT_TIMEOUT):
    """抓一个网页，返回 Markdown 正文。"""
    if not url or not str(url).strip():
        return "缺少 url 参数。请给出要抓取的完整网址，例如 https://example.com。"

    url = str(url).strip().strip('"').strip("'")
    if not url.lower().startswith(("http://", "https://")):
        return "网址必须以 http:// 或 https:// 开头，收到的是：%s" % url

    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = MAX_MARKDOWN_CHARS
    if max_chars < 500:
        max_chars = 500

    raw, err, status = fetch_html(url, timeout=timeout)
    if err:
        return "抓取失败：%s" % err

    html = decode_html(raw)
    markdown, err = html_to_markdown(html, url)
    if err:
        return "提取正文失败：%s\n原始网址：%s" % (err, url)

    body, truncated = truncate(markdown, max_chars)

    parts = [
        "来源网址：%s" % url,
        "正文长度：%d 字符%s" % (len(markdown), "（已截断到 %d）" % max_chars if truncated else ""),
        "",
        "--- 正文（Markdown）---",
        body,
    ]
    return "\n".join(parts)


def build_tools():
    """返回网页抓取类工具。"""
    return [
        Tool(
            name="fetchWeb",
            description=(
                "抓取指定网页的主要内容。输入一个网址，返回网页正文的 Markdown 文本和原始网址。"
                "适合读文章、博客、文档页，会自动去掉导航栏、广告、页脚，保留标题、段落、列表、表格和链接。"
                "当用户给了一个网址、或者需要看某个页面的具体内容时，调用它。"
            ),
            func=fetch_web,
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的完整网址，必须以 http:// 或 https:// 开头。",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "正文最多返回多少字符，默认 20000。页面很长时可以调小。",
                    },
                },
            },
            required=["url"],
        ),
    ]
