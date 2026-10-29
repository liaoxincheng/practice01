"""聊天记录区：把对话渲染成可滚动的富文本日志。"""
from rich.text import Text
from textual.widgets import RichLog


class ChatLog(RichLog):
    """一个只读的滚动日志，用来堆对话内容。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("highlight", True)
        kwargs.setdefault("markup", True)
        kwargs.setdefault("wrap", True)
        kwargs.setdefault("auto_scroll", True)
        super().__init__(**kwargs)
        self._streaming = False
        self._label_written = False
        self._buffer = ""

    def add_user(self, text):
        self.write(Text("你", style="bold cyan"))
        for line in text.splitlines() or [""]:
            self.write(Text("  " + line, style="white"))
        self.write("")

    def start_assistant(self):
        """标记新一轮开始。标签等真有内容时再写，避免出现空标签。"""
        self._streaming = True
        self._label_written = False

    def _ensure_label(self):
        if not self._label_written:
            self.write(Text("助手", style="bold green"))
            self._label_written = True

    def push_delta(self, text):
        """流式片段。

        RichLog 每次 write 就是新的一行，所以不能来一个片段写一行 ——
        那样一个字一行，根本没法看。这里按行聚合：攒到换行符才写出去。
        """
        if not self._streaming:
            self.start_assistant()
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._ensure_label()
            self.write(Text("  " + line, style="white"), shrink=False)

    def flush(self):
        """把缓冲里还没写完的那一行吐出去。"""
        if self._buffer:
            self._ensure_label()
            self.write(Text("  " + self._buffer, style="white"), shrink=False)
            self._buffer = ""

    def add_answer(self, text):
        """没有走流式（或者流式结束后）把整段回答补写出来。"""
        if not text:
            return
        self._ensure_label()
        for line in text.splitlines():
            self.write(Text("  " + line, style="white"))

    def end_assistant(self, text=None):
        """这一轮结束，收尾并留一个空行。"""
        self.flush()
        if self._label_written:
            self.write("")
        self._streaming = False
        self._label_written = False

    def add_error(self, message):
        self.write(Text("错误  " + message, style="bold red"))
        self.write("")

    def add_tool(self, name, arguments):
        args = self._format_args(arguments)
        self.write(Text("  ⚙ " + name + args, style="bold yellow"))

    def add_tool_result(self, name, result, error):
        if error:
            first = str(error).splitlines()[0][:200]
            self.write(Text("  ✗ " + first, style="red"))
        else:
            text = str(result) if result else ""
            first = text.splitlines()[0][:200] if text else ""
            lines = len(text.splitlines())
            suffix = "  …（共 %d 行）" % lines if lines > 1 else ""
            self.write(Text("  ✓ " + first + suffix, style="dim green"))

    @staticmethod
    def _format_args(arguments):
        if not arguments:
            return "()"
        pairs = []
        for k, v in arguments.items():
            s = str(v).replace("\n", "\\n")
            if len(s) > 40:
                s = s[:40] + "…"
            pairs.append("%s=%s" % (k, s))
        return "(" + ", ".join(pairs) + ")"
