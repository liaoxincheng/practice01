"""``.mcp.json`` 的读取、校验与环境变量展开。

启动时读取当前目录下的 ``.mcp.json``(与 config.ini 的 [mcp] 段并存)。
格式与 MCP 生态常见写法一致:

```json
{
  "mcpServers": {
    "local-demo": {
      "type": "stdio",
      "command": "python",
      "args": ["server.py"],
      "env": {"DEMO_TOKEN": "${DEMO_TOKEN}"},
      "cwd": "."
    },
    "remote-demo": {
      "type": "streamable_http",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer ${DEMO_API_KEY}"}
    }
  }
}
```

- 支持 stdio 与 HTTP(streamable_http)两种传输;
- ``type`` 省略时按 ``command`` / ``url`` 自动推断;
- 字符串支持 ``${VAR}`` 与 ``${VAR:-默认值}`` 环境变量展开,
  密钥可以放在环境变量里,不必写进配置文件;
- 单个服务器配置错误只记录到 issues,不影响其他服务器。
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import McpServerConfig

CONFIG_FILENAME = ".mcp.json"

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

_HTTP_TYPES = {"streamable_http", "streamablehttp", "streamable", "http"}
_UNSUPPORTED_TYPES = {"sse", "websocket", "ws"}


class McpConfigError(ValueError):
    """MCP 配置不合法(信息可以直接展示给用户)。"""


def load_mcp_json(path: str | Path) -> tuple[list[McpServerConfig], list[str]]:
    """读取 .mcp.json,返回 (服务器列表, 问题列表)。

    文件不存在时返回空列表(不视为错误);顶层 JSON 不合法时把原因放进
    问题列表,启动流程继续,不影响内置工具。
    """
    target = Path(path).expanduser()
    if not target.is_file():
        return [], []

    try:
        text = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return [], [f"MCP 配置文件 {target} 读取失败:{exc}"]

    try:
        payload = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        return [], [f"MCP 配置文件 {target} 不是合法 JSON:{exc}"]

    if not isinstance(payload, Mapping):
        return [], [f"MCP 配置文件 {target} 的顶层必须是 JSON 对象"]

    raw_servers = payload.get("mcpServers", payload.get("mcp_servers"))
    if raw_servers is None:
        return [], [f"MCP 配置文件 {target} 缺少 mcpServers 对象"]
    if not isinstance(raw_servers, Mapping):
        return [], [f"MCP 配置文件 {target} 中的 mcpServers 必须是 JSON 对象"]

    base_dir = target.parent
    servers: list[McpServerConfig] = []
    issues: list[str] = []
    for raw_name, raw_config in raw_servers.items():
        label = raw_name if isinstance(raw_name, str) and raw_name.strip() else "<未命名>"
        try:
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise McpConfigError("服务器名称必须是非空字符串")
            if not isinstance(raw_config, Mapping):
                raise McpConfigError("服务器配置必须是 JSON 对象")
            servers.append(
                parse_server_entry(
                    raw_name.strip(),
                    raw_config,
                    base_dir=base_dir,
                    expand_env=True,
                )
            )
        except McpConfigError as exc:
            issues.append(f"MCP 服务器 {label}:{exc}")

    return servers, issues


def parse_server_entry(
    name: str,
    raw: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
    expand_env: bool = True,
) -> McpServerConfig:
    """校验单个服务器配置。

    :param name: 服务器名
    :param raw: .mcp.json 中的服务器配置对象
    :param base_dir: 相对 cwd 的基准目录(通常是 .mcp.json 所在目录)
    :param expand_env: 是否展开 ${VAR};createMcp 写文件前传 False,
        这样不要求环境变量当前已经存在。
    """
    if not isinstance(name, str) or not name.strip():
        raise McpConfigError("服务器名称必须是非空字符串")
    if not isinstance(raw, Mapping):
        raise McpConfigError("服务器配置必须是 JSON 对象")

    directory = (
        Path(base_dir).expanduser().resolve() if base_dir is not None else Path.cwd()
    )
    transport = _normalize_transport(raw)

    if transport == "stdio":
        command = _string_field(raw, "command", f"{name}.command", expand=expand_env)
        if not command:
            raise McpConfigError("stdio 服务器必须提供 command")
        return McpServerConfig(
            name=name.strip(),
            transport="stdio",
            command=command,
            args=_string_list(raw.get("args"), f"{name}.args", expand=expand_env),
            env=_string_map(raw.get("env"), f"{name}.env", expand=expand_env),
            cwd=_resolve_cwd(
                raw.get("cwd"),
                base_dir=directory,
                label=f"{name}.cwd",
                expand=expand_env,
            ),
        )

    url = _string_field(raw, "url", f"{name}.url", expand=expand_env)
    if not url:
        raise McpConfigError("HTTP 服务器必须提供 url")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise McpConfigError(f"url 必须是合法的 http/https 地址:{url}")
    return McpServerConfig(
        name=name.strip(),
        transport="http",
        url=url,
        headers=_string_map(
            raw.get("headers"), f"{name}.headers", expand=expand_env
        ),
    )


def _normalize_transport(raw: Mapping[str, Any]) -> str:
    value = raw.get("type", raw.get("transport", ""))
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise McpConfigError("type 必须是字符串(stdio 或 http)")
    key = value.strip().lower().replace("-", "_")

    if not key:
        if raw.get("command"):
            return "stdio"
        if raw.get("url"):
            return "http"
        raise McpConfigError(
            "无法推断传输类型:请提供 type,或提供 command(stdio)/ url(http)"
        )
    if key == "stdio":
        return "stdio"
    if key in _HTTP_TYPES:
        return "http"
    if key in _UNSUPPORTED_TYPES:
        raise McpConfigError(
            f"暂不支持 MCP 传输类型 {value!r};当前仅支持 stdio 和 http"
        )
    raise McpConfigError(
        f"未知的 MCP 传输类型 {value!r};当前仅支持 stdio 和 http"
    )


def _expand_env(value: str, *, label: str) -> str:
    if "${" not in value:
        return value

    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        default = match.group(2)
        current = os.environ.get(variable)
        if current is not None:
            return current
        if default is not None:
            return default
        raise McpConfigError(
            f"{label} 引用了未设置的环境变量 ${{{variable}}}"
            f"(可写成 ${{{variable}:-默认值}})"
        )

    return _ENV_REFERENCE.sub(replace, value)


def _string_field(
    raw: Mapping[str, Any],
    key: str,
    label: str,
    *,
    expand: bool,
) -> str:
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise McpConfigError(f"{label} 必须是字符串")
    text = value.strip()
    return _expand_env(text, label=label) if expand else text


def _string_list(value: Any, label: str, *, expand: bool) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise McpConfigError(f"{label} 必须是字符串数组")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise McpConfigError(f"{label}[{index}] 必须是字符串")
        items.append(
            _expand_env(item, label=f"{label}[{index}]") if expand else item
        )
    return tuple(items)


def _string_map(value: Any, label: str, *, expand: bool) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise McpConfigError(f"{label} 必须是 JSON 对象")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise McpConfigError(f"{label} 的键必须是非空字符串")
        if not isinstance(item, str):
            raise McpConfigError(f"{label}.{key} 必须是字符串")
        result[key] = _expand_env(item, label=f"{label}.{key}") if expand else item
    return result


def _resolve_cwd(
    value: Any,
    *,
    base_dir: Path,
    label: str,
    expand: bool,
) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise McpConfigError(f"{label} 必须是字符串")
    text = value.strip()
    expanded = _expand_env(text, label=label) if expand else text
    if not expanded:
        return ""
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())
