import httpx
from typing import Any

DEFAULT_API_URL = "http://127.0.0.1:8080"


class PetApiClient:
    def __init__(self, base_url: str = DEFAULT_API_URL):
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        return self._client

    async def list_pets(self, params: dict[str, Any]) -> dict[str, Any]:
        client = await self._get_client()
        query_params: dict[str, Any] = {}
        mapping = {
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
        for mcp_key, api_key in mapping.items():
            val = params.get(mcp_key)
            if val is not None and val != "":
                query_params[api_key] = val
        if params.get("min_cost") is not None:
            query_params["min"] = params["min_cost"]
        if params.get("max_cost") is not None:
            query_params["max"] = params["max_cost"]
        response = await client.get("/api/v1/pets", params=query_params)
        response.raise_for_status()
        json_data = response.json()
        if json_data.get("code") != 200:
            raise httpx.HTTPError(f"API error: {json_data.get('message', 'unknown')}")
        return json_data["data"]

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def create_pet(self, pet_data: dict[str, Any]) -> dict[str, Any]:
        client = await self._get_client()
        response = await client.post("/api/v1/pets", json=pet_data)
        response.raise_for_status()
        json_data = response.json()
        if json_data.get("code") != 200 and json_data.get("code") != 201:
            raise httpx.HTTPError(f"API error: {json_data.get('message', 'unknown')}")
        return json_data["data"]

    async def get_pet(self, pet_id: str) -> dict[str, Any]:
        client = await self._get_client()
        response = await client.get(f"/api/v1/pets/{_check_id(pet_id)}")
        return self._unwrap(response)

    async def add_record(self, pet_id: str, record: dict[str, Any]) -> dict[str, Any]:
        client = await self._get_client()
        response = await client.post(
            f"/api/v1/pets/{_check_id(pet_id)}/records", json=record
        )
        return self._unwrap(response)

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        json_data = response.json()
        if json_data.get("code") not in (200, 201):
            raise httpx.HTTPError(f"API error: {json_data.get('message', 'unknown')}")
        return json_data["data"]


def _check_id(pet_id: str) -> str:
    pet_id = pet_id.strip()
    if not pet_id.startswith("PET-") or "/" in pet_id or ".." in pet_id:
        raise ValueError(f"档案 ID 格式不对，应为 PET-000001，收到: {pet_id!r}")
    return pet_id