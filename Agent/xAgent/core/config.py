"""配置加载模块。

从 config.ini 读取配置并解析成结构化对象(AppConfig)。
core 层只依赖标准库,不依赖任何第三方 UI 库。

设计说明:
- 相对路径(如 file_ops 的 work_dir)一律以"配置文件所在目录"为基准解析,
  保证无论从哪个目录启动程序,行为都一致。
- 缺失的配置项使用默认值,避免配置文件少写一项就崩溃。
"""
from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path

# 找不到 config.example.ini 时使用的最小可用配置(不会预先接入 MCP)
_MINIMAL_CONFIG = """[llm]
base_url = http://127.0.0.1:1234/v1
model = qwen3.5-4b
api_key = DUMMY

[agent]
max_tool_rounds = 5

[context]
archive_dir = .xagent/sessions
keep_recent_turns = 3
max_tokens = 12000
summary_max_chars = 4000

[file_ops]
work_dir = workspace
max_read_chars = 20000
max_list_entries = 200

[external_ops]
max_file_bytes = 200000
max_output_chars = 50000

[skill_ops]
skills_dir = .agents/skills

[cli_ops]
timeout_seconds = 30
max_output_chars = 4000

[web_ops]
fetch_timeout_seconds = 20
max_content_chars = 6000

[search]
baidu_qianfan_api_key =
top_k = 10
timeout_seconds = 20

[mcp]
servers =
timeout_seconds = 30
"""


def ensure_config_file(
    path: str | Path, example_path: str | Path | None = None
) -> bool:
    """确保配置文件存在;不存在时自动生成一份,返回是否生成了新文件。

    优先复制 ``config.example.ini``(保留完整注释),但会把默认的 MCP
    servers 清空,避免第一次启动因为连不上示例服务器而卡顿。
    示例文件不可用时写入内置的最小配置。
    """
    path = Path(path)
    if path.exists():
        return False

    text = _MINIMAL_CONFIG
    if example_path is not None:
        example = Path(example_path)
        try:
            if example.is_file():
                text = example.read_text(encoding="utf-8")
                # 只清空 [mcp] 段的 servers 值,注释和其他段保持不变
                text = re.sub(
                    r"(?m)^([ \t]*servers[ \t]*=).*$",
                    r"\1",
                    text,
                    count=1,
                )
        except (OSError, UnicodeError):
            text = _MINIMAL_CONFIG

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


@dataclass(frozen=True)
class LLMConfig:
    """大模型连接配置(OpenAI 兼容接口)。"""

    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class AgentConfig:
    """Agent 行为配置。"""

    # 一轮对话中工具调用的最大来回数,防止模型一直调工具停不下来
    max_tool_rounds: int = 5


@dataclass(frozen=True)
class ContextConfig:
    """上下文压缩与会话归档的配置。"""

    # 会话归档目录:压缩前自动归档、/save 保存、/resume 恢复、recallContext 检索
    archive_dir: Path
    # 压缩时保留最近多少轮对话不摘要(一轮 = 一条用户消息及其后续回复)
    keep_recent_turns: int = 3
    # 上下文估算上限(仅用于状态栏提示,不会自动触发压缩)
    max_tokens: int = 12000
    # 压缩时模型摘要的最大保留字符数(超出截断)
    summary_max_chars: int = 4000


@dataclass(frozen=True)
class FileOpsConfig:
    """文件操作工具的配置。"""

    # 工作目录:模型只能读写这个目录内的文件(安全限制)
    work_dir: Path
    # read_file 单次返回的最大字符数,超出截断
    max_read_chars: int = 20_000
    # list_dir 最多列出的条目数
    max_list_entries: int = 200


@dataclass(frozen=True)
class ExternalOpsConfig:
    """工作区外只读工具(readExternalFile)的配置。"""

    # 单个文件最多读取的字节数,超过直接拒绝
    max_file_bytes: int = 200_000
    # 返回给模型的最大字符数(工具调用时可用 max_chars 再调小)
    max_output_chars: int = 50_000


@dataclass(frozen=True)
class CliOpsConfig:
    """命令行工具(RunCli)的配置。"""

    # 单次命令执行的最长秒数,超时强制终止
    timeout_seconds: float = 30.0
    # stdout / stderr 各自的最大保留字符数,超出部分截断
    max_output_chars: int = 4000


@dataclass(frozen=True)
class WebOpsConfig:
    """联网工具(fetchWeb / searchWeb)的配置。"""

    # fetchWeb:下载单个网页的超时(秒)
    fetch_timeout_seconds: float = 20.0
    # fetchWeb / searchWeb:返回内容的最大保留字符数,超出部分截断
    max_content_chars: int = 6000
    # searchWeb:百度千帆 API Key(留空时 searchWeb 返回"未配置"提示,不崩溃)
    search_api_key: str = ""
    # searchWeb:千帆搜索接口地址(测试时可改为本地假服务,不联网)
    search_endpoint: str = "https://qianfan.baidubce.com/v2/ai_search/web_search"
    # searchWeb:每次搜索返回的参考来源条数上限
    search_top_k: int = 10
    # searchWeb:搜索 API 请求的超时(秒)
    search_timeout_seconds: float = 20.0


@dataclass(frozen=True)
class SkillOpsConfig:
    """技能创建工具(createSkill)的配置。"""

    # 技能目录:createSkill 在这里创建 <名称>/SKILL.md(项目的能力扩展区)
    skills_dir: Path


@dataclass(frozen=True)
class McpServerConfig:
    """一个 MCP 服务器的连接配置。

    支持两种传输方式:
    - http(config.ini 的 [mcp] 段或 .mcp.json 的 url):使用 url / headers;
    - stdio(.mcp.json 的 command):使用 command / args / env / cwd。

    env 和 headers 里可能包含密钥,所以 __repr__ 只显示端点,不显示具体值。
    """

    name: str  # 服务器名(用于工具名前缀和状态展示),如 "pet"
    url: str = ""  # MCP 服务地址,如 "http://127.0.0.1:8081/mcp"
    transport: str = "http"  # "http" 或 "stdio"
    command: str = ""  # stdio:要启动的可执行程序
    args: tuple[str, ...] = ()  # stdio:程序参数
    env: dict[str, str] = field(default_factory=dict)  # stdio:额外的环境变量
    cwd: str = ""  # stdio:子进程工作目录
    headers: dict[str, str] = field(default_factory=dict)  # http:请求头

    @property
    def endpoint(self) -> str:
        """用于状态展示的简短连接描述(不含密钥)。"""
        if self.transport == "stdio":
            parts = [self.command, *self.args]
            return " ".join(part for part in parts if part)
        return self.url

    def __repr__(self) -> str:
        return (
            f"McpServerConfig(name={self.name!r}, transport={self.transport!r}, "
            f"endpoint={self.endpoint!r})"
        )


@dataclass(frozen=True)
class McpOpsConfig:
    """MCP 工具接入([mcp] 段)的配置。"""

    # 要接入的服务器列表(留空 = 不接入 MCP)
    servers: tuple[McpServerConfig, ...] = ()
    # 连接 / 调用单个 MCP 工具的超时(秒)
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class AppConfig:
    """整个应用的配置(由 load_config 组装)。"""

    llm: LLMConfig
    agent: AgentConfig
    context: ContextConfig
    file_ops: FileOpsConfig
    external_ops: ExternalOpsConfig
    skill_ops: SkillOpsConfig
    cli_ops: CliOpsConfig
    web_ops: WebOpsConfig
    mcp_ops: McpOpsConfig


def load_config(path: str | Path = "config.ini") -> AppConfig:
    """读取配置文件,返回 AppConfig。

    :param path: config.ini 的路径
    :raises FileNotFoundError: 配置文件不存在或缺少 [llm] 段
    """
    path = Path(path)
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")

    if not parser.has_section("llm"):
        raise FileNotFoundError(
            f"找不到配置文件 {path}(至少需要 [llm] 段,请参照项目根目录的 config.ini 填写)"
        )

    # ---- [llm] 段:模型连接信息 ----
    llm = LLMConfig(
        base_url=parser.get("llm", "base_url", fallback="http://127.0.0.1:1234/v1"),
        api_key=parser.get("llm", "api_key", fallback="DUMMY"),
        model=parser.get("llm", "model", fallback="qwen3.5-4b"),
    )

    # ---- [agent] 段:Agent 行为 ----
    agent = AgentConfig(
        max_tool_rounds=parser.getint("agent", "max_tool_rounds", fallback=5),
    )

    # ---- [context] 段:上下文压缩与会话归档 ----
    archive_dir_text = parser.get(
        "context", "archive_dir", fallback=".xagent/sessions"
    )
    archive_dir = Path(archive_dir_text)
    if not archive_dir.is_absolute():
        archive_dir = (path.parent / archive_dir).resolve()
    keep_recent_turns = parser.getint(
        "context", "keep_recent_turns", fallback=3
    )
    max_context_tokens = parser.getint(
        "context", "max_tokens", fallback=12000
    )
    summary_max_chars = parser.getint(
        "context", "summary_max_chars", fallback=4000
    )
    context = ContextConfig(
        archive_dir=archive_dir,
        keep_recent_turns=max(1, min(keep_recent_turns, 20)),
        max_tokens=max(1000, max_context_tokens),
        summary_max_chars=max(500, min(summary_max_chars, 20_000)),
    )

    # ---- [file_ops] 段:文件工具工作目录与读取上限 ----
    work_dir_text = parser.get("file_ops", "work_dir", fallback="workspace")
    work_dir = Path(work_dir_text)
    if not work_dir.is_absolute():
        # 相对路径以配置文件所在目录为基准
        work_dir = (path.parent / work_dir).resolve()
    file_ops = FileOpsConfig(
        work_dir=work_dir,
        max_read_chars=max(
            100, parser.getint("file_ops", "max_read_chars", fallback=20_000)
        ),
        max_list_entries=max(
            1, parser.getint("file_ops", "max_list_entries", fallback=200)
        ),
    )

    # ---- [external_ops] 段:工作区外只读工具的读取上限 ----
    external_ops = ExternalOpsConfig(
        max_file_bytes=max(
            1000,
            parser.getint("external_ops", "max_file_bytes", fallback=200_000),
        ),
        max_output_chars=max(
            100,
            parser.getint("external_ops", "max_output_chars", fallback=50_000),
        ),
    )

    # ---- [skill_ops] 段:技能创建工具(createSkill) ----
    skills_dir_text = parser.get("skill_ops", "skills_dir", fallback=".agents/skills")
    skills_dir = Path(skills_dir_text)
    if not skills_dir.is_absolute():
        # 与 work_dir 一样,相对路径以配置文件所在目录为基准
        skills_dir = (path.parent / skills_dir).resolve()
    skill_ops = SkillOpsConfig(skills_dir=skills_dir)

    # ---- [cli_ops] 段:命令行工具(RunCli) ----
    cli_ops = CliOpsConfig(
        timeout_seconds=parser.getfloat("cli_ops", "timeout_seconds", fallback=30.0),
        max_output_chars=parser.getint("cli_ops", "max_output_chars", fallback=4000),
    )

    # ---- [web_ops] 段:联网工具 fetchWeb ----
    # ---- [search] 段:联网工具 searchWeb(百度千帆) ----
    web_ops = WebOpsConfig(
        fetch_timeout_seconds=parser.getfloat("web_ops", "fetch_timeout_seconds", fallback=20.0),
        max_content_chars=parser.getint("web_ops", "max_content_chars", fallback=6000),
        search_api_key=parser.get("search", "baidu_qianfan_api_key", fallback="").strip(),
        search_top_k=parser.getint("search", "top_k", fallback=10),
        search_timeout_seconds=parser.getfloat("search", "timeout_seconds", fallback=20.0),
    )

    # ---- [mcp] 段:MCP 服务器接入 ----
    # servers 格式:名称=地址,名称=地址(逗号分隔);单条格式不对就跳过这条
    mcp_servers: list[McpServerConfig] = []
    for item in parser.get("mcp", "servers", fallback="").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, url = item.split("=", 1)
        name, url = name.strip(), url.strip()
        if name and url:
            mcp_servers.append(McpServerConfig(name=name, url=url))
    mcp_ops = McpOpsConfig(
        servers=tuple(mcp_servers),
        timeout_seconds=parser.getfloat("mcp", "timeout_seconds", fallback=30.0),
    )

    return AppConfig(
        llm=llm,
        agent=agent,
        context=context,
        file_ops=file_ops,
        external_ops=external_ops,
        skill_ops=skill_ops,
        cli_ops=cli_ops,
        web_ops=web_ops,
        mcp_ops=mcp_ops,
    )
