"""Minimal Yuque client copied from the legacy authentication path."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from .exceptions import YuqueAPIError, YuqueAuthError
from .models import YuqueRepoInfo


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class YuqueAPIClient:
    def __init__(
        self,
        user_id: str,
        token: str,
        api_base_url: str = "https://www.yuque.com/api/v2",
        timeout: int = 30,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
    ):
        self.user_id = user_id
        self._token = token
        self.api_base_url = api_base_url
        self.timeout = timeout
        self._client_factory = client_factory
        self._client: Any | None = None

    async def __aenter__(self) -> YuqueAPIClient:
        self._client = self._client_factory(
            base_url=self.api_base_url,
            timeout=self.timeout,
            headers={
                "Content-Type": "application/json",
                "X-Auth-Token": self._token,
                "User-Agent": "Yuque-Integration-Client",
            },
        )
        return self

    async def __aexit__(self, *_args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def get_user_repos(self) -> list[YuqueRepoInfo]:
        if self._client is None:
            raise YuqueAPIError("HTTP client not initialized")
        try:
            response = await self._client.get(f"/users/{self.user_id}/repos")
        except httpx.HTTPError as exc:
            raise YuqueAPIError("Yuque network request failed") from exc
        if response.status_code == 401:
            raise YuqueAuthError("Yuque authentication failed", "401")
        if response.status_code != 200:
            raise YuqueAPIError("Yuque API request failed", str(response.status_code))
        body = response.json()
        result = []
        for item in body.get("data", []):
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                continue
            result.append(
                YuqueRepoInfo(
                    id=item["id"],
                    type=str(item.get("type") or ""),
                    name=str(item.get("name") or ""),
                    namespace=str(item.get("namespace") or ""),
                    slug=str(item.get("slug") or ""),
                    description=item.get("description"),
                    public=int(item.get("public") or 0),
                    items_count=int(item.get("items_count") or 0),
                    created_at=_parse_time(item.get("created_at")),
                    updated_at=_parse_time(item.get("updated_at")),
                )
            )
        return result


__all__ = ["YuqueAPIClient"]
