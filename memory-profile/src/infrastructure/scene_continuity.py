"""BERT client used only by the Scene continuity demo endpoint."""

from __future__ import annotations

import asyncio
import math

import httpx

from src.config import ProfileSettings


class SceneContinuityError(RuntimeError):
    pass


class SceneContinuityClient:
    def __init__(self, settings: ProfileSettings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_client(self) -> httpx.AsyncClient:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._loop is not current_loop:
            raise RuntimeError("Scene continuity client cannot be shared across event loops")
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.scene_continuity_timeout_seconds,
                trust_env=False,
            )
            self._loop = current_loop
        return self._client

    async def predict(self, history_user_messages: list[str], current_user_message: str) -> float:
        settings = self._settings
        if not settings.scene_continuity_url:
            raise SceneContinuityError("SCENE_CONTINUITY_URL is not configured")
        if not settings.scene_continuity_model:
            raise SceneContinuityError("SCENE_CONTINUITY_MODEL is not configured")

        headers = {"Content-Type": "application/json"}
        if settings.scene_continuity_api_key:
            headers["Authorization"] = f"Bearer {settings.scene_continuity_api_key}"
        payload = {
            "model": settings.scene_continuity_model,
            "query": current_user_message,
            "documents": history_user_messages,
        }
        try:
            response = await self._get_client().post(
                settings.scene_continuity_url, headers=headers, json=payload
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise SceneContinuityError(f"Scene continuity HTTP {response.status_code}")
            try:
                data = response.json()
            except Exception as exc:
                raise SceneContinuityError("Scene continuity response is not JSON") from exc
            score = data.get("score") if isinstance(data, dict) else None
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise SceneContinuityError("Scene continuity score is missing or invalid")
            score = float(score)
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise SceneContinuityError("Scene continuity score must be within [0, 1]")
            return score
        except SceneContinuityError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
            raise SceneContinuityError(str(exc)) from exc

    async def aclose(self) -> None:
        client, self._client = self._client, None
        self._loop = None
        if client is not None:
            await client.aclose()
