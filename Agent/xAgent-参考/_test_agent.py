"""用 mock 模型服务跑通 Agent 的完整一轮，验证业务逻辑。

mock 服务只在本进程内起一个 http server，不外连。
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, ".")

from core.agent import Agent
from core.config import Config
from core.tools import default_registry

passed = 0
failed = []
events = []


def check(label, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("OK   %s" % label)
    else:
        failed.append(label)
        print("FAIL %s  %s" % (label, detail))


class Handler(BaseHTTPRequestHandler):
    """第一轮吐分片 tool_calls，第二轮吐最终回答。"""

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        msgs = body["messages"]
        has_tool = any(m.get("role") == "tool" for m in msgs)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(obj):
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()

        if not has_tool:
            # 分三片下发，模拟真实流式
            send({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_x", "type": "function",
                 "function": {"name": "isLeapYear", "arguments": ""}}]}}]})
            send({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"year":'}}]}}]})
            send({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": ' 1900}'}}]}}]})
        else:
            got = [m["content"] for m in msgs if m.get("role") == "tool"]
            for ch in "结论：" + (got[0] if got else "?"):
                send({"choices": [{"delta": {"content": ch}}]})
        send({"choices": [{"delta": {}}]})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def start_server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port


def main():
    srv, port = start_server()
    print("mock 服务端口:", port)
    print("=" * 66)

    cfg = Config("http://127.0.0.1:%d/v1" % port, "mock-model")
    agent = Agent(config=cfg)
    check("Agent 用 mock 配置", agent.config.model == "mock-model")

    agent.on_assistant_start = lambda: events.append(("start", None))
    agent.on_assistant_delta = lambda t: events.append(("delta", t))
    agent.on_assistant_end = lambda t, c: events.append(("end", (t, len(c))))
    agent.on_tool_start = lambda n, a: events.append(("tool_start", (n, a)))
    agent.on_tool_end = lambda n, r, e: events.append(("tool_end", (n, r, e)))
    agent.on_error = lambda m: events.append(("error", m))

    result = agent.ask("1900年是闰年吗")
    srv.shutdown()

    print()
    print("事件序列:")
    for name, payload in events:
        print("  %-12s %s" % (name, str(payload)[:90]))
    print()

    kinds = [n for n, _ in events]
    check("触发了 assistant_start", "start" in kinds)
    check("有流式 delta", kinds.count("delta") > 0, str(kinds.count("delta")))
    check("触发了 tool_start", "tool_start" in kinds)
    check("触发了 tool_end", "tool_end" in kinds)
    check("没有错误", "error" not in kinds, str([e for e in events if e[0] == "error"]))

    tool_starts = [p for n, p in events if n == "tool_start"]
    check("工具名正确", tool_starts and tool_starts[0][0] == "isLeapYear", str(tool_starts))
    check("参数分片拼对", tool_starts and tool_starts[0][1].get("year") == 1900, str(tool_starts))

    tool_ends = [p for n, p in events if n == "tool_end"]
    check("工具执行成功", tool_ends and tool_ends[0][2] is None, str(tool_ends))
    check("工具结果是闰年判断", tool_ends and "不是闰年" in str(tool_ends[0][1]), str(tool_ends))

    check("最终有回答", "结论" in result, repr(result))
    check("历史含 user", agent.history[0]["role"] == "user")
    check("历史含 assistant+tool_calls",
          any(m.get("tool_calls") for m in agent.history), str(len(agent.history)))
    check("历史含 tool 响应",
          any(m.get("role") == "tool" for m in agent.history))

    print()
    print("=" * 66)

    # 连接失败的回滚
    events.clear()
    bad = Agent(config=Config("http://127.0.0.1:1/v1", "x", timeout=3))
    bad.on_error = lambda m: events.append(("error", m))
    bad.history.append({"role": "user", "content": "旧"})
    before = len(bad.history)
    bad.ask("新问题")
    check("连接失败有错误事件", any(n == "error" for n, _ in events), str(events))
    check("失败后回滚到原长度", len(bad.history) == before,
          "%d -> %d" % (before, len(bad.history)))

    print()
    print("通过 %d 项，失败 %d 项" % (passed, len(failed)))
    if failed:
        for f in failed:
            print("  失败:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
