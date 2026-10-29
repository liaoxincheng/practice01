"""OpenAI 兼容的流式客户端。

只负责「发请求、解析 SSE、把片段吐出来」，不关心谁在显示。
"""
import json
import urllib.error
import urllib.request


class LLMError(Exception):
    """请求或流解析出错。"""


class Delta:
    """一帧流式响应解析出来的内容。"""

    def __init__(self, content="", tool_calls=None, finish_reason=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.finish_reason = finish_reason

    def __repr__(self):
        return "Delta(content=%r, tool_calls=%d)" % (self.content[:20], len(self.tool_calls))


class ToolCallBuffer:
    """按 index 归并流式下发的 tool_calls 分片。

    分片可能是残缺的：一次只给 id，下一次只给 arguments 的一小段，
    所以必须按 index 攒起来，等流结束再拼成完整调用。
    """

    def __init__(self):
        self._slots = {}

    def feed(self, fragments):
        for call in fragments:
            idx = call.get("index", 0)
            slot = self._slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if call.get("id"):
                slot["id"] = call["id"]
            fn = call.get("function") or {}
            if fn.get("name") and not slot["name"]:
                slot["name"] = fn["name"]
            piece = fn.get("arguments")
            if piece:
                slot["arguments"] += piece

    def __len__(self):
        return len(self._slots)

    def __bool__(self):
        return bool(self._slots)

    def ready(self):
        """返回拼好的调用列表，按 index 排序。"""
        return [self._slots[i] for i in sorted(self._slots)]


class Client:
    """把请求发给模型，流式读回来。"""

    def __init__(self, config):
        self.config = config

    def stream(self, messages, tools=None):
        """发起一次流式请求。

        逐帧 yield Delta，调用方边收边显示。出错抛 LLMError。
        """
        body = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.config.endpoint, data, self.config.headers)

        try:
            resp = urllib.request.urlopen(req, timeout=self.config.timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise LLMError("服务返回 %s。%s" % (e.code, detail))
        except urllib.error.URLError as e:
            raise LLMError("连不上模型服务 %s。原因：%s" % (self.config.base_url, e.reason))
        except TimeoutError:
            raise LLMError("请求超时（%s 秒）。" % self.config.timeout)

        try:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    yield Delta(
                        content=delta.get("content") or "",
                        tool_calls=delta.get("tool_calls") or [],
                        finish_reason=choice.get("finish_reason"),
                    )
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError("读取响应中断：%s" % e)
        finally:
            resp.close()
