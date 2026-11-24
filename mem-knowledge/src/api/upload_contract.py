"""知识库新版上传入口及其错误契约。"""

from error_core.knowledge_upload import FILE_UPLOAD_PATH, is_versioned_upload
from fastapi import Request
from starlette.routing import compile_path

GATEWAY_UPLOAD_PREFIX = "/internal/v1"
GATEWAY_DOCUMENT_UPLOAD_PATH = FILE_UPLOAD_PATH
GATEWAY_QA_UPLOAD_PATH = "/chunks/v1/{kb_id}/import_qa"

_GATEWAY_UPLOAD_PATTERNS = tuple(
    compile_path(GATEWAY_UPLOAD_PREFIX + path)[0]
    for path in (GATEWAY_DOCUMENT_UPLOAD_PATH, GATEWAY_QA_UPLOAD_PATH)
)


def is_knowledge_upload(request: Request) -> bool:
    if request.method != "POST":
        return False
    path = request.url.path.removesuffix("/")
    return is_versioned_upload(request, internal=True) or any(
        pattern.fullmatch(path) for pattern in _GATEWAY_UPLOAD_PATTERNS
    )
