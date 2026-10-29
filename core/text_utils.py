"""core 层共享的文本小工具。

cli_ops(RunCli)与 web_ops(fetchWeb / searchWeb)都需要:
1. 把程序输出的字节流解码成文本 —— Windows 下不同程序编码不同
   (有些是 UTF-8,有些是系统 GBK),先按 UTF-8 试,失败再退回系统默认编码;
2. 截断过长的文本并明确标注"已截断" —— 防止一次工具调用把模型上下文撑爆。

集中在这里,避免两个模块各写一份。
"""
from __future__ import annotations

import locale


def decode_bytes(data: bytes) -> str:
    """把字节流解码成文本。

    优先按 UTF-8 解码;失败(说明不是 UTF-8 编码)则退回系统默认编码,
    并容忍个别无法解码的字节(errors="replace"),最大限度避免乱码。
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


def truncate_text(text: str, max_chars: int) -> str:
    """截断过长的文本,并明确标注截断信息。

    返回给模型的文本长度必须受控,否则一次工具调用
    就可能占用大量上下文,甚至让后续对话无法继续。
    """
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n...[输出过长,已截断:原文 {len(text)} 字符,仅显示前 {max_chars} 字符]"
    )
