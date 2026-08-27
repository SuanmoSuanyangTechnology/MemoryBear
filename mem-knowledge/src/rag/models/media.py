"""Minimal Qwen Omni adapters for worker audio and video pipelines."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import requests

from ...bootstrap import get_settings
from ..chunk.prompts import audio_transcription_prompt, video_transcription_prompt


def _response_text(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Qwen Omni response did not contain message content") from exc
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")).strip()
            for item in content
            if isinstance(item, dict) and item.get("text")
        ).strip()
    return str(content or "").strip()


class _QwenOmniClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        session=requests,
    ) -> None:
        settings = get_settings()
        self._api_key = (
            api_key if api_key is not None else settings.qwen3_omni_api_key.get_secret_value()
        )
        self._model_name = model_name or settings.qwen3_omni_model_name
        self._base_url = (base_url or settings.qwen3_omni_base_url).rstrip("/")
        self._timeout_seconds = timeout_seconds or settings.llm_timeout
        self._session = session

    def _complete(self, content: list[dict[str, Any]]) -> tuple[str, int]:
        if not self._api_key:
            raise RuntimeError("QWEN3_OMNI_API_KEY is not configured")
        try:
            response = self._session.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model_name,
                    "messages": [{"role": "user", "content": content}],
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" status={status}" if status is not None else ""
            raise RuntimeError(f"Qwen Omni request failed:{suffix or ' transport error'}") from None
        text = _response_text(payload)
        if not text:
            raise RuntimeError("Qwen Omni returned empty content")
        usage = payload.get("usage") if isinstance(payload, dict) else None
        token_count = int((usage or {}).get("total_tokens") or 0)
        return text, token_count


class QWenSeq2txt(_QwenOmniClient):
    def __init__(self, *, lang: str = "Chinese", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.lang = lang

    def transcription(self, audio_path: str) -> tuple[str, int]:
        path = Path(audio_path)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return self._complete(
            [
                {"type": "text", "text": audio_transcription_prompt(self.lang)},
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": encoded,
                        "format": path.suffix.lower().lstrip(".") or "wav",
                    },
                },
            ]
        )


class QWenCV(_QwenOmniClient):
    def __init__(self, *, lang: str = "Chinese", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.lang = lang

    def chat(
        self,
        system: str,
        history: list,
        gen_conf: dict,
        *,
        video_bytes: bytes | None = None,
        filename: str = "",
        **kwargs: Any,
    ) -> tuple[str, int]:
        del system, history, gen_conf, kwargs
        if not video_bytes:
            raise RuntimeError("Video bytes are required")
        media_type = mimetypes.guess_type(filename)[0] or "video/mp4"
        encoded = base64.b64encode(video_bytes).decode("ascii")
        return self._complete(
            [
                {"type": "text", "text": video_transcription_prompt(self.lang)},
                {
                    "type": "video_url",
                    "video_url": {"url": f"data:{media_type};base64,{encoded}"},
                },
            ]
        )


__all__ = ["QWenCV", "QWenSeq2txt"]
