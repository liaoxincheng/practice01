"""界面契约。

core 层不认识任何界面框架，界面层也不碰业务逻辑。
两边只靠 Agent 上这几个回调对接，谁实现都行：

    agent.on_assistant_start()                 一轮回答开始，界面可以清空缓冲
    agent.on_assistant_delta(text)             流式收到一段正文，界面即时追加
    agent.on_assistant_end(text, tool_calls)   正文结束，带本次要执行的工具调用
    agent.on_tool_start(name, arguments)       准备执行工具
    agent.on_tool_end(name, result, error)     工具执行完，error 非 None 表示失败
    agent.on_error(message)                    出错，界面应提示并恢复输入

写新界面时只要：
    1. 建一个 Agent（注入 Config）
    2. 把这 6 个回调挂到自己的显示逻辑上
    3. 用户输入时调用 agent.ask(text)

业务逻辑、工具实现、流式解析全部复用，一行都不用重写。
"""
