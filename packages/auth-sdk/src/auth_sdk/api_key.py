"""API key 集中校验 client（评审焦点 #16 方案 A：identity 集中校验为默认）。

identity POST /internal/api-key-verify：入参 Bearer <api_key>，返回 claims（api_key_id/
tenant_id/workspace_id/scopes）或 401。服务侧凭据校验（答「你是谁」）；授权（白名单/ACL/
租户隔离）仍在各服务本地（身份验证 vs 授权分层，v0.2.6）。
"""
from __future__ import annotations

import httpx


class ApiKeyVerifyUnavailable(Exception):
    """identity 不可用：调用方按 fail-closed 处理。"""


class ApiKeyVerifier:
    def __init__(self, verify_url: str, client: httpx.AsyncClient | None = None,
                 timeout_ms: int = 3000) -> None:
        self._verify_url = verify_url
        self._client = client or httpx.AsyncClient(timeout=timeout_ms / 1000)

    async def verify(self, api_key: str) -> dict | None:
        try:
            resp = await self._client.post(
                self._verify_url, headers={"authorization": f"Bearer {api_key}"})
        except httpx.HTTPError as exc:
            raise ApiKeyVerifyUnavailable(str(exc)) from exc
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:  # JSONDecodeError 是 ValueError 子类
                raise ApiKeyVerifyUnavailable(
                    f"identity non-json response: {exc}") from exc
        if resp.status_code == 401:
            return None
        raise ApiKeyVerifyUnavailable(f"identity status {resp.status_code}")

    async def aclose(self) -> None:
        await self._client.aclose()
