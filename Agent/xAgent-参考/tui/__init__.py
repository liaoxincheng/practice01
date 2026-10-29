"""tui —— Textual 终端界面层。

这一层只做两件事：把用户输入交给 core.agent.Agent，
把 Agent 发出的事件画到屏幕上。业务逻辑一律不写在这里。

将来做 Web 版或 Desktop 版，照着这个目录再写一份即可，
core/ 完全不用动。
"""
