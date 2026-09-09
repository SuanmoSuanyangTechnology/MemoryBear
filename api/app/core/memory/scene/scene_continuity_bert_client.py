"""BERT rerank client used for Scene continuity decisions."""

from __future__ import annotations

import math

import httpx

from app.core.config import settings


class SceneContinuityError(RuntimeError):
    pass


class SceneContinuityBertClient:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def predict(self, history_user_messages: list[str], current_user_message: str) -> float:
        if not settings.SCENE_CONTINUITY_URL:
            raise SceneContinuityError("SCENE_CONTINUITY_URL is not configured")
        if not settings.SCENE_CONTINUITY_MODEL:
            raise SceneContinuityError("SCENE_CONTINUITY_MODEL is not configured")

        headers = {"Content-Type": "application/json"}
        if settings.SCENE_CONTINUITY_API_KEY:
            headers["Authorization"] = f"Bearer {settings.SCENE_CONTINUITY_API_KEY}"
        payload = {
            "model": settings.SCENE_CONTINUITY_MODEL,
            "query": current_user_message,
            "documents": history_user_messages,
        }
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=settings.SCENE_CONTINUITY_TIMEOUT_SECONDS)
        try:
            response = await client.post(settings.SCENE_CONTINUITY_URL, headers=headers, json=payload)
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
        finally:
            if owns_client:
                await client.aclose()
