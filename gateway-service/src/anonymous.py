"""Anonymous exemptions for the file-service surface and workflow webhook.

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


_WORKFLOW_WEBHOOK_RE = re.compile(r"/api/workflows/triggers/webhook/[^/]+")
_WORKFLOW_WEBHOOK_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
)


def is_workflow_webhook(path: str, method: str) -> bool:
    """Anonymous workflow-webhook entry: the publisher configured this URL
    for external systems, which carry no platform credential (the monolith
    route had no auth dependency either). Deliberately unconditional."""
    return method in _WORKFLOW_WEBHOOK_METHODS and (
        _WORKFLOW_WEBHOOK_RE.fullmatch(path) is not None
    )


_LICENSE_STATUS_METHODS = frozenset({"GET", "HEAD"})


def is_license_status(path: str, method: str) -> bool:
    """Anonymous license-status read: the console polls it to render the
    expiry banner, including while the license middleware rejects other
    paths; the monolith route had no auth dependency either."""
    return method in _LICENSE_STATUS_METHODS and path == "/api/license/status"


def is_home_page_version(path: str, method: str) -> bool:
    """Anonymous home-page version card: the monolith route had no auth
    dependency and the dashboard renders the card regardless of login state
    (the split's public path mirrors it). Deliberately unconditional — the
    route consumes no identity, so a stray credential must not reject it."""
    return method == "GET" and path == "/api/home-page/version"
