"""Minimal Feishu client copied from the legacy authentication path."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from .exceptions import FeishuAPIError, FeishuAuthError
from .models import FileInfo


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value))
    except (TypeError, ValueError, OSError):
        return None


class FeishuAPIClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        api_base_url: str = "https://open.feishu.cn/open-apis",
        timeout: int = 30,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
    ):
        self.app_id = app_id
        self._app_secret = app_secret
        self.api_base_url = api_base_url
        self.timeout = timeout
        self._client_factory = client_factory
        self._client: Any | None = None
        self._access_token: str | None = None

    async def __aenter__(self) -> FeishuAPIClient:
        self._client = self._client_factory(
            base_url=self.api_base_url,
            timeout=self.timeout,
            headers={"Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, *_args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def get_tenant_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        if self._client is None:
            raise FeishuAuthError("HTTP client not initialized")
        try:
            response = await self._client.post(
                "/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self._app_secret},
            )
        except httpx.HTTPError as exc:
            raise FeishuAuthError("Feishu authentication request failed") from exc
        body = response.json()
        if body.get("code") != 0 or not body.get("tenant_access_token"):
            raise FeishuAuthError("Feishu authentication failed", str(body.get("code")))
        self._access_token = str(body["tenant_access_token"])
        return self._access_token

    async def list_folder_files(
        self,
        folder_token: str,
        page_token: str | None = None,
    ) -> tuple[list[FileInfo], str | None]:
        if self._client is None:
            raise FeishuAPIError("HTTP client not initialized")
        token = await self.get_tenant_access_token()
        params = {"page_size": 200, "folder_token": folder_token}
        if page_token:
            params["page_token"] = page_token
        try:
            response = await self._client.get(
                "/drive/v1/files",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise FeishuAPIError("Feishu folder request failed") from exc
        body = response.json()
        if body.get("code") != 0:
            raise FeishuAPIError("Feishu API request failed", str(body.get("code")))
        data = body.get("data") or {}
        files = [
            FileInfo(
                token=str(item.get("token") or ""),
                name=str(item.get("name") or ""),
                type=str(item.get("type") or ""),
                created_time=_timestamp(item.get("created_time")),
                modified_time=_timestamp(item.get("modified_time")),
                owner_id=str(item.get("owner_id") or ""),
                url=str(item.get("url") or ""),
            )
            for item in data.get("files", [])
            if isinstance(item, dict)
        ]
        return files, data.get("next_page_token")

    async def list_all_folder_files(
        self,
        folder_token: str,
        recursive: bool = True,
    ) -> list[FileInfo]:
        result = []
        page_token = None
        while True:
            files, page_token = await self.list_folder_files(folder_token, page_token)
            result.extend(files)
            if not page_token:
                break
        if recursive:
            for folder in [item for item in result if item.type == "folder"]:
                result.extend(await self.list_all_folder_files(folder.token, recursive=True))
        return result


__all__ = ["FeishuAPIClient"]
