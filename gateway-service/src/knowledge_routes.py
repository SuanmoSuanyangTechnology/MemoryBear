"""知识库对外 HTTP 路径的公开行为和版本映射。"""

import re

_V1_DISCOVERY_PATHS = frozenset({
    "/v1/knowledges/knowledgetype",
    "/v1/knowledges/permissiontype",
    "/v1/knowledges/parsertype",
    "/v1/chunks/retrieve_type",
})
_FILE_DOWNLOAD_PATH = re.compile(
    r"/(?:api|v1)/files/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
)
_VERSIONED_QA_PATH = re.compile(r"/chunks/v1/([^/]+)/import_qa")
_V2_POST_TARGETS = {
    "/files/v1/file": "/internal/v2/files/file",
    "/chunks/retrieval": "/internal/v2/chunks/retrieval",
}


def public_knowledge_source(path: str, method: str) -> str | None:
    """返回已发布匿名 GET 所用的知识库来源标识。"""
    if method != "GET":
        return None
    if path in _V1_DISCOVERY_PATHS:
        return "ex_api"
    if _FILE_DOWNLOAD_PATH.fullmatch(path):
        return "ex_api" if path.startswith("/v1/") else "in_api"
    return None


def versioned_knowledge_target(path: str, method: str) -> str | None:
    if method != "POST" or not path.startswith(("/api/", "/v1/")):
        return None
    suffix = "/" + path.split("/", 2)[-1]
    if suffix in _V2_POST_TARGETS:
        return _V2_POST_TARGETS[suffix]
    qa = _VERSIONED_QA_PATH.fullmatch(suffix)
    if qa is not None:
        return f"/internal/v2/chunks/{qa[1]}/import_qa"
    # 尾斜杠仍走原兼容入口，由知识库返回保留查询串的 307。
    return None
