"""工具层：注册表 + 全部具体工具。"""
from .registry import Registry, Tool

__all__ = ["Registry", "Tool", "default_registry"]


def default_registry():
    """装上全部工具，返回一个注册表。"""
    from . import cli_tools, datetime_tools, file_tools, search_tools, web_tools

    reg = Registry()
    for group in (datetime_tools, file_tools, web_tools, search_tools, cli_tools):
        for tool in group.build_tools():
            reg.register(tool)
    return reg
