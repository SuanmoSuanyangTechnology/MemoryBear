"""Anonymous exemptions for the file-service surface.

Containment mirrors the hardcoded exemptions in middleware.py:
- exact path shape (UUID segment where applicable) and explicit method;
- public reads never accept credentials: a request carrying
  Authorization/X-API-Key runs the full pipeline (declared identity fails
  closed);
- the share upload is unconditional: it carries the viewer's share access
  token as Authorization: Bearer by contract (never a platform credential);
  the middleware captures it for the forwarder, mirroring /api/public/share.
"""
import re

_UUID = r"[0-9a-fA-F-]{36}"

_STORAGE_PUBLIC_READ_PATTERNS = (
    re.compile(r"/api/storage/files/info-by-url"),
    re.compile(rf"/api/storage/files/{_UUID}/(?:url|public-url|status)"),
    re.compile(rf"/api/storage/(?:public|permanent)/{_UUID}"),
)
_STORAGE_SHARE_UPLOAD_RE = re.compile(r"/api/storage/share/files")


def is_storage_public_read(path: str, method: str) -> bool:
    return method == "GET" and any(
        p.fullmatch(path) for p in _STORAGE_PUBLIC_READ_PATTERNS)


def is_storage_share_upload(path: str, method: str) -> bool:
    return method == "POST" and _STORAGE_SHARE_UPLOAD_RE.fullmatch(path) is not None
