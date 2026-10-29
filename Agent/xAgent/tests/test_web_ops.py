"""联网工具(fetchWeb / searchWeb)的单元测试:不联网、不需要 LM Studio。

fetchWeb:在本地起一个 HTTP 测试服务器(独立线程),用真实 curl + trafilatura
跑完整的"下载 → 提取正文 → Markdown"链路;
searchWeb:把千帆搜索接口地址注入成本地假服务,验证请求头(API Key 鉴权)、
请求参数与结果整理;
另覆盖:URL 校验、HTTP 404、下载超时、正文提取失败、API Key 未配置、
API 业务错误码、schema 生成,以及与 Agent 的工具调用集成。

运行方式(在项目根目录):
    venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import http.server
import json
import threading
import time
import unittest
from unittest import mock

from core import events
from core.agent import Agent
from core.tools import ToolRegistry
from core.web_ops import register_web_ops
from tests.test_core import FakeLLM


# ---------------------------------------------------------------------------
# 测试用的本地 HTTP 服务器
# ---------------------------------------------------------------------------

# fetchWeb 的测试页面:正文(标题/段落/列表/表格/链接)+ 应被去除的噪声
ARTICLE_HTML = """<!DOCTYPE html>
<html><head><title>测试文章</title></head><body>
<nav>导航栏:首页 关于我们</nav>
<div class="ad">广告:点击购买</div>
<aside>侧边栏:热门推荐</aside>
<article>
  <h1>深度学习入门指南</h1>
  <p>深度学习是机器学习的一个分支,它使用多层神经网络来学习数据表示。</p>
  <h2>学习路线</h2>
  <ul><li>第一步:学习 Python</li><li>第二步:学习 PyTorch</li></ul>
  <h2>课程表</h2>
  <table><tr><th>课程</th><th>学时</th></tr><tr><td>线性代数</td><td>20</td></tr></table>
  <p>更多资料见<a href="/more">官方文档</a>。</p>
</article>
<footer>页脚:版权所有 2026</footer>
</body></html>"""


class _ArticleHandler(http.server.BaseHTTPRequestHandler):
    """模拟一个普通网站:正常文章页、404 页、极慢页。"""

    def do_GET(self):
        if self.path == "/article":
            self._reply(200, ARTICLE_HTML, "text/html")
        elif self.path == "/slow":
            time.sleep(5)  # 让 curl 超时(测试里 fetch 超时设 1 秒)
            try:
                self._reply(200, "<html><body>slow</body></html>", "text/html")
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass  # curl 超时已断开连接,回写失败属预期,忽略
        else:
            self._reply(404, "not found", "text/plain")

    def _reply(self, status: int, body: str, content_type: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 静默,测试输出不刷屏
        pass


class _SearchHandler(http.server.BaseHTTPRequestHandler):
    """模拟百度千帆搜索接口:记录请求头/请求体,返回预设响应。"""

    # 类属性 = 每个测试用例可重新设定的"服务端状态"
    response_status = 200
    response_body: bytes = b"{}"
    seen_headers: dict = {}
    seen_body: bytes = b""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        type(self).seen_headers = dict(self.headers)
        type(self).seen_body = body
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(type(self).response_body)))
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, *args):  # 静默
        pass


class _ReusableServer:
    """在后台线程运行的本地 HTTP 服务(端口随机,测试结束关闭)。"""

    def __init__(self, handler):
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------

def make_web_registry(**kwargs) -> ToolRegistry:
    """注册联网工具,返回只含 fetchWeb / searchWeb 的注册中心。"""
    tools = ToolRegistry()
    register_web_ops(tools, **kwargs)
    return tools


def execute(tools: ToolRegistry, name: str, arguments: dict) -> str:
    return asyncio.run(tools.execute(name, json.dumps(arguments, ensure_ascii=False)))


# ---------------------------------------------------------------------------
# fetchWeb 测试
# ---------------------------------------------------------------------------

class TestFetchWeb(unittest.TestCase):
    """fetchWeb 工具本身的行为测试(本地服务器 + 真实 curl + 真实 trafilatura)。"""

    def setUp(self):
        self.server = _ReusableServer(_ArticleHandler)
        self.addCleanup(self.server.stop)
        self.tools = make_web_registry()

    def test_extracts_markdown_and_strips_noise(self):
        """核心需求:正文保留标题/段落/列表/表格/链接,去除导航/广告/页脚。"""
        result = execute(
            self.tools, "fetchWeb", {"url": self.server.url + "/article"}
        )
        # 原文 URL 与 Markdown 标题/正文/列表/表格/链接都保留
        self.assertIn(self.server.url + "/article", result)
        self.assertIn("深度学习入门指南", result)
        self.assertIn("多层神经网络", result)
        self.assertIn("第一步:学习 Python", result)
        self.assertIn("课程", result)      # 表格头
        self.assertIn("线性代数", result)  # 表格内容
        self.assertIn("官方文档", result)  # 链接文字
        # 导航栏 / 广告 / 侧边栏 / 页脚全部被去除
        self.assertNotIn("导航栏", result)
        self.assertNotIn("广告", result)
        self.assertNotIn("侧边栏", result)
        self.assertNotIn("页脚", result)

    def test_http_404_reports_failure(self):
        """HTTP 404:curl -f 直接失败,返回友好错误而不是错误页面内容。"""
        result = execute(
            self.tools, "fetchWeb", {"url": self.server.url + "/missing"}
        )
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("网页访问失败", result)
        self.assertIn("exitcode = 22", result)  # curl 的 HTTP 错误退出码

    def test_timeout_is_reported(self):
        """页面超过超时时间:curl 被 --max-time 终止,给出明确提示。"""
        tools = make_web_registry(fetch_timeout_seconds=1.0)
        result = execute(tools, "fetchWeb", {"url": self.server.url + "/slow"})
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("网页访问失败", result)
        self.assertIn("exitcode = 28", result)  # curl 的超时退出码

    def test_bad_url_is_rejected(self):
        """参数校验:非 http/https 协议与空 url 都给出友好错误。"""
        result = execute(self.tools, "fetchWeb", {"url": "file:///C:/etc/passwd"})
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("http:// 或 https://", result)

        result = execute(self.tools, "fetchWeb", {"url": ""})
        self.assertTrue(result.startswith("错误"), result)

    def test_empty_extraction_is_reported(self):
        """HTML 下载成功但正文提取失败(模拟):提示换网页,不返回空内容。"""
        with mock.patch("core.web_ops.trafilatura.extract", return_value=""):
            result = execute(
                self.tools, "fetchWeb", {"url": self.server.url + "/article"}
            )
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("无法从", result)
        self.assertIn("提取", result)


# ---------------------------------------------------------------------------
# searchWeb 测试
# ---------------------------------------------------------------------------

SEARCH_REFERENCES = {
    "references": [
        {
            "title": "百度百科-深度学习",
            "url": "https://baike.baidu.com/item/深度学习",
            "content": "深度学习是机器学习的分支……",
            "snippet": "深度学习是机器学习的分支,以人工神经网络为架构。",
        },
        {
            "title": "PyTorch 官网",
            "url": "https://pytorch.org",
            "snippet": "",
        },
    ]
}


class TestSearchWeb(unittest.TestCase):
    """searchWeb 工具的行为测试(接口地址注入本地假服务,不联网)。"""

    def setUp(self):
        _SearchHandler.response_status = 200
        _SearchHandler.response_body = json.dumps(SEARCH_REFERENCES, ensure_ascii=False).encode("utf-8")
        _SearchHandler.seen_headers = {}
        _SearchHandler.seen_body = b""
        self.server = _ReusableServer(_SearchHandler)
        self.addCleanup(self.server.stop)
        self.tools = make_web_registry(
            search_api_key="test-key-123",
            search_endpoint=self.server.url + "/web_search",
        )

    def test_search_returns_summaries_titles_urls(self):
        """核心需求:返回搜索摘要、来源标题与 URL 的整理结果。"""
        result = execute(self.tools, "searchWeb", {"query": "深度学习"})
        self.assertIn("百度百科-深度学习", result)          # 标题
        self.assertIn("https://baike.baidu.com/item/深度学习", result)  # URL
        self.assertIn("深度学习是机器学习的分支,以人工神经网络为架构。", result)  # 摘要
        self.assertIn("PyTorch 官网", result)
        self.assertIn("https://pytorch.org", result)
        self.assertIn("2 条", result)  # 条数统计

    def test_request_uses_bearer_auth_and_correct_body(self):
        """发给 API 的请求:Authorization 用 Bearer 鉴权,请求体符合千帆格式。"""
        execute(self.tools, "searchWeb", {"query": "深度学习"})
        self.assertEqual(
            _SearchHandler.seen_headers.get("Authorization"), "Bearer test-key-123"
        )
        body = json.loads(_SearchHandler.seen_body.decode("utf-8"))
        self.assertEqual(body["messages"], [{"role": "user", "content": "深度学习"}])
        self.assertEqual(body["search_source"], "baidu_search_v2")
        self.assertEqual(body["resource_type_filter"], [{"type": "web", "top_k": 10}])

    def test_missing_api_key_is_reported(self):
        """config.ini 没填 API Key:返回"未配置"提示(引导用户,而不是崩溃)。"""
        tools = make_web_registry(search_api_key="", search_endpoint=self.server.url)
        result = execute(tools, "searchWeb", {"query": "深度学习"})
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("未配置百度千帆 API Key", result)
        self.assertIn("baidu_qianfan_api_key", result)

    def test_api_business_error_is_reported(self):
        """API 返回业务错误码(HTTP 200 但 code!=0):把 code/message 给模型看。"""
        _SearchHandler.response_body = json.dumps(
            {"code": 216100, "message": "鉴权失败"}, ensure_ascii=False
        ).encode("utf-8")
        result = execute(self.tools, "searchWeb", {"query": "深度学习"})
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("code=216100", result)
        self.assertIn("鉴权失败", result)

    def test_non_json_response_is_reported(self):
        """服务返回 500 + 非 JSON 内容:提示无法解析,不抛异常。"""
        _SearchHandler.response_status = 500
        _SearchHandler.response_body = b"boom"
        result = execute(self.tools, "searchWeb", {"query": "深度学习"})
        self.assertTrue(result.startswith("错误"), result)
        self.assertIn("无法解析的响应", result)
        self.assertIn("500", result)

    def test_bad_query_is_rejected(self):
        """参数校验:query 为空字符串给出友好错误。"""
        result = execute(self.tools, "searchWeb", {"query": ""})
        self.assertTrue(result.startswith("错误"), result)


# ---------------------------------------------------------------------------
# schema 与 Agent 集成
# ---------------------------------------------------------------------------

class TestWebOpsSchemas(unittest.TestCase):
    """function calling 的 schema 正确性。"""

    def test_schemas_are_registered(self):
        tools = make_web_registry()
        names = {s["function"]["name"] for s in tools.openai_schemas()}
        self.assertEqual(names, {"fetchWeb", "searchWeb"})
        by_name = {s["function"]["name"]: s["function"] for s in tools.openai_schemas()}

        fetch = by_name["fetchWeb"]
        self.assertEqual(fetch["parameters"]["required"], ["url"])
        self.assertEqual(fetch["parameters"]["properties"]["url"]["type"], "string")

        search = by_name["searchWeb"]
        self.assertEqual(search["parameters"]["required"], ["query"])
        self.assertEqual(search["parameters"]["properties"]["query"]["type"], "string")


class TestWebOpsWithAgent(unittest.TestCase):
    """联网工具与 Agent 编排的集成测试(假 LLM + 本地服务器)。"""

    def test_agent_executes_fetchweb_round_trip(self):
        """模型提议调用 fetchWeb → Agent 执行 → 结果送回模型。"""
        server = _ReusableServer(_ArticleHandler)
        self.addCleanup(server.stop)
        url = server.url + "/article"

        tools = make_web_registry()
        agent = Agent(
            llm=FakeLLM([
                [
                    events.ToolCallProposal(
                        call_id="call_1", name="fetchWeb",
                        arguments=json.dumps({"url": url}, ensure_ascii=False),
                    ),
                    events.StreamEnd("tool_calls"),
                ],
                [
                    events.TextChunk("这篇文章讲的是深度学习入门。"),
                    events.StreamEnd("stop"),
                ],
            ]),
            tools=tools,
        )

        evs = asyncio.run(_collect(agent, "帮我看看这个网页说了什么"))

        finished = next(
            ev for ev in evs if isinstance(ev, events.ToolCallFinished)
        )
        self.assertTrue(finished.ok, finished.result)
        self.assertIn("深度学习入门指南", finished.result)
        self.assertNotIn("导航栏", finished.result)
        # 历史完整:user / assistant(tool_calls) / tool / assistant
        self.assertEqual(
            [m["role"] for m in agent.messages],
            ["user", "assistant", "tool", "assistant"],
        )


async def _collect(agent: Agent, text: str):
    return [ev async for ev in agent.chat(text)]


if __name__ == "__main__":
    unittest.main()
