"""Textual 终端界面:聊天窗口的实现。

架构要点:
- 界面通过"依赖注入"拿到 core.Agent 实例(ChatApp.__init__ 参数),
  自己完全不创建、不配置业务组件 —— 组装工作在 main.py 完成;
- 发送一条消息 = 启动一个异步后台任务(@work),遍历 agent.chat() 的事件流,
  按事件类型更新界面。这就是"UI 依赖 core,core 不依赖 UI"的落地方式。

把本文件替换成 Web / 桌面实现时,core 层一行都不用改。
"""
from __future__ import annotations

import json

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Header, Input, RichLog, Static

from core import events
from core.agent import Agent

# 工具结果在界面上最多显示多少字符(完整结果仍会全部送给模型)
TOOL_RESULT_PREVIEW = 300


class ChatApp(App):
    """xAgent 终端聊天界面。"""

    TITLE = "xAgent · 本地大模型 Agent"
    SUB_TITLE = "core + tui 分层架构"
    CSS_PATH = "chat.css"

    # 快捷键
    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
        ("ctrl+l", "clear_screen", "清屏"),
    ]

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        # 依赖注入:界面只依赖 core 的 Agent 抽象,不关心其内部构造
        self._agent = agent
        self._streaming = ""  # 当前正在流式输出的文本缓冲区

    # ---------- 界面布局 ----------

    def compose(self) -> ComposeResult:
        """声明界面组件(从上到下):标题栏 / 聊天区 / 状态栏 / 输入框。"""
        yield Header(show_clock=True)
        with Vertical(id="chat-area"):
            # 聊天记录(历史消息都写在这里)
            yield RichLog(
                id="log",
                markup=False,   # 模型输出可能包含方括号等,按纯文本显示,防注入
                highlight=True,
                wrap=True,
                max_lines=1000,  # 只保留最近 1000 行,防止内存无限增长
            )
            # 正在流式输出的内容(打字机效果的"当前这一句")
            yield Static("", id="stream")
        yield Static("", id="status")
        yield Input(
            placeholder="输入消息,回车发送  |  exit/退出 结束  |  reset 清空对话  |  Ctrl+Q 退出",
            id="input",
        )

    def on_mount(self) -> None:
        """界面挂载完成后:输出欢迎语,聚焦输入框。"""
        self.query_one("#log", RichLog).write(
            f"👋 欢迎使用 xAgent!当前模型:{self._agent.model_name}\n"
            "   共 14 个工具:文件工具(read_file / write_file / edit_file /\n"
            "   list_dir / get_file_type / create_directory / delete_directory /\n"
            "   delete_file / rename_path)、时间工具(get_date / is_leap_year)、\n"
            "   命令行工具(RunCli)和联网工具(fetchWeb 抓网页 / searchWeb 搜索),试试:\n"
            "   · 帮我把\"你好,世界\"写入 hello.txt,再读出来念给我听\n"
            "   · ping 一下 127.0.0.1 看通不通\n"
            "   · 用 fetchWeb 抓取 https://example.com 并总结一下内容\n"
            "   · 用 searchWeb 搜索“2026 年诺贝尔物理学奖得主”(需先在 config.ini 的 [search] 段填写百度千帆 API Key)\n"
        )
        self._set_status("ready")
        self.query_one("#input", Input).focus()

    # ---------- 用户输入 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """用户在输入框按下回车。"""
        text = event.value.strip()
        event.input.value = ""  # 清空输入框
        event.input.scroll_to(0, 0)  # 复位滚动,让占位符从开头显示

        if not text:
            return

        lowered = text.lower()
        # 结束程序(与 practice01/02 的 exit / 退出 保持一致)
        if lowered in ("exit", "quit", "退出"):
            self.exit()
            return
        # 清空对话(对应 core 的 Agent.reset)
        if lowered in ("reset", "清空"):
            self._agent.reset()
            self.query_one("#log", RichLog).clear()
            self._set_status("ready")
            return

        # 正常对话:交给后台任务处理,不阻塞界面
        self._run_chat(text)

    def action_clear_screen(self) -> None:
        """Ctrl+L:只清屏幕显示,不动对话历史。"""
        self.query_one("#log", RichLog).clear()

    # ---------- 对话后台任务(消费 core 的事件流) ----------

    @work(exclusive=True, group="chat")
    async def _run_chat(self, text: str) -> None:
        """后台任务:遍历 core 的事件流并更新界面。

        exclusive=True 保证同一时间只有一轮对话在跑。
        """
        log = self.query_one("#log", RichLog)
        input_widget = self.query_one("#input", Input)

        input_widget.disabled = True  # 对话期间禁止输入
        log.write(f"👤 你: {text}")
        self._streaming = ""
        self._set_status("thinking")

        try:
            async for ev in self._agent.chat(text):
                if isinstance(ev, events.TextChunk):
                    # 流式文本增量:累积到缓冲区并实时显示
                    self._streaming += ev.text
                    self._render_stream()
                elif isinstance(ev, events.ToolCallStarted):
                    # 模型要调用工具了:先把已经流出的文本落进聊天记录
                    self._flush_stream()
                    args = json.dumps(ev.arguments, ensure_ascii=False)
                    log.write(f"🔧 调用工具: {ev.name}({args})")
                    self._set_status("tool", tool=ev.name)
                elif isinstance(ev, events.ToolCallFinished):
                    preview = (
                        ev.result
                        if len(ev.result) <= TOOL_RESULT_PREVIEW
                        else ev.result[:TOOL_RESULT_PREVIEW] + "…"
                    )
                    mark = "✅" if ev.ok else "⚠️"
                    log.write(f"{mark} 工具结果: {preview}")
                elif isinstance(ev, events.ReplyDone):
                    # 一轮回复结束:把缓冲区内容落进聊天记录
                    self._flush_stream()
                elif isinstance(ev, events.AgentError):
                    self._flush_stream()
                    log.write(f"❌ {ev.message}")
        finally:
            # 无论成功失败,恢复界面到可输入状态
            self._streaming = ""
            self._render_stream()
            input_widget.disabled = False
            input_widget.focus()
            self._set_status("ready")

    # ---------- 界面小工具方法 ----------

    def _flush_stream(self) -> None:
        """把流式缓冲区的内容写入聊天记录,并清空缓冲区。"""
        if self._streaming:
            self.query_one("#log", RichLog).write(self._streaming)
            self._streaming = ""
        self._render_stream()

    def _render_stream(self) -> None:
        """刷新"正在流式输出"区域。"""
        label = self.query_one("#stream", Static)
        label.update("🤖 " + self._streaming if self._streaming else "")

    def _set_status(self, state: str, tool: str = "") -> None:
        """更新底部状态栏。"""
        texts = {
            "ready": "✅ 就绪 — 输入消息开始对话(exit 退出 / reset 清空 / Ctrl+Q 退出)",
            "thinking": "⏳ AI 正在思考…",
            "tool": f"🔧 正在执行工具 {tool} …",
        }
        self.query_one("#status", Static).update(texts.get(state, state))
