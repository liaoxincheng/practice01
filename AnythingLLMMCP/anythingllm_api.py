"""AnythingLLM HTTP API 的极简异步客户端。

只封装 MCP 工具需要的两个接口:
- 列出工作区(下拉/选择用)
- 向指定工作区提问(检索问答)

服务地址与 API Key 从环境变量读取,不写死在代码里:
    ANYTHINGLLM_API_URL  默认 http://localhost:3001
    ANYTHINGLLM_API_KEY  必填,AnythingLLM「设置 → 工具 → API 密钥」生成
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:3001"
DEFAULT_TIMEOUT = 30.0


class AnythingLLMError(RuntimeError):
    """调用 AnythingLLM 失败(网络、鉴权、业务错误等)的统一异常。"""


class WorkspaceClient:
    """AnythingLLM 工作区 API 客户端。

    :param base_url: 服务地址,缺省读环境变量 ANYTHINGLLM_API_URL
    :param api_key: API Key,缺省读环境变量 ANYTHINGLLM_API_KEY
    :param transport: 可注入的 httpx 传输层(测试时用 MockTransport)
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("ANYTHINGLLM_API_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("ANYTHINGLLM_API_KEY", "")
        self.timeout = timeout
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    # ---- 内部:懒创建连接 ----
    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                # 本地服务,不走系统代理
                trust_env=False,
                transport=self._transport,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._http

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """发请求并把各类失败统一转成 AnythingLLMError。"""
        if not self.api_key:
            raise AnythingLLMError(
                "未配置 API Key:请设置环境变量 ANYTHINGLLM_API_KEY"
            )
        try:
            response = await self._client().request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AnythingLLMError(f"连接 AnythingLLM 失败:{exc}") from exc

        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code >= 400:
            detail = ""
            if isinstance(data, dict):
                detail = str(data.get("error") or data.get("message") or "")
            raise AnythingLLMError(
                f"AnythingLLM 返回 HTTP {response.status_code}"
                + (f":{detail}" if detail else "")
            )
        if not isinstance(data, dict):
            raise AnythingLLMError("AnythingLLM 返回的不是 JSON 对象")

        error = data.get("error")
        if error:
            raise AnythingLLMError(f"AnythingLLM 业务错误:{error}")
        return data

    # ---- 对外接口 ----
    async def list_workspaces(self) -> list[dict[str, Any]]:
        """列出全部工作区,每项至少含 name 与 slug。"""
        data = await self._request("GET", "/api/v1/workspaces")
        workspaces = data.get("workspaces")
        if not isinstance(workspaces, list):
            raise AnythingLLMError("响应里没有 workspaces 列表")
        return workspaces

    async def ask(
        self, workspace_slug: str, message: str, mode: str = "query"
    ) -> dict[str, Any]:
        """在一个工作区里提问。

        :param mode: query = 只基于该工作区文档检索回答(不记住上下文);
                     chat  = 带上该工作区的聊天历史
        :return: {"reply": 回答文本, "sources": 来源, "mode": 实际模式}
        """
        if not workspace_slug.strip():
            raise AnythingLLMError("workspace_slug 不能为空")
        if not message.strip():
            raise AnythingLLMError("message 不能为空")
        if mode not in ("query", "chat"):
            raise AnythingLLMError("mode 只能是 query 或 chat")

        data = await self._request(
            "POST",
            f"/api/v1/workspace/{workspace_slug}/chat",
            json={"message": message, "mode": mode},
        )
        reply = data.get("textResponse") or data.get("response") or ""
        if not reply:
            raise AnythingLLMError("AnythingLLM 没有返回回答文本")
        return {
            "reply": reply,
            "sources": data.get("sources") or [],
            "mode": mode,
        }

    async def aclose(self) -> None:
        """关闭底层连接;可重复调用。"""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
