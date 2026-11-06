"""``createMcp`` 工具:把模型给出的 MCP Server 配置写入 ``.mcp.json``。

行为与 Study 仓库里的同名工具一致:

- 默认写入 xAgent 启动时读取的那份 ``.mcp.json``;
- 与文件中已有服务器合并,其他顶层字段原样保留;
- 写入前用与启动时相同的规则校验,校验失败不改动文件;
- 同名服务器默认拒绝覆盖,用户确认后显式传 ``overwrite=true``;
- 返回值只显示服务器名和端点,不回显 env / headers 的具体值。
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .mcp_config import CONFIG_FILENAME, McpConfigError, parse_server_entry
from .tools import Tool, ToolRegistry

_PRIMARY_KEY = "mcpServers"
_LEGACY_KEY = "mcp_servers"
_MAX_SERVERS = 50
_NAME_LIMIT = 64
_ENDPOINT_LIMIT = 160


class _ToolError(Exception):
    """createMcp 的校验错误(信息直接返回给模型)。"""


def _atomic_write(path: Path, data: bytes) -> None:
    """先写临时文件再替换,避免写到一半破坏原文件。"""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _coerce_servers(value: Any) -> dict[Any, Any]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise _ToolError("servers 不能为空")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _ToolError(f"servers 不是合法 JSON:{exc}") from exc
    if not isinstance(value, Mapping):
        raise _ToolError(
            "servers 必须是「服务器名 -> 配置对象」的 JSON 对象,"
            '例如 {"demo": {"type": "stdio", "command": "python"}}'
        )
    return dict(value)


def _validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise _ToolError("服务器名必须是字符串")
    clean = name.strip()
    if not clean:
        raise _ToolError("服务器名不能为空")
    if len(clean) > _NAME_LIMIT:
        raise _ToolError(f"服务器名过长(超过 {_NAME_LIMIT} 个字符):{clean}")
    if clean in {".", ".."} or "/" in clean or "\\" in clean:
        raise _ToolError(f"服务器名不能包含路径分隔符:{clean}")
    if any(ord(char) < 32 for char in clean):
        raise _ToolError("服务器名包含控制字符")
    return clean


def _read_payload(target: Path) -> tuple[dict[str, Any], str]:
    if not target.exists():
        return {}, _PRIMARY_KEY
    try:
        raw_text = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise _ToolError(f"读取现有 MCP 配置失败:{target}:{exc}") from exc

    try:
        payload = json.loads(raw_text) if raw_text.strip() else {}
    except json.JSONDecodeError as exc:
        raise _ToolError(
            f"{target} 不是合法 JSON,未改动任何内容。"
            f"请先手工修正该文件后重试:{exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise _ToolError(f"{target} 的顶层必须是 JSON 对象,未改动任何内容")
    payload = dict(payload)

    if _PRIMARY_KEY in payload:
        key = _PRIMARY_KEY
    elif _LEGACY_KEY in payload:
        key = _LEGACY_KEY
    else:
        key = _PRIMARY_KEY
    return payload, key


def _describe(name: str, server: Any) -> str:
    endpoint = " ".join(str(server.endpoint).split())
    if len(endpoint) > _ENDPOINT_LIMIT:
        endpoint = endpoint[:_ENDPOINT_LIMIT] + "……"
    return f"{name}({server.transport}:{endpoint})"


def _has_literal_secret(entries: Mapping[str, Any]) -> bool:
    for config in entries.values():
        for field in ("env", "headers"):
            values = config.get(field)
            if not isinstance(values, Mapping):
                continue
            if any(
                isinstance(value, str) and "${" not in value
                for value in values.values()
            ):
                return True
    return False


def register_mcp_config_ops(registry: ToolRegistry, config_path: Path) -> None:
    """注册 createMcp 工具,写入 ``config_path`` 指向的 .mcp.json。"""
    config_path = Path(config_path).expanduser().resolve()

    def create_mcp(servers: Any, overwrite: bool = False) -> str:
        try:
            data = _coerce_servers(servers)
            if not data:
                raise _ToolError(
                    "servers 至少需要一个服务器,"
                    '例如 {"demo": {"type": "stdio", "command": "python"}}'
                )
            if len(data) > _MAX_SERVERS:
                raise _ToolError(
                    f"一次最多写入 {_MAX_SERVERS} 个服务器,本次收到 {len(data)} 个"
                )

            entries: dict[str, Any] = {}
            validated: dict[str, Any] = {}
            seen: dict[str, str] = {}
            for raw_name, raw_config in data.items():
                name = _validate_name(raw_name)
                folded = name.casefold()
                if folded in seen:
                    raise _ToolError(
                        f"服务器名重复:{seen[folded]} 与 {name}(大小写不同也不行)"
                    )
                seen[folded] = name
                if not isinstance(raw_config, Mapping):
                    raise _ToolError(f"服务器 {name} 的配置必须是 JSON 对象")
                try:
                    validated[name] = parse_server_entry(
                        name,
                        raw_config,
                        base_dir=config_path.parent,
                        expand_env=False,
                    )
                except McpConfigError as exc:
                    raise _ToolError(f"服务器 {name} 配置不合法:{exc}") from exc
                entries[name] = dict(raw_config)

            payload, key = _read_payload(config_path)
            existing = payload.get(key) or {}
            if not isinstance(existing, Mapping):
                raise _ToolError(
                    f"{config_path} 中的 {key} 必须是 JSON 对象,"
                    "当前内容无法安全合并;未改动任何文件"
                )
            existing = dict(existing)

            conflicts = [name for name in entries if name in existing]
            if conflicts and not overwrite:
                raise _ToolError(
                    f"{config_path} 中已存在 MCP 服务器:{'、'.join(conflicts)},"
                    "未改动任何文件。"
                    "确认要覆盖这些服务器时,先向用户确认,再以 overwrite=true "
                    "重新调用;如只想新增,请换一个服务器名。"
                )

            merged = existing | entries
            payload[key] = merged
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            _atomic_write(config_path, text.encode("utf-8"))

            # 回读校验:确认落盘内容包含本次写入的全部服务器
            try:
                written = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise _ToolError(f"写入后回读 {config_path} 失败:{exc}") from exc
            written_servers = (
                written.get(key) if isinstance(written, Mapping) else None
            )
            if not isinstance(written_servers, Mapping) or set(entries) - set(
                written_servers
            ):
                raise _ToolError(
                    f"写入后校验 {config_path} 失败:服务器列表与预期不一致"
                )

            added = [name for name in entries if name not in existing]
            updated = [name for name in entries if name in existing]
            lines = [f"成功:已写入 MCP 配置 {config_path}"]
            if added:
                lines.append(
                    "新增服务器:"
                    + "、".join(_describe(name, validated[name]) for name in added)
                )
            if updated:
                lines.append(
                    "覆盖服务器:"
                    + "、".join(_describe(name, validated[name]) for name in updated)
                )
            kept = [name for name in existing if name not in entries]
            lines.append("文件中其他服务器:" + ("、".join(kept) if kept else "(无)"))
            lines.append("当前配置的全部服务器:" + "、".join(str(n) for n in merged))
            lines.append(
                "生效方式:新配置需要重启 xAgent 后才会连接服务器、"
                "注册对应工具;当前会话的工具列表不会立即刷新。"
            )
            if _has_literal_secret(entries):
                lines.append(
                    "安全提示:env / headers 中存在直接写入的值,建议改用 "
                    "${ENV_VAR} 从环境变量读取,避免密钥进入配置文件与版本库。"
                )
            lines.append(
                "说明:本结果不回显 env / headers 的具体值;配置中的 ${VAR} / "
                "${VAR:-默认值} 会在启动时按环境变量展开。"
            )
            return "\n".join(lines)
        except _ToolError as exc:
            return f"错误:{exc}"
        except Exception as exc:  # noqa: BLE001 —— 工具错误统一转成文本
            return f"错误:写入 MCP 配置失败:{exc}"

    registry.register(
        Tool(
            name="createMcp",
            description=(
                "把用户提供的 MCP Server 配置写入 .mcp.json(默认就是 xAgent "
                "启动时读取的那份文件,即启动目录下的 .mcp.json)。"
                "当用户要求接入、新增或修改 MCP Server / MCP 工具时调用。"
                "servers 是「服务器名 -> 配置对象」的 JSON 对象,字段与 .mcp.json "
                "的 mcpServers 一致:stdio 用 command / args / env / cwd,"
                "HTTP 用 url / headers,type 可省略(按 command / url 推断)。"
                "工具会先校验再合并写入;同名服务器默认拒绝覆盖,"
                "需要覆盖时先向用户确认再传 overwrite=true。"
                "密钥建议写成 ${ENV_VAR};工具结果不回显 env / headers 的值。"
                "新配置需要重启 xAgent 后才会连接生效。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "servers": {
                        "type": "object",
                        "description": (
                            "服务器名 -> 配置对象 的映射,例如 "
                            '{"demo": {"type": "stdio", "command": "python",'
                            ' "args": ["server.py"]}}'
                        ),
                        "additionalProperties": {"type": "object"},
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": (
                            "同名服务器已存在时是否覆盖,默认 false(拒绝写入并提示)"
                        ),
                        "default": False,
                    },
                },
                "required": ["servers"],
            },
            handler=create_mcp,
        )
    )


__all__ = ["CONFIG_FILENAME", "register_mcp_config_ops"]
