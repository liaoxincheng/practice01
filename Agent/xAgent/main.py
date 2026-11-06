"""xAgent 启动入口(组装层 / Composition Root)。

这里做"组装"而非"业务":把 core 里的组件(配置 → LLM → 工具 → Agent)
创建并连接起来,再交给 UI 层运行。

以后新增 Web / 桌面界面时,只需要修改 main() 的最后几行:
    from tui.app import ChatApp  →  from web.app import WebApp
core 一行都不用改。
"""
from __future__ import annotations

import sys
from pathlib import Path

# Windows 下统一按 UTF-8 读写终端,避免中文乱码
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from core.agent import Agent
from core.agents_md import load_agents_md
from core.cli_ops import register_cli_ops
from core.config import load_config
from core.file_ops import register_file_ops
from core.llm import OpenAILLMClient
from core.mcp_ops import register_mcp_ops
from core.skill_ops import register_skill_ops
from core.time_ops import register_time_ops
from core.tools import ToolRegistry
from core.web_ops import register_web_ops
from tui.app import ChatApp

# 项目根目录(本文件所在目录)
PROJECT_ROOT = Path(__file__).resolve().parent


def build_agent(config_path: Path) -> tuple[Agent, list[str]]:
    """组装 core 组件(依赖注入):

    配置 → LLM 客户端 → 工具注册中心(注册内置工具 + MCP 工具)→ Agent

    返回 (Agent, MCP 接入状态列表):状态供界面启动时展示
    (哪台 MCP 服务器接入了几个工具、哪台连不上被跳过)。
    """
    config = load_config(config_path)

    llm = OpenAILLMClient(config.llm)

    tools = ToolRegistry()
    register_file_ops(tools, work_dir=config.file_ops.work_dir)
    register_time_ops(tools)
    register_skill_ops(tools, skills_dir=config.skill_ops.skills_dir)
    register_cli_ops(
        tools,
        timeout_seconds=config.cli_ops.timeout_seconds,
        max_output_chars=config.cli_ops.max_output_chars,
    )
    register_web_ops(
        tools,
        fetch_timeout_seconds=config.web_ops.fetch_timeout_seconds,
        max_content_chars=config.web_ops.max_content_chars,
        search_api_key=config.web_ops.search_api_key,
        search_endpoint=config.web_ops.search_endpoint,
        search_top_k=config.web_ops.search_top_k,
        search_timeout_seconds=config.web_ops.search_timeout_seconds,
    )
    mcp_status = register_mcp_ops(
        tools,
        servers=config.mcp_ops.servers,
        timeout_seconds=config.mcp_ops.timeout_seconds,
    )

    # AGENTS.md:读取"程序启动时的工作目录"(不是代码目录)下的项目说明,
    # 会话开始时注入聊天历史一次,之后全程复用
    agents_md = load_agents_md(Path.cwd())

    agent = Agent(
        llm=llm,
        tools=tools,
        max_tool_rounds=config.agent.max_tool_rounds,
        agents_md=agents_md,
    )
    return agent, mcp_status


def main() -> None:
    """程序入口:组装 core,启动 Textual 界面。"""
    agent, mcp_status = build_agent(PROJECT_ROOT / "config.ini")
    ChatApp(agent=agent, mcp_status=mcp_status).run()


if __name__ == "__main__":
    main()
