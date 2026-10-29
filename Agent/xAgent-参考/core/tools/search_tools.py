"""联网搜索工具：调百度千帆 AI 搜索 API。

API key 从 config.ini 的 [search] 小节读取（baidu_qianfan_api_key），
不在代码里硬编码。

接口：POST https://qianfan.baidubce.com/v2/ai_search/web_search
鉴权：Authorization: Bearer <API Key>
"""
import json
import urllib.error
import urllib.request

from .registry import Tool

SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"
SEARCH_TIMEOUT = 30
DEFAULT_TOP_K = 10
MAX_TOP_K = 50
MAX_SNIPPET_CHARS = 300


def read_api_key():
    """从 config.ini 的 [search] 小节读 key。读不到返回空串。"""
    try:
        from ..config import read_section
    except ImportError:
        from core.config import read_section
    try:
        return read_section("search", "baidu_qianfan_api_key").strip()
    except Exception:
        return ""


def build_payload(query, top_k=DEFAULT_TOP_K, recency=None):
    """拼请求体。字段名以百度千帆文档为准。"""
    payload = {
        "messages": [{"role": "user", "content": query}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": top_k}],
    }
    if recency:
        payload["search_recency_filter"] = recency
    return payload


def call_api(api_key, payload, timeout=SEARCH_TIMEOUT):
    """发请求，返回 (json字典, 错误信息)。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
        "User-Agent": "xAgent/0.1",
    }
    req = urllib.request.Request(SEARCH_ENDPOINT, body, headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        hint = ""
        if e.code == 401:
            hint = "（API Key 无效或已失效，请核对 config.ini 里 [search] 的 baidu_qianfan_api_key）"
        elif e.code == 403:
            hint = "（权限不足，可能是该 Key 未开通 AI 搜索服务）"
        elif e.code == 429:
            hint = "（请求过于频繁，触发了限流）"
        elif e.code >= 500:
            hint = "（百度服务端异常，稍后重试）"
        msg = "搜索接口返回 HTTP %d%s" % (e.code, hint)
        if detail:
            msg += "。详情：" + detail[:400]
        return None, msg
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return None, "网络请求失败：%s（请检查网络连接或代理设置）" % reason
    except TimeoutError:
        return None, "搜索请求超时（超过 %d 秒）。" % timeout
    except OSError as e:
        return None, "网络请求异常：%s" % e

    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return None, "接口返回的不是合法 JSON：%s" % raw[:300]


def pick_results(data):
    """从响应里取结果列表。不同版本字段名略有差异，这里兼容几种。"""
    if not isinstance(data, dict):
        return []
    for key in ("references", "search_results", "results", "documents"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("references"), list):
            return value["references"]
    # 有些返回把结果塞在 data 里
    inner = data.get("data")
    if isinstance(inner, dict):
        return pick_results(inner)
    return []


def pick_summary(data):
    """取顶部的 AI 总结（如果有）。"""
    if not isinstance(data, dict):
        return ""
    for key in ("summary", "answer", "abstract", "conclusion"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    inner = data.get("data")
    if isinstance(inner, dict):
        return pick_summary(inner)
    return ""


def format_results(query, results, summary="", top_k=DEFAULT_TOP_K):
    """把结果拼成给模型看的文本。"""
    lines = ["搜索关键词：%s" % query, "结果数量：%d 条" % len(results)]

    if summary:
        lines += ["", "--- 搜索摘要 ---", summary]

    if not results:
        lines += ["", "没有搜到相关结果。可以换个关键词再试。"]
        return "\n".join(lines)

    lines += ["", "--- 参考来源 ---"]
    for i, item in enumerate(results[:top_k], 1):
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or item.get("name") or "").strip() or "（无标题）"
        url = (item.get("url") or item.get("link") or "").strip()
        snippet = (item.get("snippet") or item.get("content") or item.get("abstract") or "").strip()
        site = (item.get("website") or item.get("site") or "").strip()
        date = (item.get("date") or "").strip()
        # 百度偶尔把站点名写成「无」，别原样显示
        if site in ("无", "None", "null"):
            site = ""

        lines.append("")
        lines.append("%d. %s" % (i, title))
        if url:
            lines.append("   链接：%s" % url)
        meta = []
        if site:
            meta.append("站点：" + site)
        if date:
            meta.append("时间：" + date)
        if meta:
            lines.append("   " + "  ".join(meta))
        if snippet:
            if len(snippet) > MAX_SNIPPET_CHARS:
                snippet = snippet[:MAX_SNIPPET_CHARS].rstrip() + "……"
            lines.append("   摘要：%s" % snippet.replace("\n", " "))

    return "\n".join(lines)


def search_web(query=None, top_k=DEFAULT_TOP_K, recency=None):
    """搜网页，返回摘要和参考来源。"""
    if not query or not str(query).strip():
        return "缺少 query 参数。请给出要搜索的关键词或问题。"

    query = str(query).strip()

    api_key = read_api_key()
    if not api_key:
        return (
            "没有配置搜索 API Key，无法联网搜索。\n"
            "请在 config.ini 里加上 [search] 小节并填入 baidu_qianfan_api_key：\n\n"
            "[search]\n"
            "baidu_qianfan_api_key = 你的密钥\n\n"
            "密钥在百度智能云千帆控制台的 API Key 页面获取。"
        )
    # 模板占位符也当没填
    if not api_key.isascii() or api_key.lower() in ("your-key", "your_key", "placeholder", "xxx"):
        return (
            "config.ini 里的 baidu_qianfan_api_key 还是占位符，请换成真实的 API Key。\n"
            "密钥在百度智能云千帆控制台的 API Key 页面获取。"
        )

    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        top_k = DEFAULT_TOP_K
    top_k = max(1, min(top_k, MAX_TOP_K))

    recency = (recency or "").strip().lower() or None
    if recency not in (None, "week", "month", "semiyear", "year"):
        recency = None

    payload = build_payload(query, top_k, recency)
    data, err = call_api(api_key, payload)
    if err:
        return "联网搜索失败：%s" % err

    # 接口可能返回业务错误码
    if isinstance(data, dict):
        code = data.get("code") or data.get("error_code")
        if code not in (None, 0, "0", 200):
            msg = data.get("message") or data.get("error_msg") or "未知错误"
            return "联网搜索失败：接口返回错误码 %s，信息：%s" % (code, msg)

    results = pick_results(data)
    summary = pick_summary(data)

    if not results and not summary:
        return (
            "联网搜索没有拿到结果。\n"
            "接口返回内容：%s\n"
            "可以换一个更具体的关键词再试。" % json.dumps(data, ensure_ascii=False)[:500]
        )

    return format_results(query, results, summary, top_k)


def build_tools():
    """返回联网搜索类工具。"""
    return [
        Tool(
            name="searchWeb",
            description=(
                "联网搜索最新信息。输入关键词或问题，返回搜索摘要和参考来源的标题、链接、摘要。"
                "凡是涉及实时信息、近期新闻、你不确定或知识可能过时的问题，都应该调用它，"
                "不要凭记忆回答。需要看某个网页的具体内容时，再对返回的链接调用 fetchWeb。"
            ),
            func=search_web,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或问题，用自然语言描述即可，例如「2026年诺贝尔物理学奖」。",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回多少条结果，默认 10，最大 50。",
                    },
                    "recency": {
                        "type": "string",
                        "enum": ["week", "month", "semiyear", "year"],
                        "description": "按发布时间筛选：week 最近7天，month 最近30天，semiyear 最近180天，year 最近365天。不传则不限制。",
                    },
                },
            },
            required=["query"],
        ),
    ]
