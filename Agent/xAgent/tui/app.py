"""Textual 终端界面:聊天窗口的实现。

架构要点:
- 界面通过"依赖注入"拿到 core.Agent 实例(ChatApp.__init__ 参数),
  自己完全不创建、不配置业务组件 —— 组装工作在 main.py 完成;
- 发送一条消息 = 启动一个异步后台任务(@work),遍历 agent.chat() 的事件流,
  按事件类型更新界面。这就是"UI 依赖 core,core 不依赖 UI"的落地方式。

把本文件替换成 Web / 桌面实现时,core 层一行都不用改。
"""
from __future__ import annotations

import asyncio
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
        ("escape", "cancel_chat", "取消当前对话"),
    ]

    def __init__(self, agent: Agent, mcp_status: list[str] | None = None) -> None:
        super().__init__()
        # 依赖注入:界面只依赖 core 的 Agent 抽象,不关心其内部构造
        self._agent = agent
        # MCP 服务器接入状态(启动欢迎语里展示,没有接入就是空列表)
        self._mcp_status = list(mcp_status or [])
        self._streaming = ""  # 当前正在流式输出的文本缓冲区
        self._chat_worker = None  # 当前对话的后台任务(按 Esc 时取消)

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
            placeholder=(
                "输入消息,回车发送  |  /help 命令  |  Esc 取消对话  |  Ctrl+Q 退出"
            ),
            id="input",
        )

    def on_mount(self) -> None:
        """界面挂载完成后:输出欢迎语,聚焦输入框。"""
        welcome = f"👋 欢迎使用 xAgent!当前模型:{self._agent.model_name}\n"
        if self._agent.has_agents_md:
            welcome += (
                "   已加载项目说明 AGENTS.md(会话开始时注入一次,之后全程复用)\n"
            )
        welcome += (
            f"   共 {len(self._agent.tool_names)} 个工具:文件、时间、命令行、联网、"
            "技能、工作区外读取和 MCP\n"
        )
        if self._agent.skill_names:
            names = "、".join(self._agent.skill_names)
            welcome += (
                f"   已发现 {len(self._agent.skill_names)} 个技能:{names}"
                "(任务匹配时用 loadSkill 加载完整说明)\n"
            )
        for issue in self._agent.skill_issues:
            welcome += f"   ⚠️ {issue}\n"
        welcome += (
            "   直接输入问题即可;输入 /help 查看命令,试试:\n"
            "   · 帮我把\"你好,世界\"写入 hello.txt,再读出来念给我听\n"
            "   · 看看当前工作目录在哪,现在几点了\n"
            "   · 用 fetchWeb 抓取 https://example.com 并总结一下内容\n"
            "   · 用 searchWeb 搜索“2026 年诺贝尔物理学奖得主”\n"
            "   · 帮我创建一个网页搜索技能\n"
        )
        if self._mcp_status:
            welcome += "   MCP 服务器接入情况:\n"
            welcome += "".join(f"   · {status}\n" for status in self._mcp_status)
            welcome += (
                "   · 用 mcp_ 开头的工具问问 MCP 服务器吧"
                "(服务器没启动的话它们会报连接失败)\n"
            )
        self.query_one("#log", RichLog).write(welcome)
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
        # 结束程序(与 practice01/02 的 exit / 退出 保持一致,同时支持 /quit)
        if lowered in ("exit", "quit", "退出", "/quit", "/exit"):
            self.exit()
            return
        # 清空对话(对应 core 的 Agent.reset)
        if lowered in ("reset", "清空", "/clear", "/reset"):
            self._reset_chat()
            return
        # 斜杠命令:不发给模型,直接在界面处理
        if lowered in ("/help", "/?"):
            self._show_help()
            return
        if lowered == "/status":
            self._show_status()
            return
        if lowered == "/skills":
            self._show_skills()
            return
        if lowered == "/mcp":
            self._show_mcp()
            return
        if text.startswith("/"):
            self._write_log(f"未知命令:{text}。输入 /help 查看可用命令。")
            return

        # 正常对话:交给后台任务处理,不阻塞界面
        self._chat_worker = self._run_chat(text)

    def action_clear_screen(self) -> None:
        """Ctrl+L:只清屏幕显示,不动对话历史。"""
        self.query_one("#log", RichLog).clear()

    def action_cancel_chat(self) -> None:
        """Esc:取消正在进行的对话(空闲时什么也不做)。"""
        worker = self._chat_worker
        if worker is None or worker.is_finished:
            return
        worker.cancel()

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
        except asyncio.CancelledError:
            # 用户按 Esc:保留已经显示的文字,提示已取消;历史已由 Agent 回滚
            self._flush_stream()
            log.write("⏹️ 已取消当前对话")
            raise
        finally:
            # 无论成功失败,恢复界面到可输入状态
            self._streaming = ""
            self._render_stream()
            input_widget.disabled = False
            input_widget.focus()
            self._set_status("ready")
            self._chat_worker = None

    # ---------- 斜杠命令与状态展示 ----------

    def _reset_chat(self) -> None:
        """清空对话历史(新会话会重新注入系统提示词和 AGENTS.md)。"""
        self._agent.reset()
        self.query_one("#log", RichLog).clear()
        self._set_status("ready")
        self._write_log(
            "🧹 已清空对话历史(系统提示词和 AGENTS.md 已在新会话中重新注入)"
        )

    def _show_help(self) -> None:
        """显示斜杠命令帮助。"""
        self._write_log(
            "可用命令:\n"
            "  /help    显示本帮助\n"
            "  /clear   清空对话历史(reset 同义)\n"
            "  /status  查看模型、工具、技能和 MCP 状态\n"
            "  /skills  列出已发现的技能\n"
            "  /mcp     查看 MCP 服务器接入情况\n"
            "  /quit    退出程序(exit 同义)\n"
            "  Esc      取消正在进行的对话\n"
            "  其他输入 直接交给 Agent 对话"
        )

    def _show_status(self) -> None:
        """显示当前模型、工具、技能和项目说明状态。"""
        tools = self._agent.tool_names
        lines = [
            f"模型:{self._agent.model_name}",
            f"工具:{len(tools)} 个({', '.join(tools)})",
            "技能:"
            + (
                "、".join(self._agent.skill_names)
                if self._agent.skill_names else "暂无"
            ),
            "系统提示词:" + ("已注入" if self._agent.has_system_prompt else "未注入"),
            "AGENTS.md:" + ("已加载" if self._agent.has_agents_md else "未找到"),
        ]
        if self._mcp_status:
            lines.append("MCP:")
            lines.extend(f"  - {status}" for status in self._mcp_status)
        self._write_log("\n".join(lines))

    def _show_skills(self) -> None:
        """列出已发现的技能。"""
        if not self._agent.skill_names:
            self._write_log(
                "当前没有发现 Agent Skill。可以让 Agent 用 createSkill 创建一个,"
                "或把技能放到 <项目根>/.agents/skills/<名称>/SKILL.md。"
            )
            return
        lines = [f"已发现 {len(self._agent.skill_names)} 个技能:"]
        lines.extend(f"- {name}" for name in self._agent.skill_names)
        lines.append("匹配任务时 Agent 会调用 loadSkill 读取完整说明。")
        self._write_log("\n".join(lines))

    def _show_mcp(self) -> None:
        """显示 MCP 服务器接入情况。"""
        if not self._mcp_status:
            self._write_log(
                "没有接入 MCP 服务器。可在 config.ini 的 [mcp] 段填写 HTTP 服务器,"
                "或在启动目录的 .mcp.json 中配置 stdio / HTTP 服务器。"
            )
            return
        self._write_log("MCP 服务器接入情况:\n" + "\n".join(
            f"- {status}" for status in self._mcp_status
        ))

    def _write_log(self, text: str) -> None:
        """往聊天记录里写一段系统提示。"""
        self.query_one("#log", RichLog).write(text)

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
            "ready": "✅ 就绪 — 输入消息开始对话(/help 命令 / Esc 取消 / Ctrl+Q 退出)",
            "thinking": "⏳ AI 正在思考…",
            "tool": f"🔧 正在执行工具 {tool} …",
        }
        self.query_one("#status", Static).update(texts.get(state, state))
