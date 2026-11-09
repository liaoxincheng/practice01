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
from collections.abc import Callable

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import Button, Header, Input, RichLog, Static

from core import events
from core.agent import Agent

# 工具结果在界面上最多显示多少字符(完整结果仍会全部送给模型)
TOOL_RESULT_PREVIEW = 300

# 输入框的斜杠命令补全列表(输入 / 开头时 Textual 会给出建议)
SLASH_COMMANDS = [
    "/help",
    "/status",
    "/compact",
    "/save",
    "/resume",
    "/sessions",
    "/new",
    "/clear",
    "/skills",
    "/mcp",
    "/quit",
]

# /sessions 一次最多列出多少个归档(避免刷屏)
MAX_SESSION_ROWS = 20


class ConfigNoticeScreen(ModalScreen[None]):
    """首次启动自动生成 config.ini 时的提醒弹窗。"""

    BINDINGS = [
        ("escape", "dismiss_notice", "关闭"),
        ("enter", "dismiss_notice", "关闭"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="config-dialog"):
            yield Static("⚠️ 已自动生成 config.ini", id="config-title")
            yield Static(self._message, id="config-message")
            yield Button("我知道了", id="config-ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_dismiss_notice(self) -> None:
        self.dismiss(None)


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

    def __init__(
        self,
        agent: Agent,
        mcp_status: list[str] | None = None,
        startup_notice: str = "",
        mcp_loader: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__()
        # 依赖注入:界面只依赖 core 的 Agent 抽象,不关心其内部构造
        self._agent = agent
        # MCP 服务器接入状态(启动欢迎语里展示,没有接入就是空列表)
        self._mcp_status = list(mcp_status or [])
        # 启动时已知的配置问题(后台加载完成后仍要保留在状态里)
        self._mcp_issues = [
            status for status in self._mcp_status if "正在后台连接" not in status
        ]
        # 首次启动自动生成配置等需要弹窗提醒的内容(空字符串 = 不弹)
        self._startup_notice = startup_notice.strip()
        # 延迟加载 MCP 的函数(在后台线程执行,避免启动时卡住界面)
        self._mcp_loader = mcp_loader
        self._streaming = ""  # 当前正在流式输出的文本缓冲区
        self._chat_worker = None  # 当前对话的后台任务(按 Esc 时取消)
        self._mcp_worker = None  # 后台 MCP 加载任务

    # ---------- 界面布局 ----------

    def compose(self) -> ComposeResult:
        """声明界面组件(从上到下):标题栏 / 聊天区 / 状态栏 / 输入框。"""
        yield Header(show_clock=True)
        # 双栏布局:左侧聊天区,右侧运行状态(上下文 / 会话 / MCP)
        with Horizontal(id="main"):
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
            with Vertical(id="sidebar"):
                yield Static("📊 运行状态", id="side-title")
                yield Static("", id="side-context")
                yield Static("", id="side-sessions")
                yield Static("", id="side-mcp")
        yield Static("", id="status")
        yield Input(
            placeholder=(
                "输入消息,回车发送  |  /help 命令  |  Esc 取消对话  |  Ctrl+Q 退出"
            ),
            suggester=SuggestFromList(SLASH_COMMANDS, case_sensitive=False),
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
            "技能、工作区外读取、上下文归档和 MCP\n"
        )
        if self._agent.archive_dir:
            welcome += (
                f"   会话归档目录:{self._agent.archive_dir}\n"
                "   输入 /compact 压缩上下文,/save 保存会话,/resume 恢复会话\n"
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
        self._refresh_sidebar()
        self.query_one("#input", Input).focus()
        if self._mcp_loader is not None:
            self._mcp_worker = self._load_mcp_in_background()
        if self._startup_notice:
            self.push_screen(ConfigNoticeScreen(self._startup_notice))

    # ---------- 用户输入 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """用户在输入框按下回车。"""
        text = event.value.strip()
        event.input.value = ""  # 清空输入框
        event.input.scroll_to(0, 0)  # 复位滚动,让占位符从开头显示

        if not text:
            return

        # 命令可能带参数(如 /save 我的会话、/compact 4),先拆出命令和参数
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        # 结束程序(与 practice01/02 的 exit / 退出 保持一致,同时支持 /quit)
        if command in ("exit", "quit", "退出", "/quit", "/exit"):
            self.exit()
            return
        # 清空对话(对应 core 的 Agent.reset;/new 与 /clear 同义)
        if command in ("reset", "清空", "/clear", "/reset", "/new"):
            self._reset_chat()
            return
        # 斜杠命令:不发给模型,直接在界面处理
        if command in ("/help", "/?"):
            self._show_help()
            return
        if command == "/status":
            self._show_status()
            return
        if command == "/skills":
            self._show_skills()
            return
        if command == "/mcp":
            self._show_mcp()
            return
        if command == "/sessions":
            self._show_sessions()
            return
        if command == "/save":
            self._write_log(self._agent.save_session(argument or None))
            self._set_status("ready")
            self._refresh_sidebar()
            return
        if command == "/resume":
            if not argument:
                self._write_log(
                    "用法:/resume <会话文件名、名字片段或 last>\n"
                    "先输入 /sessions 查看已保存的会话。"
                )
                return
            result = self._agent.resume_session(argument)
            self._write_log(result)
            if result.startswith("✅"):
                self._replay_conversation()
            self._set_status("ready")
            self._refresh_sidebar()
            return
        if command == "/compact":
            keep: int | None = None
            if argument:
                try:
                    keep = int(argument)
                except ValueError:
                    self._write_log(
                        f"参数 {argument!r} 不是整数。用法:/compact [保留轮数,1-20]"
                    )
                    return
                if not 1 <= keep <= 20:
                    self._write_log("保留轮数必须在 1 到 20 之间。")
                    return
            self._chat_worker = self._run_compact(keep)
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

    @work(exclusive=True, group="mcp")
    async def _load_mcp_in_background(self) -> None:
        """后台连接 MCP 服务器,避免启动时界面卡住。

        ``register_mcp_ops`` 内部使用 asyncio.run,所以放到线程里执行;
        工具注册到同一个 ToolRegistry,模型下一轮请求就能看到它们。
        """
        loader = self._mcp_loader
        if loader is None:
            return
        self._mcp_loader = None
        self._write_log(
            "🔌 MCP 服务器正在后台连接,现在就可以先用内置工具聊天…"
        )
        try:
            statuses = await asyncio.to_thread(loader)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 后台失败不影响内置工具
            statuses = [f"MCP 后台加载失败:{exc}"]

        self._mcp_status = [*self._mcp_issues, *statuses]
        self._refresh_sidebar()
        if statuses:
            self._write_log(
                "🔌 MCP 后台加载完成:\n"
                + "\n".join(f"- {status}" for status in statuses)
            )
        else:
            self._write_log("🔌 MCP 后台加载完成:没有可接入的 MCP 服务器")
        self._mcp_worker = None

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
            self._refresh_sidebar()
            self._chat_worker = None

    @work(exclusive=True, group="chat")
    async def _run_compact(self, keep_recent_turns: int | None) -> None:
        """后台任务:压缩上下文(归档完整历史 → 生成摘要 → 替换旧消息)。"""
        log = self.query_one("#log", RichLog)
        input_widget = self.query_one("#input", Input)

        input_widget.disabled = True
        self._set_status("compacting")
        log.write("🗜️ 正在压缩上下文(先归档完整历史,再生成摘要)…")
        try:
            result = await self._agent.compact(keep_recent_turns)
            log.write(result)
        except asyncio.CancelledError:
            log.write("⏹️ 已取消上下文压缩(聊天历史未改动)")
            raise
        except Exception as exc:  # noqa: BLE001 —— 界面兜底,避免后台任务崩溃
            log.write(f"❌ 压缩上下文失败:{exc}")
        finally:
            input_widget.disabled = False
            input_widget.focus()
            self._set_status("ready")
            self._refresh_sidebar()
            self._chat_worker = None

    # ---------- 斜杠命令与状态展示 ----------

    def _reset_chat(self) -> None:
        """清空对话历史(新会话会重新注入系统提示词和 AGENTS.md)。"""
        self._agent.reset()
        self.query_one("#log", RichLog).clear()
        self._set_status("ready")
        self._refresh_sidebar()
        self._write_log(
            "🧹 已清空对话历史(系统提示词和 AGENTS.md 已在新会话中重新注入)"
        )

    def _replay_conversation(self) -> None:
        """把恢复的会话回放到聊天记录里,方便用户继续之前的上下文。"""
        messages = self._agent.conversation_messages
        if not messages:
            return
        log = self.query_one("#log", RichLog)
        log.write("📂 已恢复的对话记录(下面的历史已重新载入模型上下文):")
        for message in messages:
            role = message.get("role")
            content = message.get("content") or ""
            if role == "user":
                log.write(f"👤 你: {content}")
            elif role == "assistant":
                if content:
                    log.write(f"🤖 助手: {content}")
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") or {}
                    log.write(
                        f"🔧 调用工具: {function.get('name', '')}"
                        f"({function.get('arguments', '')})"
                    )
            elif role == "tool":
                preview = (
                    str(content)
                    if len(str(content)) <= TOOL_RESULT_PREVIEW
                    else str(content)[:TOOL_RESULT_PREVIEW] + "…"
                )
                log.write(f"🔧 工具结果: {preview}")
            elif role == "system":
                log.write(f"🗜️ {content}")

    def _show_help(self) -> None:
        """显示斜杠命令帮助。"""
        self._write_log(
            "可用命令:\n"
            "  /help              显示本帮助\n"
            "  /new               开始新会话(/clear、/reset 同义)\n"
            "  /compact [轮数]    压缩较早的对话,保留最近若干轮(默认 3,范围 1-20)\n"
            "  /save [名字]       把当前会话保存到归档目录\n"
            "  /resume <名字|last> 恢复归档会话(恢复前会自动归档当前对话)\n"
            "  /sessions          列出已保存的会话\n"
            "  /status            查看模型、工具、技能、MCP 和上下文占用\n"
            "  /skills            列出已发现的技能\n"
            "  /mcp               查看 MCP 服务器接入情况\n"
            "  /quit              退出程序(exit 同义)\n"
            "  Esc                取消正在进行的对话或压缩\n"
            "  其他输入           直接交给 Agent 对话"
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
        stats = self._agent.context_stats
        lines.append(
            f"上下文:{stats.messages} 条消息(对话 {stats.conversation_messages} 条,"
            f"历史摘要 {stats.summary_messages} 条),"
            f"约 {stats.estimated_tokens} / {stats.max_tokens} tokens"
        )
        if stats.summary_messages:
            lines.append("  说明:当前上下文包含压缩摘要,旧细节可用 recallContext 检索")
        if stats.estimated_tokens >= stats.max_tokens * 0.8:
            lines.append(
                "  ⚠️ 上下文接近配置上限,建议输入 /compact 保留最近几轮"
            )
        if self._agent.archive_dir:
            lines.append(f"会话归档目录:{self._agent.archive_dir}")
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

    def _show_sessions(self) -> None:
        """列出归档目录里的会话,并提示如何恢复。"""
        archive_dir = self._agent.archive_dir
        if archive_dir is None:
            self._write_log(
                "未启用会话归档。请在 config.ini 的 [context] 段配置 archive_dir。"
            )
            return
        sessions = self._agent.list_sessions()
        if not sessions:
            self._write_log(
                f"还没有保存的会话。归档目录:{archive_dir}\n"
                "输入 /save 可手动保存当前对话;/compact 压缩前也会自动归档。"
            )
            return

        shown = sessions[:MAX_SESSION_ROWS]
        lines = [f"已保存 {len(sessions)} 个会话(归档目录:{archive_dir}):"]
        for info in shown:
            preview = f" — {info.summary_preview}" if info.summary_preview else ""
            lines.append(
                f"- {info.name} | {info.created_at} | "
                f"{info.message_count} 条消息{preview}"
            )
        if len(sessions) > len(shown):
            lines.append(f"……还有 {len(sessions) - len(shown)} 个未显示")
        lines.append(
            "恢复:/resume <文件名或名字片段>,最新会话可直接 /resume last"
        )
        self._write_log("\n".join(lines))

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

    @staticmethod
    def _session_label(filename: str) -> str:
        """把归档文件名压成侧边栏可读的名字(去掉时间戳前缀)。"""
        stem = filename[:-5] if filename.endswith(".json") else filename
        parts = stem.split("-", 3)
        label = parts[3] if len(parts) == 4 else stem
        if len(label) > 22:
            label = label[:21] + "…"
        return label

    def _refresh_sidebar_context(self) -> None:
        """刷新右侧栏的上下文占用信息。"""
        try:
            widget = self.query_one("#side-context", Static)
        except Exception:  # noqa: BLE001 —— 界面尚未挂载时安全跳过
            return
        stats = self._agent.context_stats
        percent = min(
            100, round(stats.estimated_tokens / max(stats.max_tokens, 1) * 100)
        )
        lines = [
            "上下文",
            f"  对话 {stats.conversation_messages} 条"
            f"(摘要 {stats.summary_messages} 条)",
            f"  约 {stats.estimated_tokens} / {stats.max_tokens} tokens({percent}%)",
        ]
        if percent >= 80:
            lines.append("  ⚠️ 建议输入 /compact")
        archive_dir = self._agent.archive_dir
        lines.append(f"  归档:{archive_dir if archive_dir else '未启用'}")
        widget.update("\n".join(lines))

    def _refresh_sidebar(self) -> None:
        """刷新右侧栏:上下文、最近归档会话、MCP 接入状态。"""
        self._refresh_sidebar_context()

        try:
            sessions_widget = self.query_one("#side-sessions", Static)
            mcp_widget = self.query_one("#side-mcp", Static)
        except Exception:  # noqa: BLE001
            return

        sessions = self._agent.list_sessions()
        if not sessions:
            sessions_text = "最近会话\n  暂无(/save 保存)"
        else:
            lines = [f"最近会话({len(sessions)})"]
            for info in sessions[:3]:
                created = (
                    info.created_at[5:16].replace("T", " ")
                    if len(info.created_at) >= 16
                    else info.created_at
                )
                label = self._session_label(info.name)
                lines.append(f"  • {created} {label}")
            if len(sessions) > 3:
                lines.append(f"  …另有 {len(sessions) - 3} 个(输入 /sessions)")
            sessions_text = "\n".join(lines)
        sessions_widget.update(sessions_text)

        if not self._mcp_status:
            mcp_text = "MCP 服务器\n  未接入"
        else:
            lines = [f"MCP 服务器({len(self._mcp_status)})"]
            for status in self._mcp_status[:4]:
                short = status.split("(")[0].strip()
                if short.startswith("服务器 "):
                    short = short[3:]
                if len(short) > 30:
                    short = short[:29] + "…"
                lines.append(f"  • {short}")
            if len(self._mcp_status) > 4:
                lines.append(f"  …另有 {len(self._mcp_status) - 4} 台(输入 /mcp)")
            mcp_text = "\n".join(lines)
        mcp_widget.update(mcp_text)

    def _context_hint(self) -> str:
        """状态栏里的上下文占用提示(估算值,不用于计费)。"""
        stats = self._agent.context_stats
        percent = min(
            100, round(stats.estimated_tokens / max(stats.max_tokens, 1) * 100)
        )
        summary = (
            f",摘要 {stats.summary_messages} 条" if stats.summary_messages else ""
        )
        return (
            f"上下文 {stats.conversation_messages} 条{summary} / "
            f"约 {stats.estimated_tokens} tokens({percent}%)"
        )

    def _set_status(self, state: str, tool: str = "") -> None:
        """更新底部状态栏(始终附带上下文估算占用)。"""
        texts = {
            "ready": "✅ 就绪 — 输入消息开始对话(/help 命令 / Esc 取消 / Ctrl+Q 退出)",
            "thinking": "⏳ AI 正在思考…",
            "tool": f"🔧 正在执行工具 {tool} …",
            "compacting": "🗜️ 正在压缩上下文…",
        }
        hint = self._context_hint()
        suffix = f"  |  {hint}"
        if state == "ready" and self._agent.context_stats.estimated_tokens >= (
            self._agent.context_stats.max_tokens * 0.8
        ):
            suffix += "  |  建议 /compact"
        self.query_one("#status", Static).update(
            texts.get(state, state) + suffix
        )
        self._refresh_sidebar_context()
