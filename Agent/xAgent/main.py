"""xAgent 启动入口(组装层 / Composition Root)。

这里做"组装"而非"业务":把 core 里的组件(配置 → LLM → 工具 → Agent)
创建并连接起来,再交给 UI 层运行。

以后新增 Web / 桌面界面时,只需要修改 main() 的最后几行:
    from tui.app import ChatApp  →  from web.app import WebApp
core 一行都不用改。
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

# Windows 下统一按 UTF-8 读写终端,避免中文乱码
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from core.agent import Agent
from core.agents_md import load_agents_md
from core.cli_ops import register_cli_ops
from core.config import ensure_config_file, load_config
from core.context_ops import register_context_ops
from core.external_ops import register_external_ops
from core.file_ops import register_file_ops
from core.llm import OpenAILLMClient
from core.mcp_config import CONFIG_FILENAME, load_mcp_json
from core.mcp_config_ops import register_mcp_config_ops
from core.mcp_ops import register_mcp_ops
from core.prompts import build_system_prompt
from core.skill_ops import register_skill_ops
from core.skills_catalog import SkillsCatalog
from core.time_ops import register_time_ops
from core.tools import ToolRegistry
from core.web_ops import register_web_ops
from tui.app import ChatApp

# 项目根目录(本文件所在目录)
PROJECT_ROOT = Path(__file__).resolve().parent


def build_agent(
    config_path: Path, *, defer_mcp: bool = False
) -> tuple[Agent, list[str], Callable[[], list[str]] | None]:
    """组装 core 组件(依赖注入):

    配置 → LLM 客户端 → 工具注册中心(注册内置工具 + MCP 工具)→ Agent

    返回 (Agent, MCP 接入状态列表, 延迟加载函数):
    - 状态列表供界面启动时展示(哪台 MCP 服务器接入了几个工具、哪台连不上);
    - ``defer_mcp=True`` 时先不连接 MCP(避免启动卡顿),返回一个函数;
      界面在后台线程调用它完成连接,返回真实状态。
    """
    config = load_config(config_path)

    llm = OpenAILLMClient(config.llm)

    tools = ToolRegistry()
    register_file_ops(
        tools,
        work_dir=config.file_ops.work_dir,
        max_read_chars=config.file_ops.max_read_chars,
        max_list_entries=config.file_ops.max_list_entries,
    )
    register_time_ops(tools)

    # 技能自动发现:启动目录 → 工作区 → xAgent 自带目录 → 配置里的技能目录。
    # 这里只读 Frontmatter(名称 + 简介),正文留给 loadSkill 按需加载。
    catalog = SkillsCatalog.discover(
        [Path.cwd(), config.file_ops.work_dir, PROJECT_ROOT],
        skill_dirs=[config.skill_ops.skills_dir],
    )
    register_skill_ops(
        tools, skills_dir=config.skill_ops.skills_dir, catalog=catalog
    )
    register_external_ops(
        tools,
        max_file_bytes=config.external_ops.max_file_bytes,
        max_output_chars=config.external_ops.max_output_chars,
    )
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
    # recallContext:在压缩前的会话归档里检索旧细节
    register_context_ops(tools, config.context.archive_dir)

    # MCP:config.ini 的 [mcp] 段(HTTP)+ 启动目录下的 .mcp.json(stdio / HTTP)。
    # 同名服务器以 config.ini 为准,.mcp.json 中的重复项跳过并提示。
    mcp_config_path = Path.cwd() / CONFIG_FILENAME
    mcp_json_servers, mcp_issues = load_mcp_json(mcp_config_path)
    mcp_servers = list(config.mcp_ops.servers)
    seen_servers = {server.name.casefold() for server in mcp_servers}
    for server in mcp_json_servers:
        if server.name.casefold() in seen_servers:
            mcp_issues.append(
                f"MCP 服务器 {server.name}:与 config.ini 中的同名服务器重复,"
                "已跳过 .mcp.json 中的配置"
            )
            continue
        mcp_servers.append(server)
        seen_servers.add(server.name.casefold())

    def load_mcp_tools() -> list[str]:
        """连接 MCP 服务器并注册工具(可能耗时,放后台线程执行)。"""
        return register_mcp_ops(
            tools,
            servers=mcp_servers,
            timeout_seconds=config.mcp_ops.timeout_seconds,
        )

    mcp_status = list(mcp_issues)
    mcp_loader: Callable[[], list[str]] | None = None
    if defer_mcp and mcp_servers:
        # 后台加载:先显示"连接中",界面和内置工具立即可用
        mcp_status.append("MCP 服务器正在后台连接,工具就绪后会显示在右侧栏")
        mcp_loader = load_mcp_tools
    else:
        mcp_status += load_mcp_tools()
    register_mcp_config_ops(tools, mcp_config_path)

    # 系统提示词:工作区规则 + 技能清单 + MCP 接入状态,会话开始时注入一次
    system_prompt = build_system_prompt(
        workspace=config.file_ops.work_dir,
        skills=catalog.skills,
        mcp_status=mcp_status,
    )

    # AGENTS.md:读取"程序启动时的工作目录"(不是代码目录)下的项目说明,
    # 会话开始时注入聊天历史一次,之后全程复用
    agents_md = load_agents_md(Path.cwd())

    agent = Agent(
        llm=llm,
        tools=tools,
        max_tool_rounds=config.agent.max_tool_rounds,
        agents_md=agents_md,
        system_prompt=system_prompt,
        skill_names=[skill.name for skill in catalog.skills],
        skill_issues=list(catalog.issues),
        archive_dir=config.context.archive_dir,
        keep_recent_turns=config.context.keep_recent_turns,
        max_context_tokens=config.context.max_tokens,
        summary_max_chars=config.context.summary_max_chars,
    )
    return agent, mcp_status, mcp_loader


def main() -> None:
    """程序入口:组装 core,启动 Textual 界面。

    如果 config.ini 不存在,会先根据 config.example.ini 自动生成一份,
    并在界面启动后用弹窗提醒用户检查配置。
    """
    config_path = PROJECT_ROOT / "config.ini"
    generated = ensure_config_file(
        config_path, PROJECT_ROOT / "config.example.ini"
    )
    notice = ""
    if generated:
        notice = (
            f"没有找到配置文件:{config_path}\n\n"
            "已根据 config.example.ini 自动生成一份最小配置。\n"
            "默认连接 LM Studio(http://127.0.0.1:1234/v1),\n"
            "请按需修改 [llm] 的 base_url / model / api_key 后重启 xAgent。\n\n"
            "为避免首次启动卡顿,自动生成的配置没有预先接入 MCP 服务器。"
        )

    agent, mcp_status, mcp_loader = build_agent(config_path, defer_mcp=True)
    ChatApp(
        agent=agent,
        mcp_status=mcp_status,
        startup_notice=notice,
        mcp_loader=mcp_loader,
    ).run()


if __name__ == "__main__":
    main()
