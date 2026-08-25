"""Complete-image visual model helper extracted from the legacy picture module."""

from __future__ import annotations

import io
import re
from collections.abc import Callable


def _clean_markdown_block(text: str) -> str:
    cleaned = re.sub(r"^\s*```markdown\s*\n?", "", text, flags=re.IGNORECASE)
    return re.sub(r"\n?\s*```\s*$", "", cleaned).strip()


def vision_llm_chunk(
    image,
    vision_model,
    prompt: str | None = None,
    callback: Callable | None = None,
) -> str:
    """Describe one PIL-compatible image and return normalized Markdown text."""

    callback = callback or (lambda _progress, _message: None)
    try:
        with io.BytesIO() as image_binary:
            try:
                image.save(image_binary, format="JPEG")
            except Exception:
                image_binary.seek(0)
                image_binary.truncate()
                image.save(image_binary, format="PNG")
            description, _token_count = vision_model.describe_with_prompt(
                image_binary.getvalue(),
                prompt,
            )
        return _clean_markdown_block(str(description or ""))
    except Exception as exc:  # noqa: BLE001 - preserve best-effort visual enhancement.
        callback(-1, f"Vision model failed: {type(exc).__name__}")
        raise


__all__ = ["vision_llm_chunk"]
