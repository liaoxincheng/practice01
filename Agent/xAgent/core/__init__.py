"""xAgent core 包:Agent 核心业务逻辑(与 UI 完全无关,可独立复用)。

模块一览:
- config   配置加载(从 config.ini 读取)
- events   core 与 UI 之间的事件协议(唯一"对话语言")
- llm      LLM 客户端抽象与实现(流式调用,可替换成其他模型服务)
- tools    工具注册中心(工具能力的统一管理)
- file_ops 文件操作工具(read_file / write_file / list_dir)
- agent    Agent 编排核心(聊天历史 + 流式对话 + 工具调用循环)

依赖规则(分层架构的核心约束):
- core 不 import 任何 tui 代码,也不依赖任何 UI 库;
- UI 层(tui/ 或未来的 web/、desktop/)只允许单向依赖 core;
- 新增界面(Web、桌面端)时,core 一行代码都不用改。
"""
