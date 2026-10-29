import os
from typing import Any

import httpx

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11435")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-vl:4b-instruct")

SYSTEM_PROMPT = (
    "你是宠物医院的临床助理。根据给出的历史病历，用中文输出三段："
    "一、病情小结；二、随访与复查提醒（写明建议时间）；三、主人居家护理注意事项。"
    "每段不超过三句话，语言简洁。"
    "严格只使用病历中已有的信息：复查要求直接照抄病历里的 followUp 原文，"
    "不要换算成具体日期、不要自行推算；病历中没有的内容一律不要写。"
)


class OllamaClient:
    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=180.0)

    async def generate(self, prompt: str) -> str:
        response = await self._client.post(
            "/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.3},
            },
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return (data.get("message") or {}).get("content", "").strip()

    async def close(self) -> None:
        await self._client.aclose()


def build_prompt(pet: dict[str, Any]) -> str:
    lines = [
        f"宠物：{pet.get('name')}（{pet.get('species')}/{pet.get('breed')}，"
        f"{pet.get('ageMonths')}月龄，{pet.get('gender')}）",
        f"主人：{pet.get('ownerName')} {pet.get('ownerPhone')}",
        f"主治医生：{pet.get('doctor')}，当前诊断：{pet.get('disease')}，"
        f"状态：{pet.get('status')}，过敏史：{pet.get('allergy') or '无'}",
        f"就诊次数：{pet.get('visitCount')}，总花费：{pet.get('totalCost')}",
        "历史病历：",
    ]
    for rec in pet.get("records") or []:
        drugs = "、".join(rec.get("prescription") or []) or "无"
        lines.append(
            f"- {rec.get('visitDate')} {rec.get('doctor')}：{rec.get('diagnosis')}"
            f"；症状：{rec.get('symptoms') or '未记录'}"
            f"；处置：{rec.get('treatment') or '未记录'}"
            f"；用药：{drugs}"
            f"；体重{rec.get('weightKg') or '?'}kg，体温{rec.get('temperature') or '?'}℃"
            f"；医嘱复查：{rec.get('followUp') or '未记录'}"
            f"；费用：{rec.get('charge', 0)}"
        )
    return "\n".join(lines)
