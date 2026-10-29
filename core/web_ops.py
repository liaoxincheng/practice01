"""联网工具(fetchWeb / searchWeb):让模型可以上网获取信息。

fetchWeb:
1. 接收一个网页 URL,用 subprocess 以"列表形式"调用本地 curl 下载 HTML
   (shell=False,URL 作为独立参数传给程序,杜绝 shell 注入);
2. 用 trafilatura 提取网页正文并转换为 Markdown —— 保留标题、段落、列表、
   表格及链接,去除导航栏、广告、页脚等噪声,节省模型上下文;
3. 处理网页访问失败、超时、HTML 解析失败、正文无法提取等异常情况,
   全部以"错误:……"开头的文本返回给模型,让模型能看懂并自己换方案。

searchWeb:
1. 接收搜索关键词,调用百度千帆 AI 搜索 API(web_search 接口),返回搜索摘要、
   参考来源的标题及 URL,整理成文本给模型;
2. API Key 从 config.ini 的 [search] 段读取,代码中绝不硬编码;
3. 处理网络请求失败、API 返回错误、缺少配置等异常,给出明确的错误提示。

安全说明:与 RunCli 相同,本工具面向受控教学实验环境。
扩展:照本文件的样子,在别的模块里写一个新工具并 register 即可。
"""
from __future__ import annotations

import subprocess
from typing import Optional

import httpx
import trafilatura

from .text_utils import decode_bytes, truncate_text
from .tools import Tool, ToolRegistry

# curl 下载网页的默认超时(秒)
DEFAULT_FETCH_TIMEOUT_SECONDS = 20.0
# 提取出的正文最大保留字符数(超出截断,防止撑爆上下文)
DEFAULT_MAX_CONTENT_CHARS = 6000

# 百度千帆 AI 搜索接口(官方文档:https://cloud.baidu.com/doc/qianfan-api/s/Wmbq4z7e5)
DEFAULT_SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"
# 默认返回的参考来源条数
DEFAULT_SEARCH_TOP_K = 10
# 请求搜索 API 的默认超时(秒)
DEFAULT_SEARCH_TIMEOUT_SECONDS = 20.0

# 请求网页时使用的 User-Agent(部分网站会拒绝没有 UA 的请求)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) xAgent/1.0 (教学实验)"


def _is_http_url(url: str) -> bool:
    """只允许 http/https 协议,防止 curl 被拿去访问本地文件等奇怪地址。"""
    return url.startswith("http://") or url.startswith("https://")


def register_web_ops(
    registry: ToolRegistry,
    fetch_timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    search_api_key: str = "",
    search_endpoint: str = DEFAULT_SEARCH_ENDPOINT,
    search_top_k: int = DEFAULT_SEARCH_TOP_K,
    search_timeout_seconds: float = DEFAULT_SEARCH_TIMEOUT_SECONDS,
) -> None:
    """把联网工具 fetchWeb、searchWeb 注册进工具注册中心。

    :param registry: 目标工具注册中心
    :param fetch_timeout_seconds: 下载单个网页的超时时间(秒)
    :param max_content_chars: 网页正文 / 搜索结果的最大保留字符数
    :param search_api_key: 百度千帆 API Key(从 config.ini 的 [search] 段读取;
        留空时 searchWeb 会返回"未配置"错误提示,不会崩溃)
    :param search_endpoint: 搜索接口地址(测试时可注入本地假服务,不联网)
    :param search_top_k: 每次搜索返回的参考来源条数上限
    :param search_timeout_seconds: 搜索 API 请求的超时时间(秒)
    """

    # ---- 工具实现:抓取网页并提取正文为 Markdown ----
    def fetch_web(url: str) -> str:
        """下载网页 HTML,用 trafilatura 提取正文为 Markdown。

        :param url: 网页地址,必须是 http:// 或 https:// 开头
        """
        # 1) 参数校验:只允许 http/https
        if not isinstance(url, str) or not url.strip():
            return "错误:参数 url 必须是字符串(网页地址),如 https://example.com"
        url = url.strip()
        if not _is_http_url(url):
            return f"错误:参数 url 必须是 http:// 或 https:// 开头的网页地址,收到:{url!r}"

        # 2) 用 curl 下载网页:列表形式 + shell=False,URL 作为独立参数,杜绝注入。
        #    -sS:静默模式但显示错误;-L:跟随重定向;-f:HTTP 4xx/5xx 时直接失败;
        #    --max-time:限制下载时间,防止卡死。
        cmd = [
            "curl",
            "-sS",
            "-L",
            "-f",
            "--max-time", str(fetch_timeout_seconds),
            "-A", USER_AGENT,
            url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                shell=False,           # 关键安全点:绝不经过 shell 解析
                capture_output=True,   # 捕获 HTML(二进制,稍后统一解码)
                timeout=fetch_timeout_seconds + 5,  # 外层再留 5 秒余量兜底
            )
        except FileNotFoundError:
            return (
                "错误:找不到 curl 程序,请先安装 curl 并加入 PATH"
                "(Windows 10/11 系统自带 C:\\Windows\\System32\\curl.exe)"
            )
        except subprocess.TimeoutExpired:
            return (
                f"错误:下载网页超过 {fetch_timeout_seconds} 秒,已取消。"
                "请稍后重试,或换一个更快的网页。"
            )
        except Exception as exc:  # noqa: BLE001 —— 统一转成文本返回,不中断对话
            return f"错误:调用 curl 失败:{exc}"

        if proc.returncode != 0:
            err = decode_bytes(proc.stderr or b"").strip()
            detail = f":{err}" if err else "(无错误信息)"
            return f"错误:网页访问失败(curl exitcode = {proc.returncode}){detail}"

        html = decode_bytes(proc.stdout or b"")
        if not html.strip():
            return f"错误:网页 {url} 返回了空内容,可能不是有效网页"

        # 3) trafilatura 提取正文 → Markdown
        try:
            # 先用"精确模式":只保留最像正文的区域(标题/段落/列表/表格/链接),
            # 导航栏、广告、页脚会被自动去掉
            markdown = trafilatura.extract(
                html,
                url=url,
                output_format="markdown",
                favor_precision=True,
            )
            if not markdown or not markdown.strip():
                # 精确模式提不出来(页面结构不太标准),换"召回优先"模式再试
                markdown = trafilatura.extract(
                    html,
                    url=url,
                    output_format="markdown",
                    favor_recall=True,
                )
        except Exception as exc:  # noqa: BLE001 —— HTML 解析失败也转成文本
            return f"错误:网页正文提取失败(HTML 解析异常):{exc}"

        if not markdown or not markdown.strip():
            return (
                f"错误:无法从 {url} 提取正文。网页已成功下载,但 trafilatura "
                "提取结果为空(页面可能是纯图片/视频、需要登录或已失效),"
                "请换一个网页再试。"
            )

        markdown = truncate_text(markdown.strip(), max_content_chars)
        return f"[网页正文(来自 {url}),以下为 Markdown 格式]\n\n{markdown}"

    # ---- 工具实现:百度千帆联网搜索 ----
    def search_web(query: str) -> str:
        """调用百度千帆 AI 搜索 API,返回搜索摘要 + 来源标题 + URL。

        :param query: 搜索关键词(自然语言问题或关键词均可)
        """
        # 1) 参数校验
        if not isinstance(query, str) or not query.strip():
            return "错误:参数 query 必须是字符串(搜索关键词)"

        # 2) 配置校验:API Key 从 config.ini 的 [search] 段读取,绝不硬编码
        if not search_api_key:
            return (
                "错误:未配置百度千帆 API Key。请在 config.ini 的 [search] 段填写 "
                "baidu_qianfan_api_key(在百度智能云千帆平台申请),然后重启 xAgent。"
            )

        # 3) 请求千帆搜索接口(web_search)
        headers = {
            "Authorization": f"Bearer {search_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "messages": [{"role": "user", "content": query.strip()}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": search_top_k}],
        }
        try:
            resp = httpx.post(
                search_endpoint,
                headers=headers,
                json=body,
                timeout=search_timeout_seconds,
            )
        except httpx.TimeoutException:
            return (
                f"错误:搜索请求超过 {search_timeout_seconds} 秒未响应,"
                "请稍后重试。"
            )
        except httpx.HTTPError as exc:  # 网络层失败:连不上 / DNS 解析失败等
            return f"错误:网络请求失败,无法连接搜索服务:{exc}"

        # 4) 解析响应:HTTP 状态码异常 或 API 业务错误码 都要给出明确提示
        try:
            data = resp.json()
        except ValueError:
            return (
                f"错误:搜索服务返回了无法解析的响应(HTTP {resp.status_code}),"
                "请稍后重试。"
            )

        if resp.status_code != 200 or "references" not in data:
            code = data.get("code", "?")
            message = data.get("message", f"HTTP {resp.status_code}")
            return (
                f"错误:搜索 API 调用失败:code={code},message={message}。"
                "若提示与鉴权/token 相关,请检查 config.ini 的 [search] 段里 "
                "baidu_qianfan_api_key 是否正确、有效。"
            )

        # 5) 整理结果:搜索摘要 + 来源标题 + URL(引用数组在 references 字段里)
        refs = data.get("references", [])
        if not refs:
            return "搜索完成,但没有找到相关结果,请换个关键词再试。"

        lines = [f"搜索结果(共 {len(refs)} 条,来自百度千帆 AI 搜索):", ""]
        for i, ref in enumerate(refs, 1):
            title = (ref.get("title") or "无标题").strip()
            url = (ref.get("url") or "").strip()
            # snippet 是搜索摘要;没有摘要时退回 content 字段
            snippet = (ref.get("snippet") or ref.get("content") or "").strip()
            lines.append(f"### {i}. {title}")
            if url:
                lines.append(f"URL: {url}")
            if snippet:
                lines.append(snippet)
            lines.append("")
        return truncate_text("\n".join(lines), max_content_chars)

    # ---- 注册:模型能看到的只有 name / description / parameters ----
    registry.register(
        Tool(
            name="fetchWeb",
            description=(
                "抓取指定网页并提取正文(去除导航栏/广告/页脚,保留标题、段落、"
                "列表、表格和链接),以 Markdown 格式返回。适合需要读取某个网页"
                "具体内容、查资料时使用。只接受 http/https 开头的网址;"
                f"单个网页下载最长 {fetch_timeout_seconds} 秒,正文过长会被截断(结果中会标注)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "要抓取的网页完整地址,必须以 http:// 或 https:// 开头,"
                            "如 https://example.com/article"
                        ),
                    },
                },
                "required": ["url"],
            },
            handler=fetch_web,
        )
    )
    registry.register(
        Tool(
            name="searchWeb",
            description=(
                "使用百度千帆 AI 搜索引擎进行联网搜索,返回搜索摘要、来源标题和 URL。"
                "适合回答需要最新信息、时事新闻、事实查询的问题。"
                "返回结果条数有限(默认 "
                f"{search_top_k} 条),过长会被截断(结果中会标注)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或自然语言问题,如 2026 年诺贝尔物理学奖得主",
                    },
                },
                "required": ["query"],
            },
            handler=search_web,
        )
    )
