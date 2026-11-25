"""BERT reranker client for single-message emotion classification."""

from __future__ import annotations

import asyncio

import httpx

from src.config import ProfileSettings

_EMOTIONS = frozenset({
    "neutral", "joy", "relief", "anxiety", "sadness",
    "anger", "frustration", "loneliness", "confusion", "hope",
})


class EmotionClassifier:
    def __init__(self, settings: ProfileSettings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    async def predict(self, message: str) -> tuple[str, float] | None:
        settings = self._settings
        api_key = settings.emotion_api_key.get_secret_value()
        if not (settings.emotion_url and api_key and settings.emotion_model):
            return None

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.emotion_http_timeout_seconds, trust_env=False
            )
        async with asyncio.timeout(settings.emotion_total_timeout_seconds):
            response = await self._client.post(
                settings.emotion_url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": settings.emotion_model, "query": message},
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid emotion response")
            emotion = data.get("top_emotion")
            if not isinstance(emotion, str) or emotion not in _EMOTIONS:
                raise ValueError("invalid emotion code")
            score = float(data["top_score"])
            if not 0.0 <= score <= 1.0:
                raise ValueError("invalid emotion score")
        return emotion, score

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
