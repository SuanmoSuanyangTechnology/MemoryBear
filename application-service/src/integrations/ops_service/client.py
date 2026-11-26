"""Sync HTTP client of ops-service's internal home-page face.

The endpoint is a public read on the ops side (no credential; source
constrained by NetworkPolicy) and answers a raw payload, not the project
envelope. Every failure degrades to ``None`` so the caller can fall back to
the monolith's last-resort version card instead of failing the request.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_VERSION_PATH = "/internal/v1/home-page/version"


class OpsServiceClient:
    """``transport`` is a test seam (``httpx.MockTransport``)."""

    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._timeout = httpx.Timeout(
            read_timeout_seconds, connect=connect_timeout_seconds
        )
        self._transport = transport

    def get_home_page_version(self) -> dict[str, Any] | None:
        """Return the raw ``{version, introduction, introduction_en}`` payload."""

        if not self._base_url:
            return None
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = client.get(_VERSION_PATH)
        except httpx.HTTPError as exc:
            logger.warning(
                "ops-service home-page version read failed: %s", type(exc).__name__
            )
            return None
        if response.status_code != 200:
            logger.warning(
                "ops-service home-page version read returned %s", response.status_code
            )
            return None
        try:
            payload: Any = response.json()
        except ValueError:
            logger.warning("ops-service home-page version returned invalid JSON")
            return None
        if not isinstance(payload, dict) or not payload.get("version"):
            logger.warning("ops-service home-page version payload is malformed")
            return None
        return payload


__all__ = ["OpsServiceClient"]
