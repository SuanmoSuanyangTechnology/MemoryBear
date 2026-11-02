"""Artifact ids (design doc 7.4): stateless, signed, expiring pointers to one file.

An aid carries ``(user digest, absolute path, size, mtime_ms, expiry)`` and an
HMAC-SHA256 signature. Nothing is stored, so any replica can verify it. The caller must
still present the same end-user identity: the digest in the aid has to match, so a
leaked aid is useless to anyone else.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import posixpath
from dataclasses import dataclass

from ..errors import SandboxError

_CONTENT_TYPES = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".zip": "application/zip",
}
DEFAULT_CONTENT_TYPE = "application/octet-stream"


def content_type_for(name: str) -> str:
    """Fixed table, not ``mimetypes``: slim images often lack the Office types."""

    return _CONTENT_TYPES.get(posixpath.splitext(name)[1].lower(), DEFAULT_CONTENT_TYPE)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class ArtifactClaims:
    digest: str
    path: str
    size: int
    mtime_ms: int
    expires_at: int  # unix seconds


class ArtifactSigner:
    def __init__(self, key: str | bytes) -> None:
        self._key = key.encode("utf-8") if isinstance(key, str) else key

    def _mac(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())

    def issue(self, claims: ArtifactClaims) -> str:
        payload = json.dumps(
            [claims.digest, claims.path, claims.size, claims.mtime_ms, claims.expires_at],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        body = _b64(payload.encode("utf-8"))
        return f"{body}.{self._mac(body)}"

    def verify(self, aid: str, *, now_s: float) -> ArtifactClaims:
        """Return the claims of a genuine, unexpired aid; otherwise ``artifact_gone``.

        A forged or malformed aid and an expired one are reported the same way on
        purpose: the caller learns nothing about which part was wrong.
        """

        gone = SandboxError("artifact_gone", "the artifact id is invalid or expired")
        if not isinstance(aid, str) or aid.count(".") != 1:
            raise gone
        body, mac = aid.split(".")
        if not hmac.compare_digest(mac, self._mac(body)):
            raise gone
        try:
            digest, path, size, mtime_ms, expires_at = json.loads(_unb64(body))
            claims = ArtifactClaims(str(digest), str(path), int(size), int(mtime_ms), int(expires_at))
        except (ValueError, TypeError, binascii.Error):
            raise gone from None
        if now_s >= claims.expires_at:
            raise gone
        return claims
