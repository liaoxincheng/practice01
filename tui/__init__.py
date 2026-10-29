"""xAgent tui 包:基于 Textual 的终端交互界面(TUI)。

本包只负责三件事(全部围绕"显示",不写业务逻辑):
1. 渲染界面:聊天记录、流式输出、状态栏、输入框;
2. 接收用户输入:把输入交给 core.Agent.chat();
3. 消费 core 的事件流:把 TextChunk / ToolCallStarted / ... 显示出来。

分层约束:tui 单向依赖 core;core 绝不 import tui。

扩展预留:以后新增 web/(Web 界面,如 FastAPI + WebSocket)、
desktop/(桌面界面,如 PySide/Tkinter)时,复用同一个 core 即可,
界面包之间互不影响。
"""
from .app import ChatApp

__all__ = ["ChatApp"]
