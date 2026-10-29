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
from dataclasses import dataclass
from pathlib import Path


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
class FileOpsConfig:
    """文件操作工具的配置。"""

    # 工作目录:模型只能读写这个目录内的文件(安全限制)
    work_dir: Path


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
class AppConfig:
    """整个应用的配置(由 load_config 组装)。"""

    llm: LLMConfig
    agent: AgentConfig
    file_ops: FileOpsConfig
    cli_ops: CliOpsConfig
    web_ops: WebOpsConfig


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

    # ---- [file_ops] 段:文件工具工作目录 ----
    work_dir_text = parser.get("file_ops", "work_dir", fallback="workspace")
    work_dir = Path(work_dir_text)
    if not work_dir.is_absolute():
        # 相对路径以配置文件所在目录为基准
        work_dir = (path.parent / work_dir).resolve()
    file_ops = FileOpsConfig(work_dir=work_dir)

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

    return AppConfig(
        llm=llm, agent=agent, file_ops=file_ops, cli_ops=cli_ops, web_ops=web_ops,
    )
