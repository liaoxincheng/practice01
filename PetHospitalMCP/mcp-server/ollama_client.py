"""本机 Ollama 客户端:把一只宠物的全部病历交给模型写随访小结。

服务地址从环境变量读取:
    OLLAMA_URL    默认 http://127.0.0.1:11435
    OLLAMA_MODEL  默认 qwen3-vl:4b-instruct

只做一次非流式 /api/chat 调用;失败统一抛 OllamaError。
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11435"
DEFAULT_MODEL = "qwen3-vl:4b-instruct"
DEFAULT_TIMEOUT = 180.0

# 约束模型:结构化三段、只依据病历、复查时间照抄原文
SYSTEM_PROMPT = (
    "你是宠物医院的临床随访助理。请只根据用户提供的病历资料,用中文回答,"
    "固定输出三部分:"
    "一、病情小结;"
    "二、复查与随访建议(病历中写了 followUp 的,按原文照抄,不要换算或推算日期);"
    "三、主人居家护理注意事项。"
    "每部分不超过三句话。病历中没有的信息一律不要编造;资料不足时直接说明。"
)


class OllamaError(RuntimeError):
    """调用本机 Ollama 失败。"""


class OllamaSummarizer:
    """调用本机 Ollama 生成随访小结。"""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("OLLAMA_URL")
            or DEFAULT_OLLAMA_URL
        ).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                trust_env=False,
                transport=self._transport,
            )
        return self._http

    async def summarize(self, pet: dict[str, Any]) -> str:
        """传入完整宠物档案,返回模型写出的三段式随访小结。"""
        prompt = render_case(pet)
        try:
            response = await self._client().post(
                "/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "options": {"temperature": 0.2},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"连接本机 Ollama 失败({self.base_url}):{exc}"
            ) from exc

        if response.status_code >= 400:
            raise OllamaError(
                f"Ollama 返回 HTTP {response.status_code}:{response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError:
            raise OllamaError("Ollama 返回的不是 JSON")

        content = (data.get("message") or {}).get("content", "")
        if not content.strip():
            raise OllamaError("Ollama 没有返回内容(检查模型是否已加载)")
        return content.strip()

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()


def render_case(pet: dict[str, Any]) -> str:
    """把宠物档案整理成喂给模型的纯文本病历资料。"""
    lines = [
        f"宠物姓名:{pet.get('name', '未知')}",
        f"种类/品种:{pet.get('species', '未知')}/{pet.get('breed') or '未记录'}",
        f"性别/月龄:{pet.get('gender') or '未记录'}/{pet.get('ageMonths', '?')} 个月",
        f"主人:{pet.get('ownerName', '未知')}(电话 {pet.get('ownerPhone', '未记录')})",
        f"主治医生:{pet.get('doctor', '未记录')}",
        f"当前诊断:{pet.get('disease', '未记录')}",
        f"就诊状态:{pet.get('status', '未记录')}",
        f"过敏史:{pet.get('allergy') or '无'}",
        f"就诊次数:{pet.get('visitCount', 0)},总花费:{pet.get('totalCost', 0)} 元",
        "",
        "历史病历:",
    ]
    records = pet.get("records") or []
    for index, record in enumerate(records, start=1):
        drugs = "、".join(record.get("prescription") or []) or "无"
        lines.extend(
            [
                f"[{index}] 就诊日期:{record.get('visitDate') or '未记录'}"
                f";医生:{record.get('doctor') or '未记录'}"
                f";诊断:{record.get('diagnosis') or '未记录'}",
                f"    症状:{record.get('symptoms') or '未记录'}"
                f";处置:{record.get('treatment') or '未记录'}"
                f";用药:{drugs}",
                f"    体重:{record.get('weightKg') or '?'}kg"
                f";体温:{record.get('temperature') or '?'}℃"
                f";本次费用:{record.get('charge', 0)} 元",
                f"    医嘱复查(followUp 原文):{record.get('followUp') or '未记录'}",
            ]
        )
    if not records:
        lines.append("(无历史病历)")
    return "\n".join(lines)
