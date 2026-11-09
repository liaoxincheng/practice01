"""系统提示词:把工作区规则、技能清单和 MCP 状态组合成一段说明。

这段文字在会话开始时作为第一条 system 消息注入聊天历史,之后每轮复用,
不会重复追加。它属于 core 业务,不依赖任何 UI。
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .skills_catalog import SkillInfo


def build_system_prompt(
    workspace: Path,
    skills: Iterable[SkillInfo] | None = None,
    mcp_status: Iterable[str] | None = None,
) -> str:
    """生成系统提示词。

    :param workspace: 文件工具的工作区(模型只能在这里读写普通文件)
    :param skills: 启动时发现的技能(只含名称和简介)
    :param mcp_status: MCP 服务器的接入状态(供模型了解哪些外部工具可用)
    """
    skill_section = _build_skill_section(tuple(skills or ()))
    mcp_section = _build_mcp_section(tuple(mcp_status or ()))

    return f"""你是一个本地文件管理助手,当前工作区是:{workspace}

请遵守以下规则:
1. 需要查看或操作文件时,必须调用工具,不要凭空编造目录或文件内容。
2. 普通文件工具只能访问当前工作区,相对路径都相对于工作区。
3. 读取和编辑只支持纯文本文件;文件类型不确定时,先调用 get_file_type。
4. 删除目录会递归删除其中所有内容;删除、覆盖、重命名前要确认目标路径正确,
   只有用户明确要求时才执行破坏性操作。
5. 工具返回失败时,把失败原因如实告诉用户,不要假装操作成功。
6. 涉及“今天”“现在”“今年”“星期几”“闰年”等日期时间信息时,
   必须先调用 get_current_datetime(或 get_date),不要凭记忆回答。
7. 需要阅读、总结或分析网页时,先调用 fetchWeb 获取正文,再基于返回内容回答。
8. 用户询问新闻、天气、价格、赛事等时效性信息时,先调用 searchWeb 搜索;
   需要更详细内容时再用 fetchWeb 读取来源网页,并注明参考来源。
9. 需要执行本地命令行程序时调用 RunCli:command 只填程序名或完整路径,
   args 按顺序逐项填写,不要把程序名和参数拼成一条 shell 命令。
10. 只有用户明确要求读取工作区之外的文件时,才调用 readExternalFile;
    不要主动读取系统文件、密钥、凭据、浏览器数据或与任务无关的隐私文件。
11. 当用户要求接入、新增或修改 MCP Server 时,调用 createMcp 把配置写入
    启动目录下的 .mcp.json;密钥建议写成 ${{ENV_VAR}}。createMcp 不会回显
    env / headers 的具体值,写入后要告诉用户重启 xAgent 才生效。
12. 如果上下文里出现“[历史对话摘要]”,说明更早的对话已被压缩归档;
    需要旧细节(用户以前的要求、文件路径、命令或结论)时,先调用 recallContext
    按关键字检索归档,不要只根据摘要猜测。
13. 用中文回答,简洁说明执行结果;可以汇总多个工具的结果。
{skill_section}{mcp_section}"""


def _build_skill_section(skills: tuple[SkillInfo, ...]) -> str:
    if not skills:
        return ""

    lines = ["", "你可以使用以下 Agent Skills(这里只有名称和简介):"]
    for skill in skills:
        description = " ".join(skill.description.split())
        lines.append(f"- {skill.name}:{description}")
    lines.extend(
        (
            "当用户的任务与某个 Skill 的简介匹配时,先调用 loadSkill 读取它的"
            "完整说明(SKILL.md),再按说明执行;需要附件时给 loadSkill 传 "
            "filename。不要只根据简介猜测操作步骤。",
            "当用户要求创建新 Skill 时调用 createSkill;新创建的 Skill 会在"
            "下次启动 xAgent 后出现在可用技能列表中。",
        )
    )
    return "\n".join(lines) + "\n"


def _build_mcp_section(statuses: tuple[str, ...]) -> str:
    if not statuses:
        return ""

    lines = ["", "外部 MCP Server 接入情况:"]
    lines.extend(f"- {status}" for status in statuses)
    lines.extend(
        (
            "MCP 工具名以 mcp_ 开头,和本地工具一样通过 function calling 调用。"
            "某个 MCP Server 连接失败时,它的工具不可用,但本地工具仍可正常使用;"
            "MCP 工具返回错误时,把服务器给出的错误信息如实告诉用户,不要编造结果。",
        )
    )
    return "\n".join(lines) + "\n"
