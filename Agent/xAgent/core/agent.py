"""Agent 核心:整个项目最核心的业务编排逻辑(与 UI 完全无关)。

职责:
1. 维护聊天历史(多轮上下文);
2. 把用户消息交给 LLM 流式请求,并把文本增量事件转发出去;
3. 当 LLM 要求调用工具时,通过 ToolRegistry 执行工具,
   把结果作为 tool 消息送回 LLM,循环往复,直到 LLM 不再调用工具;
4. 全程通过"异步生成器"产出 events.py 中定义的标准事件。

UI 层(现在是 tui/,未来可以是 web/、desktop/)只需:
    async for event in agent.chat(user_input):
        ... 根据事件类型渲染界面 ...
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from . import events
from .agents_md import build_agents_md_message
from .context import (
    ArchiveStore,
    ContextStats,
    SessionInfo,
    estimate_messages_tokens,
    message_text,
)
from .llm import LLMClient
from .tools import ToolRegistry

# 上下文压缩时给模型的摘要指令(单独放在这里,便于调整)
SUMMARY_SYSTEM_PROMPT = (
    "你是对话摘要助手。请把下面的历史对话压缩成一段中文摘要,保留:"
    "用户的目标、约束和明确要求;已经完成的操作、关键结果、文件路径、"
    "命令和错误原因;尚未完成的事项和下一步。"
    "不要编造未出现的信息,直接输出摘要正文,不要加标题,尽量控制在 800 字以内。"
)


def _safe_parse_arguments(raw: str) -> dict:
    """把模型给的参数 JSON 字符串解析成 dict(仅用于界面展示)。

    解析失败不中断流程(执行时由 ToolRegistry 再解析并给出错误)。
    """
    try:
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {"raw": raw}
    except json.JSONDecodeError:
        return {"raw": raw}


class Agent:
    """与 UI 无关的对话 Agent(可被任何界面复用)。"""

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        max_tool_rounds: int = 5,
        agents_md: str | None = None,
        system_prompt: str | None = None,
        skill_names: tuple[str, ...] | list[str] = (),
        skill_issues: tuple[str, ...] | list[str] = (),
        archive_dir: str | Path | None = None,
        keep_recent_turns: int = 3,
        max_context_tokens: int = 12000,
        summary_max_chars: int = 4000,
    ) -> None:
        """
        :param llm: LLM 客户端(依赖注入,方便测试时替换成假实现)
        :param tools: 工具注册中心(依赖注入)
        :param max_tool_rounds: 一轮对话中工具调用的最大来回数
        :param agents_md: AGENTS.md 的正文;会话开始时作为一条 system 消息注入,
            之后一直保留在历史里(一次注入,全程复用)
        :param system_prompt: 系统提示词(工作区规则 + 技能清单 + MCP 状态),
            会话开始时注入一次;为 None 时不注入
        :param skill_names: 启动时发现的技能名称(仅供界面展示)
        :param skill_issues: 技能扫描时的问题(仅供界面展示)
        :param archive_dir: 会话归档目录;为 None 时关闭压缩/保存/恢复功能
        :param keep_recent_turns: 压缩时保留最近多少轮完整对话
        :param max_context_tokens: 上下文估算上限(只用于状态栏提示)
        :param summary_max_chars: 压缩摘要的最大保留字符数(超出截断)
        """
        self._llm = llm
        self._tools = tools
        self._max_tool_rounds = max_tool_rounds
        self._agents_md = (agents_md or "").strip()
        self._system_prompt = (system_prompt or "").strip()
        self._skill_names = tuple(skill_names)
        self._skill_issues = tuple(skill_issues)
        self._archive = ArchiveStore(archive_dir) if archive_dir is not None else None
        self._keep_recent_turns = max(1, min(int(keep_recent_turns), 20))
        self._max_context_tokens = max(1000, int(max_context_tokens))
        self._summary_max_chars = max(500, min(int(summary_max_chars), 20_000))
        self._messages: list[dict] = []  # 聊天历史(OpenAI messages 格式)
        self._seed_count = 0  # 历史开头有几条是系统提示词/AGENTS.md(压缩时保留)
        self._seed_session_messages()

    # ---------- 对外只读信息 ----------

    @property
    def model_name(self) -> str:
        """当前使用的模型名(供界面展示)。"""
        return self._llm.model_name

    @property
    def tool_names(self) -> list[str]:
        """当前注册的全部工具名(供界面展示工具数量和 MCP 接入情况)。"""
        return self._tools.names()

    @property
    def has_agents_md(self) -> bool:
        """本次会话是否加载了 AGENTS.md(供界面展示)。"""
        return bool(self._agents_md)

    @property
    def has_system_prompt(self) -> bool:
        """本次会话是否注入了系统提示词(供界面展示)。"""
        return bool(self._system_prompt)

    @property
    def skill_names(self) -> tuple[str, ...]:
        """启动时发现的技能名称(供界面展示)。"""
        return self._skill_names

    @property
    def skill_issues(self) -> tuple[str, ...]:
        """技能扫描时的问题(供界面展示)。"""
        return self._skill_issues

    @property
    def messages(self) -> list[dict]:
        """聊天历史的副本(只读,避免外部误改)。"""
        return list(self._messages)

    @property
    def conversation_messages(self) -> list[dict]:
        """只含对话正文的副本(不含系统提示词和 AGENTS.md)。"""
        return list(self._conversation_messages())

    @property
    def context_stats(self) -> ContextStats:
        """当前上下文的消息数、估算 Token 和摘要条数(供界面展示)。"""
        conversation = self._messages[self._seed_count:]
        summary_messages = sum(
            1 for message in conversation if message.get("role") == "system"
        )
        return ContextStats(
            messages=len(self._messages),
            conversation_messages=len(conversation),
            estimated_tokens=estimate_messages_tokens(self._messages),
            max_tokens=self._max_context_tokens,
            summary_messages=summary_messages,
        )

    @property
    def archive_dir(self) -> Path | None:
        """当前的会话归档目录(未启用归档时为 None)。"""
        return self._archive.directory if self._archive is not None else None

    @property
    def keep_recent_turns(self) -> int:
        """压缩时默认保留的最近轮数。"""
        return self._keep_recent_turns

    def reset(self) -> None:
        """清空聊天历史,开始新一轮对话。

        新一轮会话同样会重新注入系统提示词和 AGENTS.md。
        """
        self._messages.clear()
        self._seed_session_messages()

    def list_sessions(self) -> list[SessionInfo]:
        """列出归档目录里的会话(按时间从新到旧)。"""
        if self._archive is None:
            return []
        return self._archive.list_sessions()

    # ---------- 内部辅助 ----------

    def _seed_messages(self) -> list[dict]:
        """生成会话开头要注入的消息(系统提示词在前,AGENTS.md 在后)。"""
        seeds: list[dict] = []
        if self._system_prompt:
            seeds.append({"role": "system", "content": self._system_prompt})
        if self._agents_md:
            seeds.append(build_agents_md_message(self._agents_md))
        return seeds

    def _seed_session_messages(self) -> None:
        """在会话开始时注入系统提示词和 AGENTS.md(各只做一次)。"""
        seeds = self._seed_messages()
        self._messages.extend(seeds)
        self._seed_count = len(seeds)

    def _conversation_messages(self) -> list[dict]:
        """只看用户/模型/工具消息,不含开头的系统提示词与 AGENTS.md。"""
        return self._messages[self._seed_count:]

    @staticmethod
    def _split_for_compaction(
        conversation: list[dict], keep_turns: int
    ) -> tuple[list[dict], list[dict]]:
        """把对话拆成"要摘要的旧消息"和"保留的最近 N 轮"。

        一轮从一条 user 消息开始,到下一次 user 消息之前结束。
        工具调用的 assistant/tool 消息会跟着所属的那一轮保留,不会拆散。
        """
        user_indices = [
            index
            for index, message in enumerate(conversation)
            if message.get("role") == "user"
        ]
        if len(user_indices) <= keep_turns:
            return [], list(conversation)
        split_at = user_indices[-keep_turns]
        return list(conversation[:split_at]), list(conversation[split_at:])

    @staticmethod
    def _build_transcript(messages: list[dict]) -> str:
        """把旧消息拼成给摘要模型看的纯文本记录。"""
        labels = {
            "system": "历史摘要",
            "user": "用户",
            "assistant": "助手",
            "tool": "工具结果",
        }
        lines: list[str] = []
        for message in messages:
            text = message_text(message).strip()
            if not text:
                continue
            if len(text) > 1500:
                text = text[:1500] + "…"
            lines.append(f"[{labels.get(message.get('role'), message.get('role'))}] {text}")
        transcript = "\n".join(lines)
        # 超长历史只保留后半段,防止摘要请求本身超出模型上下文
        limit = 48000
        if len(transcript) > limit:
            transcript = "(更早的内容已省略)\n" + transcript[-limit:]
        return transcript

    def _fallback_summary(self, messages: list[dict]) -> str:
        """模型摘要不可用时的本地摘要:逐条截取,保证压缩不会失败。"""
        labels = {
            "system": "历史摘要",
            "user": "用户",
            "assistant": "助手",
            "tool": "工具结果",
        }
        lines = ["(模型摘要不可用,以下是本地生成的关键信息)"]
        for message in messages:
            text = " ".join(message_text(message).split())
            if not text:
                continue
            if len(text) > 300:
                text = text[:300] + "…"
            lines.append(f"- {labels.get(message.get('role'), message.get('role'))}:{text}")
        return "\n".join(lines)[: self._summary_max_chars]

    async def _summarize(self, messages: list[dict]) -> str:
        """调用模型生成旧对话摘要(失败由调用方决定是否回退)。"""
        prompt = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "请压缩下面的历史对话:\n\n" + self._build_transcript(messages),
            },
        ]
        parts: list[str] = []
        async for event in self._llm.stream_chat(prompt, tools=None):
            if isinstance(event, events.TextChunk):
                parts.append(event.text)
        return "".join(parts).strip()

    # ---------- 上下文压缩与会话管理 ----------

    async def compact(self, keep_recent_turns: int | None = None) -> str:
        """把较早的对话压缩成一条摘要,保留最近若干轮。

        步骤:
        1. 先把完整对话归档到 archive_dir(失败则不改动上下文);
        2. 让模型把旧消息总结成一段中文摘要;模型失败时用本地摘要兜底;
        3. 用"系统提示词 + AGENTS.md + 摘要 + 最近 N 轮"替换聊天历史。

        压缩后的旧细节仍可通过 recallContext 工具检索归档。
        """
        if self._archive is None:
            return "错误:未配置上下文归档目录([context] archive_dir),无法压缩"
        try:
            keep = (
                int(keep_recent_turns)
                if keep_recent_turns is not None
                else self._keep_recent_turns
            )
        except (TypeError, ValueError):
            keep = self._keep_recent_turns
        keep = max(1, min(keep, 20))

        conversation = self._conversation_messages()
        old, recent = self._split_for_compaction(conversation, keep)
        if not old:
            return (
                f"当前对话不足 {keep + 1} 轮,还不需要压缩"
                f"(保留最近 {keep} 轮)。"
            )

        before = self.context_stats
        try:
            # 归档写磁盘可能较慢,放到线程里避免阻塞事件循环
            archive_path = await asyncio.to_thread(
                self._archive.save,
                conversation,
                name="before-compact",
                summary="上下文压缩前的自动归档",
            )
        except Exception as exc:  # noqa: BLE001 —— 归档失败时绝不改动上下文
            return f"错误:压缩前归档失败,本次未修改上下文:{exc}"

        note = ""
        try:
            summary = await self._summarize(old)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 模型摘要失败时本地兜底
            summary = self._fallback_summary(old)
            note = f"模型摘要失败,已改用本地摘要:{exc}"
        if not summary.strip():
            summary = self._fallback_summary(old)
            note = "模型返回空摘要,已改用本地摘要。"

        summary = summary.strip()
        if len(summary) > self._summary_max_chars:
            summary = summary[: self._summary_max_chars] + "…"

        seeds = self._seed_messages()
        summary_message = {
            "role": "system",
            "content": (
                "[历史对话摘要]\n"
                f"{summary}\n\n"
                f"完整历史已归档到 {archive_path.name}。"
                "当前上下文只保留了最近的对话;"
                "需要更早的细节时,调用 recallContext 按关键字检索归档。"
            ),
        }
        self._messages[:] = [*seeds, summary_message, *recent]
        self._seed_count = len(seeds)

        after = self.context_stats
        lines = [
            "✅ 上下文已压缩",
            f"- 摘要来源:{'本地摘要' if note else '模型摘要'}",
            f"- 压缩前:{before.messages} 条消息,约 {before.estimated_tokens} tokens",
            f"- 压缩后:{after.messages} 条消息,约 {after.estimated_tokens} tokens",
            f"- 保留最近 {keep} 轮对话",
            f"- 完整历史归档:{archive_path}",
            "- 需要旧细节时,模型可调用 recallContext 检索",
        ]
        if note:
            lines.append(f"- 注意:{note}")
        return "\n".join(lines)

    def save_session(self, name: str | None = None) -> str:
        """把当前对话保存成一个归档会话文件。"""
        if self._archive is None:
            return "错误:未配置上下文归档目录([context] archive_dir),无法保存"
        conversation = self._conversation_messages()
        if not conversation:
            return "当前对话为空,没有可保存的内容。"
        try:
            path = self._archive.save(conversation, name=name)
        except Exception as exc:  # noqa: BLE001
            return f"错误:保存会话失败:{exc}"
        return (
            f"✅ 已保存当前会话:{path.name}\n"
            f"- 消息数:{len(conversation)}\n"
            f"- 文件:{path}\n"
            f"- 恢复命令:/resume {path.name}"
        )

    def resume_session(self, ref: str) -> str:
        """载入一个归档会话,替换当前对话(恢复前会自动归档当前对话)。"""
        if self._archive is None:
            return "错误:未配置上下文归档目录([context] archive_dir),无法恢复"
        try:
            record, conversation = self._archive.load(ref)
        except ValueError as exc:
            return f"错误:{exc}"
        except Exception as exc:  # noqa: BLE001
            return f"错误:恢复会话失败:{exc}"

        backup_note = ""
        current = self._conversation_messages()
        if current:
            try:
                backup_path = self._archive.save(
                    current,
                    name="before-resume",
                    summary="恢复其他会话前的自动归档",
                )
            except Exception as exc:  # noqa: BLE001 —— 不覆盖用户现有对话
                return (
                    "错误:恢复前自动归档当前对话失败,"
                    f"已取消恢复(当前对话未改动):{exc}"
                )
            backup_note = f"\n- 恢复前,当前对话已自动归档:{backup_path.name}"

        seeds = self._seed_messages()
        self._messages[:] = [*seeds, *conversation]
        self._seed_count = len(seeds)
        name = str(record.get("name") or ref)
        created = str(record.get("created_at") or "")
        return (
            f"✅ 已恢复会话:{name}\n"
            f"- 创建时间:{created}\n"
            f"- 恢复消息数:{len(conversation)}\n"
            f"- 当前上下文:{self.context_stats.messages} 条消息,"
            f"约 {self.context_stats.estimated_tokens} tokens"
            f"{backup_note}"
        )

    # ---------- 对话主流程 ----------

    async def chat(self, user_input: str) -> AsyncIterator[events.AgentEvent]:
        """处理一条用户消息,产出事件流。

        典型事件序列:
        1. TextChunk × N        —— 模型流式输出的文本增量
        2. ToolCallStarted/Finished —— 模型要求调用工具并执行(可多轮循环)
        3. ReplyDone            —— 本轮对话结束

        出错时产出 AgentError,并把聊天历史回滚到本轮开始前,
        保证用户重试时不会带上半截的对话。
        """
        user_input = user_input.strip()
        if not user_input:
            yield events.ReplyDone(text="")
            return

        snapshot = len(self._messages)  # 记录本轮开始的位置,出错时回滚用
        self._messages.append({"role": "user", "content": user_input})

        try:
            # 工具调用循环:模型每回答一次,若还想调用工具就执行工具并送回结果,
            # 直到它不再调用工具(或达到最大轮数)。
            for _round in range(self._max_tool_rounds):
                reply_parts: list[str] = []
                tool_calls: list[events.ToolCallProposal] = []

                # 1) 请求 LLM,收集本轮回复
                async for ev in self._llm.stream_chat(
                    self._messages,
                    tools=self._tools.openai_schemas(),
                ):
                    if isinstance(ev, events.TextChunk):
                        yield ev  # 文本增量直接转发给 UI
                        reply_parts.append(ev.text)
                    elif isinstance(ev, events.ToolCallProposal):
                        tool_calls.append(ev)

                reply_text = "".join(reply_parts)

                # 2) 模型没有要求调用工具 → 正常回答结束
                if not tool_calls:
                    if reply_text:
                        self._messages.append(
                            {"role": "assistant", "content": reply_text}
                        )
                    yield events.ReplyDone(text=reply_text)
                    return

                # 3) 模型要求调用工具:先把"带工具调用的回复"记入历史
                self._messages.append(
                    {
                        "role": "assistant",
                        "content": reply_text or None,
                        "tool_calls": [
                            {
                                "id": tc.call_id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": tc.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )

                # 4) 逐个执行工具,把结果作为 tool 消息送回 LLM(下一轮循环)
                for tc in tool_calls:
                    yield events.ToolCallStarted(
                        name=tc.name,
                        arguments=_safe_parse_arguments(tc.arguments),
                    )
                    result = await self._tools.execute(tc.name, tc.arguments)
                    yield events.ToolCallFinished(
                        name=tc.name,
                        ok=not result.startswith("错误"),
                        result=result,
                    )
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.call_id,
                            "content": result,
                        }
                    )

            # 5) 超过最大工具轮数还没结束:中断并提示
            yield events.AgentError(
                message=(
                    f"工具调用超过 {self._max_tool_rounds} 轮仍未结束,已强制停止。"
                    "请尝试把需求描述得更具体一些。"
                )
            )
        except asyncio.CancelledError:
            # 用户取消(如按 Esc):同样回滚本轮消息,避免留下半截对话
            del self._messages[snapshot:]
            raise
        except Exception as exc:
            # 出错回滚:丢弃本轮产生的所有消息,对话可以继续
            del self._messages[snapshot:]
            yield events.AgentError(message=f"调用大模型失败:{exc}")
