"""网关公开操作的路径和方法匹配。

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

from .termination import SOURCE_EXTERNAL, SOURCE_INTERNAL

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


_KB_DOWNLOAD_PATH = re.compile(
    r"/(?P<face>api|v1)/files/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
)
_KB_PUBLIC_V1_ENUM_PATHS = frozenset({
    "/v1/knowledges/knowledgetype",
    "/v1/knowledges/permissiontype",
    "/v1/knowledges/parsertype",
    "/v1/chunks/retrieve_type",
})


def kb_public_source(method: str, path: str) -> str | None:
    """返回匿名知识库读取的可信来源，用于文件下载和检索类型投影。"""
    if method != "GET":
        return None
    if path in _KB_PUBLIC_V1_ENUM_PATHS:
        return SOURCE_EXTERNAL
    if match := _KB_DOWNLOAD_PATH.fullmatch(path):
        return SOURCE_INTERNAL if match["face"] == "api" else SOURCE_EXTERNAL
    return None
