"""把 Agent 的事件回调翻译成 Textual 的消息。

core 层不知道 Textual 的存在，界面层也不知道 socket 和流式解析，
两边只通过这里的消息类型对接。
"""
from textual.message import Message


class AssistantDelta(Message):
    """收到一段流式正文。"""

    def __init__(self, text):
        self.text = text
        super().__init__()


class AssistantStart(Message):
    """一轮回答开始。"""


class AssistantEnd(Message):
    """正文结束，带上本次要执行的工具调用。"""

    def __init__(self, text, calls):
        self.text = text
        self.calls = calls
        super().__init__()


class ToolStarted(Message):
    """准备执行某个工具。"""

    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments
        super().__init__()


class ToolFinished(Message):
    """某个工具执行完。"""

    def __init__(self, name, result, error):
        self.name = name
        self.result = result
        self.error = error
        super().__init__()


class AgentFailed(Message):
    """出错了。"""

    def __init__(self, message):
        self.message = message
        super().__init__()
