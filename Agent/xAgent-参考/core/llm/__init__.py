"""LLM 接入层。"""
from .client import Client, Delta, LLMError, ToolCallBuffer

__all__ = ["Client", "Delta", "LLMError", "ToolCallBuffer"]
