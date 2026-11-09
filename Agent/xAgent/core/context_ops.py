"""``recallContext`` 工具:在压缩前的会话归档里检索旧细节。

上下文被 ``/compact`` 压缩后,完整历史仍然保存在归档目录里。
模型可以用这个工具按关键字找回早先的对话内容,例如:
"我们之前决定的配置文件路径是什么?"。
"""
from __future__ import annotations

from pathlib import Path

from .context import ArchiveStore
from .tools import Tool, ToolRegistry


def register_context_ops(registry: ToolRegistry, archive_dir: str | Path) -> Path:
    """注册 recallContext,返回归档目录路径。"""
    store = ArchiveStore(archive_dir)

    def recall_context(query: str, limit: int = 5) -> str:
        try:
            if not (query or "").strip():
                return "错误:query 不能为空,请输入要检索的关键字"
            try:
                count = int(limit)
            except (TypeError, ValueError):
                count = 5
            hits = store.search(query, count)
            if not hits:
                return (
                    f"没有在归档会话中找到与 {query!r} 相关的内容。"
                    f"归档目录:{store.directory}"
                )

            lines = [
                f"在归档会话中找到 {len(hits)} 条与 {query!r} 相关的记录"
                f"(归档目录:{store.directory}):"
            ]
            for index, hit in enumerate(hits, start=1):
                lines.append(
                    f"[{index}] {hit.created_at} | {hit.session} | {hit.role}\n"
                    f"    {hit.snippet}"
                )
            lines.append(
                "说明:归档保存的是上下文压缩前的完整对话,"
                "可以作为旧细节的依据;回答时请区分归档内容和当前对话。"
            )
            return "\n".join(lines)
        except ValueError as exc:
            return f"错误:{exc}"
        except Exception as exc:  # noqa: BLE001 —— 工具错误统一转文本
            return f"错误:检索会话归档失败:{exc}"

    registry.register(
        Tool(
            name="recallContext",
            description=(
                "在会话归档中按关键字检索上下文压缩前的旧对话。"
                "当用户提到‘之前说过/我们上次决定/压缩前的细节’,"
                "而当前上下文里找不到时,调用本工具。"
                "query 填要查找的关键字(人名、文件名、路径、结论等),"
                "limit 是返回条数(1-20,默认 5)。"
                "归档只读,不会修改当前对话。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的关键字或短语",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回多少条记录(1-20,默认 5)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["query"],
            },
            handler=recall_context,
        )
    )
    return store.directory


__all__ = ["register_context_ops"]
