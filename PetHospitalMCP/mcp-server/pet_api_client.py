"""宠物医院 REST API 客户端。

后端统一响应信封:`{"code": 200, "message": "ok", "data": ...}`;
新增 / 追加病历成功时 code 为 201。本模块把信封拆开,只把 data 交给上层,
失败统一抛 PetApiError(带后端 message),由 MCP 工具转成错误结果。

服务地址从环境变量 PET_API_URL 读取,默认 http://127.0.0.1:8080。
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT = 30.0

# 合法档案 ID:PET-数字。限制字符集是为了防止把 "../admin/compact" 拼进 URL
PET_ID_PATTERN = re.compile(r"PET-\d{1,12}")

# MCP 参数名 → REST API 查询参数名(list_pets 用)
_LIST_PARAM_MAP = {
    "species": "species",
    "doctor": "doctor",
    "status": "status",
    "disease": "disease",
    "search": "q",
    "owner_name": "ownerName",
    "owner_phone": "ownerPhone",
    "sort_by": "sortBy",
    "order": "order",
    "page": "page",
    "page_size": "pageSize",
}


class PetApiError(RuntimeError):
    """调用宠物医院 REST API 失败。"""


def validate_pet_id(pet_id: str) -> str:
    """校验并规范化档案 ID,不合法时抛 PetApiError。"""
    cleaned = (pet_id or "").strip()
    if not PET_ID_PATTERN.fullmatch(cleaned):
        raise PetApiError(f"档案 ID 格式不对,应形如 PET-000001,收到:{pet_id!r}")
    return cleaned


class PetHospitalClient:
    """宠物医院 API 异步客户端(连接懒创建、可注入传输层便于测试)。"""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("PET_API_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
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

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        """发请求并拆响应信封;任何失败都转成 PetApiError。"""
        try:
            response = await self._client().request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PetApiError(f"连接宠物医院服务失败:{exc}") from exc

        try:
            envelope = response.json()
        except ValueError:
            raise PetApiError(
                f"服务返回的不是 JSON(HTTP {response.status_code})"
            )

        if not isinstance(envelope, dict):
            raise PetApiError("服务响应格式异常:不是 JSON 对象")

        code = envelope.get("code")
        if response.status_code >= 400 or code not in (200, 201):
            message = envelope.get("message") or f"HTTP {response.status_code}"
            raise PetApiError(f"宠物医院接口报错:{message}")

        if "data" not in envelope:
            raise PetApiError("服务响应缺少 data 字段")
        return envelope["data"]

    # ---- 查询 ----
    async def list_pets(self, filters: dict[str, Any]) -> dict[str, Any]:
        """按筛选条件查询宠物列表,返回 items/total/page/... 字典。"""
        params: dict[str, Any] = {}
        for mcp_key, api_key in _LIST_PARAM_MAP.items():
            value = filters.get(mcp_key)
            if value is not None and value != "":
                params[api_key] = value
        if filters.get("min_cost") is not None:
            params["min"] = filters["min_cost"]
        if filters.get("max_cost") is not None:
            params["max"] = filters["max_cost"]

        data = await self._call("GET", "/api/v1/pets", params=params)
        if not isinstance(data, dict) or "items" not in data:
            raise PetApiError("列表响应缺少 items 字段")
        return data

    async def get_pet(self, pet_id: str) -> dict[str, Any]:
        """按 ID 取完整档案(含 records / charges)。"""
        data = await self._call("GET", f"/api/v1/pets/{validate_pet_id(pet_id)}")
        if not isinstance(data, dict):
            raise PetApiError("档案响应格式异常")
        return data

    # ---- 写入 ----
    async def create_pet(self, pet: dict[str, Any]) -> dict[str, Any]:
        """新建档案,返回后端生成 ID 后的完整档案。"""
        data = await self._call("POST", "/api/v1/pets", json=pet)
        if not isinstance(data, dict) or not data.get("id"):
            raise PetApiError("新建档案成功但响应里没有 id")
        return data

    async def add_record(self, pet_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """向档案追加一条病历,返回刷新了 visitCount/totalCost 的完整档案。"""
        path = f"/api/v1/pets/{validate_pet_id(pet_id)}/records"
        data = await self._call("POST", path, json=record)
        if not isinstance(data, dict):
            raise PetApiError("追加病历响应格式异常")
        return data

    async def aclose(self) -> None:
        """关闭连接,可重复调用。"""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
