import os

import httpx
from typing import Any

DEFAULT_API_URL = os.environ.get("ANYTHINGLLM_API_URL", "http://localhost:3001")
DEFAULT_API_KEY = os.environ.get("ANYTHINGLLM_API_KEY", "")


class AnythingLLMClient:
    def __init__(self, base_url: str = DEFAULT_API_URL, api_key: str = DEFAULT_API_KEY):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                trust_env=False,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def list_workspaces(self) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = await client.get("/api/v1/workspaces")
        response.raise_for_status()
        json_data = response.json()
        if not json_data.get("workspaces"):
            raise httpx.HTTPError("No workspaces found or invalid response")
        return json_data["workspaces"]

    async def chat_with_workspace(
        self, workspace_slug: str, message: str, mode: str = "query"
    ) -> dict[str, Any]:
        client = await self._get_client()
        payload = {
            "message": message,
            "mode": mode,
        }
        response = await client.post(
            f"/api/v1/workspace/{workspace_slug}/chat", json=payload
        )
        response.raise_for_status()
        json_data = response.json()
        if json_data.get("error"):
            raise httpx.HTTPError(f"Chat error: {json_data['error']}")
        text_response = json_data.get("textResponse") or json_data.get("response") or ""
        return {
            "response": text_response,
            "sources": json_data.get("sources", []),
            "type": json_data.get("type", "textResponse"),
        }

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()