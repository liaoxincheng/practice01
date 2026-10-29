"""Agent 编排层：把模型、工具、会话历史串起来。

这一层不导入任何界面库。界面通过订阅事件（on_*）来显示过程，
换成 Web 或 Desktop 时只需实现同样的回调，业务逻辑一行不用改。

事件回调都是可选的，签名如下：

    on_assistant_start()                 一轮回答开始
    on_assistant_delta(text)             流式收到一段正文
    on_assistant_end(text, tool_calls)   正文结束（tool_calls 是拼好的调用列表）
    on_tool_start(name, arguments)       准备执行工具
    on_tool_end(name, result, error)     工具执行完
    on_error(message)                    出错
"""
import json

from .config import Config
from .llm.client import Client, LLMError, ToolCallBuffer
from .tools import default_registry

MAX_TOOL_ROUNDS = 12


class ToolCall:
    """一次完整的工具调用。"""

    def __init__(self, call_id, name, arguments):
        self.call_id = call_id
        self.name = name
        self.arguments = arguments

    def parsed(self):
        """把 arguments 解析成 dict，解析不了返回空 dict。"""
        try:
            value = json.loads(self.arguments or "{}")
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    def as_message(self):
        """塞回 messages 用的格式。"""
        return {
            "id": self.call_id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments or "{}"},
        }

    def __repr__(self):
        return "ToolCall(%s, %s)" % (self.name, self.arguments[:60])


class Agent:
    """一个有记忆、会用工具的对话代理。"""

    def __init__(self, config=None, registry=None, system_prompt=None):
        self.config = config or Config("http://127.0.0.1:11437/v1", "")
        self.client = Client(self.config)
        self.tools = registry if registry is not None else default_registry()
        self.history = []
        self.system_prompt = system_prompt

        # 事件回调，界面层按需挂
        self.on_assistant_start = None
        self.on_assistant_delta = None
        self.on_assistant_end = None
        self.on_tool_start = None
        self.on_tool_end = None
        self.on_error = None

    # ---- 回调辅助 ----
    def _emit(self, name, *args):
        fn = getattr(self, name, None)
        if fn:
            fn(*args)

    # ---- 会话管理 ----
    def reset(self):
        """清空历史，开新会话。"""
        self.history = []

    def messages(self):
        """发给模型的完整消息列表。"""
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend(self.history)
        return msgs

    def tool_names(self):
        return self.tools.names()

    # ---- 主流程 ----
    def ask(self, text):
        """提一个问题，跑完整个「回答-调工具-再回答」循环。

        返回最终的助手文本。出错时返回空串并把错误交给 on_error。
        """
        text = (text or "").strip()
        if not text:
            return ""

        self.history.append({"role": "user", "content": text})
        mark = len(self.history) - 1  # 出错时回滚到这里

        try:
            return self._run_loop(mark)
        except LLMError as e:
            # 整轮回滚：不能只弹最后一条，否则会留下没有对应 tool 响应的
            # assistant.tool_calls，下一轮请求必然被服务端拒绝。
            del self.history[mark:]
            self._emit("on_error", str(e))
            return ""

    def _run_loop(self, mark):
        schemas = self.tools.schemas()
        final_text = ""

        for _ in range(MAX_TOOL_ROUNDS):
            buffer = ToolCallBuffer()
            answer = ""
            self._emit("on_assistant_start")

            for delta in self.client.stream(self.messages(), schemas):
                if delta.content:
                    answer += delta.content
                    self._emit("on_assistant_delta", delta.content)
                if delta.tool_calls:
                    buffer.feed(delta.tool_calls)

            calls = [ToolCall(c["id"], c["name"], c["arguments"]) for c in buffer.ready()]
            self._emit("on_assistant_end", answer, calls)

            if not calls:
                self.history.append({"role": "assistant", "content": answer})
                return answer

            self.history.append(
                {
                    "role": "assistant",
                    "content": answer or None,
                    "tool_calls": [c.as_message() for c in calls],
                }
            )

            for call in calls:
                args = call.parsed()
                self._emit("on_tool_start", call.name, args)
                result, error = self.tools.execute(call.name, args)
                if error:
                    self._emit("on_tool_end", call.name, error, error)
                    content = "工具调用失败：%s" % error
                else:
                    self._emit("on_tool_end", call.name, result, None)
                    content = result
                self.history.append(
                    {"role": "tool", "tool_call_id": call.call_id, "content": content}
                )
            final_text = answer

        # 轮数用完还没收敛，把当前状态告诉调用方
        self._emit(
            "on_error",
            "连续 %d 轮都在调用工具还没给出结论，已停止。可以换个问法。" % MAX_TOOL_ROUNDS,
        )
        return final_text
