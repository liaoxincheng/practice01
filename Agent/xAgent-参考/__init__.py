"""xAgent —— 带 function call 能力的本地 Agent。

分层约定：
    core/   业务逻辑，不导入任何界面库，可被 TUI / Web / Desktop 复用
    tui/    Textual 终端界面，只负责显示和输入
"""

__version__ = "0.1.0"
