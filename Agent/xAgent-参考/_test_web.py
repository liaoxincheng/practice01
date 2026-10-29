"""searchWeb 与 fetchWeb 的单元测试。

全部用 mock，不外连；真实验证在真机实测脚本里。
"""
import sys
import json

sys.path.insert(0, ".")

from core.tools import search_tools, web_tools

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


print("=" * 70)
print("一、searchWeb：参数与配置检查")
print("=" * 70)

# 缺 query
r = search_tools.search_web()
check("缺 query 时报错", "缺少 query" in r, r[:80])

r = search_tools.search_web("")
check("空 query 时报错", "缺少 query" in r, r[:80])

# 没配 key —— 用 mock 屏蔽真实 config.ini 里的 key，
# 否则这些用例会真的去调百度接口
_real_read_key = search_tools.read_api_key
search_tools.read_api_key = lambda: ""
r = search_tools.search_web("测试")
check("无 key 时给出清晰提示", "没有配置搜索 API Key" in r, r[:120])
check("无 key 提示里说明了怎么配", "[search]" in r and "baidu_qianfan_api_key" in r, r[:150])

# key 是占位符
search_tools.read_api_key = lambda: "你的密钥"
r = search_tools.search_web("测试")
check("占位符 key 被拦下", "占位符" in r, r[:120])

search_tools.read_api_key = _real_read_key

print()
print("=" * 70)
print("二、searchWeb：请求体构造")
print("=" * 70)

p = search_tools.build_payload("今天天气")
check("messages 结构正确", p["messages"] == [{"role": "user", "content": "今天天气"}], str(p["messages"]))
check("search_source 固定值", p["search_source"] == "baidu_search_v2", str(p.get("search_source")))
check("resource_type_filter 结构", p["resource_type_filter"] == [{"type": "web", "top_k": 10}], str(p.get("resource_type_filter")))
check("未传 recency 时不加该字段", "search_recency_filter" not in p, str(p))

p2 = search_tools.build_payload("新闻", top_k=5, recency="week")
check("top_k 生效", p2["resource_type_filter"][0]["top_k"] == 5, str(p2["resource_type_filter"]))
check("recency 生效", p2.get("search_recency_filter") == "week", str(p2.get("search_recency_filter")))

print()
print("=" * 70)
print("三、searchWeb：响应解析")
print("=" * 70)

fake = {
    "requestId": "x",
    "references": [
        {"id": 1, "title": "标题A", "url": "https://a.com", "snippet": "摘要A内容", "website": "a.com", "date": "2026-10-01"},
        {"id": 2, "title": "标题B", "url": "https://b.com", "snippet": "摘要B内容"},
    ],
}
res = search_tools.pick_results(fake)
check("references 解析出 2 条", len(res) == 2, str(len(res)))

out = search_tools.format_results("测试词", res)
check("输出含查询词", "测试词" in out)
check("输出含标题A", "标题A" in out)
check("输出含链接", "https://a.com" in out)
check("输出含摘要", "摘要A内容" in out)
check("输出含站点", "a.com" in out)
check("输出含日期", "2026-10-01" in out)

# 结果为空
out2 = search_tools.format_results("空词", [])
check("空结果时提示换关键词", "没有搜到" in out2, out2[:100])

# 长摘要截断
long_snip = "长" * 900
out3 = search_tools.format_results("x", [{"title": "t", "url": "u", "snippet": long_snip}])
check("超长摘要被截断", "……" in out3 and len(out3) < 1200, str(len(out3)))

# 兼容 search_results 字段
check("兼容 search_results 字段", len(search_tools.pick_results({"search_results": [{"title": "z"}]})) == 1)

# 业务错误码
print()
print("=" * 70)
print("四、searchWeb：错误处理（mock call_api）")
print("=" * 70)

orig_call = search_tools.call_api
orig_key = search_tools.read_api_key

# 有 key 但接口报错
search_tools.read_api_key = lambda: "ms-fakekey"
search_tools.call_api = lambda k, p, timeout=30: (None, "网络请求失败：连接超时")
r = search_tools.search_web("测试")
check("网络失败时返回清晰错误", "联网搜索失败" in r and "网络请求失败" in r, r[:150])

# 业务错误码
search_tools.call_api = lambda k, p, timeout=30: ({"code": 403, "message": "无权限"}, None)
r = search_tools.search_web("测试")
check("业务错误码被识别", "错误码 403" in r, r[:150])

# 占位符 key
search_tools.read_api_key = lambda: "你的密钥"
r = search_tools.search_web("测试")
check("中文占位符 key 被拦下", "占位符" in r, r[:120])

# 正常返回
search_tools.read_api_key = lambda: "ms-fakekey"
search_tools.call_api = lambda k, p, timeout=30: (fake, None)
r = search_tools.search_web("正常测试")
check("正常路径返回结果", "标题A" in r and "标题B" in r, r[:100])

# 有 summary 字段
search_tools.call_api = lambda k, p, timeout=30: ({"summary": "这是AI总结", "references": [{"title": "T", "url": "U"}]}, None)
r = search_tools.search_web("总结测试")
check("summary 被展示", "这是AI总结" in r and "搜索摘要" in r, r[:150])

search_tools.call_api = orig_call
search_tools.read_api_key = orig_key

print()
print("=" * 70)
print("五、fetchWeb：参数与异常")
print("=" * 70)

r = web_tools.fetch_web()
check("缺 url 时报错", "缺少 url" in r, r[:80])

r = web_tools.fetch_web("ftp://x.com")
check("非 http 协议被拒", "http://" in r, r[:80])

r = web_tools.fetch_web("example.com")
check("缺协议头被拒", "必须以" in r, r[:80])

# 域名不存在
r = web_tools.fetch_web("https://this-domain-should-not-exist-12345.invalid")
check("域名解析失败有提示", "失败" in r, r[:150])

# 超时/连接拒绝
r = web_tools.fetch_web("http://127.0.0.1:1/", timeout=3)
check("连接失败有提示", "失败" in r or "拒绝" in r, r[:150])

print()
print("=" * 70)
print("六、fetchWeb：HTML 转 Markdown")
print("=" * 70)

html = """
<html><head><title>测试页</title></head><body>
<nav>导航栏噪音</nav>
<article>
<h1>文章标题</h1>
<p>这是第一段正文，内容足够长以便提取器识别为正文主体内容。</p>
<h2>小节</h2>
<ul><li>列表项一</li><li>列表项二</li></ul>
<p>带<a href="https://example.com">链接</a>的段落，也写得长一些方便识别。</p>
</article>
<footer>页脚版权信息</footer>
</body></html>
"""
md, err = web_tools.html_to_markdown(html, "https://example.com")
check("能提取出 Markdown", md is not None and not err, str(err))
if md:
    check("保留标题", "文章标题" in md, md[:200])
    check("保留列表", "列表项一" in md, md[:200])
    check("保留链接（Markdown 格式）", "https://example.com" in md, md[:300])
    check("去掉导航栏", "导航栏噪音" not in md, md[:300])
    check("去掉页脚", "页脚版权信息" not in md, md[:300])

# 空 HTML
md2, err2 = web_tools.html_to_markdown("<html><body></body></html>")
check("空页面提示提取失败", md2 is None and "没能" in (err2 or ""), str(err2))

print()
print("=" * 70)
print("七、fetchWeb：截断")
print("=" * 70)

text = "x" * 30000
cut, was = web_tools.truncate(text, 1000)
check("超长被截断", was and len(cut) < 1100, str(len(cut)))
check("截断有标记", "已截断" in cut)

short, was2 = web_tools.truncate("短文本", 1000)
check("短文本不截断", not was2 and short == "短文本")

print()
print("=" * 70)
print("八、工具声明")
print("=" * 70)
sw = search_tools.build_tools()[0]
fw = web_tools.build_tools()[0]
check("searchWeb 名字", sw.name == "searchWeb", sw.name)
check("searchWeb required 含 query", sw.required == ["query"], str(sw.required))
check("searchWeb 有 recency 枚举", "enum" in sw.parameters["properties"]["recency"])
check("fetchWeb 名字", fw.name == "fetchWeb", fw.name)
check("fetchWeb required 含 url", fw.required == ["url"], str(fw.required))

print()
print("=" * 70)
print("通过 %d 项，失败 %d 项" % (passed, len(failed)))
if failed:
    for f in failed:
        print("  失败:", f)
sys.exit(1 if failed else 0)
