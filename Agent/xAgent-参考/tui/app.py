"""xAgent 的终端界面。

全部界面代码都在这里和 widgets/ 里，业务逻辑一行都没有 ——
它只是把 Agent 的事件画出来、把用户输入递进去。
"""
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Static

from core.agent import Agent
from core.config import find_config, load, ConfigError

from . import events
from .widgets import ChatLog, PromptInput

CSS = """
Screen {
    background: $surface;
}

#status {
    height: 1;
    background: $panel;
    color: $text-muted;
    padding: 0 1;
}

#log {
    height: 1fr;
    border: round $primary 30%;
    padding: 0 1;
}

#prompt {
    height: 5;
    border: round $accent;
    padding: 0 1;
}

#prompt:focus {
    border: round $accent-lighten-2;
}
"""


class XAgentApp(App):
    """把 core.agent.Agent 包成一个终端应用。"""

    TITLE = "xAgent"
    SUB_TITLE = "带 function call 的本地 Agent"
    CSS = CSS

    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", show=True),
        Binding("ctrl+n", "new_session", "新会话", show=True),
        Binding("ctrl+l", "clear_log", "清屏", show=True),
        Binding("f1", "show_tools", "工具清单", show=True),
    ]

    def __init__(self, agent=None, config_path=None):
        super().__init__()
        self._agent = agent
        self._config_path = config_path
        self._busy = False

    # ---- 组装界面 ----
    def compose(self):
        yield Header(show_clock=True)
        with Vertical():
            yield Static("正在准备…", id="status")
            yield ChatLog(id="log")
            yield PromptInput(id="prompt")
        yield Footer()

    def on_mount(self):
        self._setup_agent()
        self.query_one("#prompt", PromptInput).focus()

    def _setup_agent(self):
        """建 Agent 并把它的回调接到界面上。"""
        if self._agent is None:
            try:
                cfg = load(self._config_path or "config.ini")
                self._agent = Agent(config=cfg)
                where = find_config(self._config_path or "config.ini")
            except ConfigError as e:
                self._agent = Agent()
                self.query_one(ChatLog).add_error(str(e))
                self.query_one("#status", Static).update("配置缺失")
                return
        else:
            where = "(由调用方注入)"

        agent = self._agent
        agent.on_assistant_start = lambda: self.post_message(events.AssistantStart())
        agent.on_assistant_delta = lambda t: self.post_message(events.AssistantDelta(t))
        agent.on_assistant_end = lambda t, c: self.post_message(events.AssistantEnd(t, c))
        agent.on_tool_start = lambda n, a: self.post_message(events.ToolStarted(n, a))
        agent.on_tool_end = lambda n, r, e: self.post_message(events.ToolFinished(n, r, e))
        agent.on_error = lambda m: self.post_message(events.AgentFailed(m))

        cfg = agent.config
        model = cfg.model or "（未配置模型）"
        self.query_one("#status", Static).update(
            "模型 %s   │   %d 个工具   │   %s" % (model, len(agent.tools), where)
        )
        log = self.query_one(ChatLog)
        log.write("输入问题后回车发送，Ctrl+J 换行，F1 看工具清单。")
        log.write("")

    # ---- 用户输入 ----
    def on_prompt_input_submitted(self, event):
        text = event.value
        prompt = self.query_one("#prompt", PromptInput)
        prompt.clear_text()

        if self._busy:
            self.query_one(ChatLog).add_error("上一轮还没结束，稍等。")
            return

        log = self.query_one(ChatLog)
        log.add_user(text)
        self._busy = True
        self.query_one("#status", Static).update("思考中…")
        # Agent 走网络是阻塞的，必须扔到工作线程，否则界面会冻结
        self.run_worker(lambda: self._agent.ask(text), thread=True, exclusive=True)

    def _finish_turn(self):
        self._busy = False
        agent = self._agent
        model = agent.config.model or "（未配置模型）"
        self.query_one("#status", Static).update(
            "模型 %s   │   %d 个工具" % (model, len(agent.tools))
        )
        self.query_one("#prompt", PromptInput).focus()

    # ---- Agent 事件 ----
    def on_assistant_start(self, _event):
        self.query_one(ChatLog).start_assistant()

    def on_assistant_delta(self, event):
        self.query_one(ChatLog).push_delta(event.text)

    def on_assistant_end(self, event):
        log = self.query_one(ChatLog)
        # 这一轮如果最后是纯文本回答（没有工具调用），说明答案已经流式显示过了；
        # 如果是工具调用的回合、正文为空，就不用补标签。
        log.end_assistant(event.text)
        if not event.calls:
            self._finish_turn()

    def on_tool_started(self, event):
        self.query_one(ChatLog).add_tool(event.name, event.arguments)

    def on_tool_finished(self, event):
        self.query_one(ChatLog).add_tool_result(event.name, event.result, event.error)

    def on_agent_failed(self, event):
        self.query_one(ChatLog).add_error(event.message)
        self._finish_turn()

    # ---- 快捷键 ----
    def action_new_session(self):
        self._agent.reset()
        self.query_one(ChatLog).clear()
        self.query_one(ChatLog).write("已开新会话，历史已清空。")
        self.query_one(ChatLog).write("")

    def action_clear_log(self):
        self.query_one(ChatLog).clear()

    def action_show_tools(self):
        log = self.query_one(ChatLog)
        log.write("可用工具（%d 个）：" % len(self._agent.tools))
        for tool in self._agent.tools.schemas():
            fn = tool["function"]
            log.write("  · %s" % fn["name"])
            log.write("      %s" % fn["description"].split("。")[0])
        log.write("")


def run(config_path=None):
    """启动终端界面。"""
    XAgentApp(config_path=config_path).run()
