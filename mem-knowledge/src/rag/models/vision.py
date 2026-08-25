"""Minimal knowledge-owned adapter for image-capable RedBear models."""

from __future__ import annotations

import base64
from typing import Any

from redbear_model import ResolvedModelConfig
from redbear_model.runtime import RedBearLLM


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")).strip()
            for item in content
            if isinstance(item, dict) and item.get("text")
        ).strip()
    return str(content or "").strip()


class QWenCV:
    """Expose the legacy describe methods over a resolved model snapshot."""

    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        client_pool,
        lang: str = "Chinese",
    ) -> None:
        self.lang = lang
        self._model = RedBearLLM(config, client_pool=client_pool)

    def describe(self, image: bytes) -> tuple[str, int]:
        prompt = (
            "请准确描述图片内容，并提取所有可见文字和数据。"
            if self.lang.lower() == "chinese"
            else "Describe the image accurately and extract all visible text and data."
        )
        return self.describe_with_prompt(image, prompt)

    def describe_with_prompt(
        self,
        image: bytes,
        prompt: str | None = None,
    ) -> tuple[str, int]:
        encoded = base64.b64encode(image).decode("ascii")
        media_type = "image/jpeg"
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        response = self._model.invoke(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or "Describe the image."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                        },
                    ],
                }
            ]
        )
        text = _message_text(response)
        if not text:
            raise RuntimeError("Image model returned empty content")
        return text, 0


__all__ = ["QWenCV"]
