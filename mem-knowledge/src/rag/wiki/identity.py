"""Wiki 主题身份不依赖模型自由填写的 scope。"""

import unicodedata
from typing import Any

from .config import WikiError, fingerprint, normalize_name, source_reference_key

TOPIC_IDENTITY_VERSION = "wiki_topic_identity_v1"


def topic_identity(kind: str, name: str) -> tuple[str, str]:
    return kind, "".join(unicodedata.normalize("NFKC", name).casefold().split())


def merge_candidate(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    target["source_refs"] = sorted(
        set(target["source_refs"]) | set(incoming["source_refs"]), key=source_reference_key,
    )
    target["aliases"] = sorted(
        (set(target["aliases"]) | set(incoming["aliases"]) | {incoming["name"]})
        - {target["name"]},
    )


def same_name_target(
    candidate: dict[str, Any], pages: list[dict[str, Any]], *, kb_id: str, document_id: str,
) -> dict[str, Any] | None:
    identity = topic_identity(candidate["kind"], candidate["name"])
    matches = [page for page in pages if topic_identity(page["kind"], page["name"]) == identity]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    # 历史重复页只沿用已明确的归属，不让模型从同名候选中任意选页。
    explicit = [page for page in matches if page["page_id"] == candidate.get("existing_page_id")]
    if explicit:
        return explicit[0]
    old_key = fingerprint([
        candidate["kind"], normalize_name(candidate["name"]),
        normalize_name(candidate["scope"]) or str(document_id),
    ])
    legacy = [page for page in matches if page.get("canonical_key") == old_key]
    if len(legacy) == 1:
        return legacy[0]
    raise WikiError("WIKI_IDENTITY_AMBIGUOUS", retryable=False, diagnostics={
        "stage": "wiki_identity_v2", "knowledge_id": str(kb_id),
        "document_id": str(document_id), "kind": identity[0], "identity_name": identity[1],
        "page_ids": sorted(page["page_id"] for page in matches),
        "error_message": "同名同类型主题对应多个历史页面，无法唯一确定归属，需要定向处理重复页",
    })
