"""输入框：回车发送，多行编辑用 Ctrl+J。"""
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea


class PromptInput(TextArea):
    """一个能感知回车的输入区。"""

    BINDINGS = [
        Binding("enter", "submit", "发送", show=True, priority=True),
        Binding("ctrl+j", "newline", "换行", show=True),
    ]

    class Submitted(Message):
        """用户按下回车，带着输入内容。"""

        def __init__(self, value):
            self.value = value
            super().__init__()

    def __init__(self, **kwargs):
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("tab_behavior", "focus")
        super().__init__(**kwargs)

    def action_submit(self):
        text = self.text.strip()
        if not text:
            return
        self.post_message(self.Submitted(text))

    def action_newline(self):
        self.insert("\n")

    def clear_text(self):
        self.text = ""
