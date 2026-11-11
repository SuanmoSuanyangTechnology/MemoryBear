"""FAQ retrieval pre-check (business side, shared by workflow knowledge nodes and agent apps).

The knowledge service accepts ``wiki-faq`` as one retrieval type per knowledge base
and rejects parameter combinations it cannot serve. The business side applies the
same criteria before submitting retrieval, so users get the exact reason while
editing a workflow / debugging an agent instead of a downstream 400.

Boundaries mirror mem-knowledge's ``services/wiki_faq_retrieval.validate_request``:

    1. image query on a FAQ knowledge base  -> KB_FAQ_IMAGE_QUERY_UNSUPPORTED
    2. file name filter on a FAQ KB         -> KB_FAQ_FILE_FILTER_UNSUPPORTED
    3. enabled metadata filters on a FAQ KB -> KB_FAQ_METADATA_FILTER_UNSUPPORTED
    4. auto metadata mode, not yet resolved -> KB_FAQ_AUTO_METADATA_UNSUPPORTED

Only these four combinations are refused. Mixing FAQ with participle / semantic /
hybrid / graph knowledge bases in one request is supported: every knowledge base
carries its own ``retrieve_type`` and the candidates are merged and reranked by the
existing cross-KB global rerank, so no "all or nothing" rule is applied here.

Messages are word-for-word identical to ``mem-knowledge/src/locales/zh.json`` (FAQ
block) so the pre-check and the downstream rejection read the same sentence. This
module reads nothing but the request payload and takes no authorization
responsibility.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from ..schemas.chunk_schema import RetrieveType
from ..schemas.knowledge_metadata_schema import MetadataFilterMode

logger = logging.getLogger(__name__)

# Word-for-word identical to mem-knowledge/src/locales/zh.json.
# `image_retrieval_guard` 也要用这一条（FAQ 知识库的图片检索拒绝），两边原本各
# 硬编码一份，改一处就悄悄不一致；统一从这里取。
FAQ_IMAGE_QUERY_UNSUPPORTED_MESSAGE = "FAQ 检索暂不支持图片检索，请使用文本查询。"

_FAQ_BOUNDARY_MESSAGES: dict[str, str] = {
    "KB_FAQ_IMAGE_QUERY_UNSUPPORTED": FAQ_IMAGE_QUERY_UNSUPPORTED_MESSAGE,
    "KB_FAQ_FILE_FILTER_UNSUPPORTED": "FAQ 检索暂不支持文件名过滤，请移除文件名过滤条件。",
    "KB_FAQ_AUTO_METADATA_UNSUPPORTED": "FAQ 检索暂不支持自动元数据过滤，请关闭自动过滤后重试。",
    "KB_FAQ_METADATA_FILTER_UNSUPPORTED": "FAQ 检索暂不支持生效的元数据过滤表达式，请禁用元数据过滤或清空过滤条件。",
}


def _field(source: Any, name: str) -> Any:
    """Read a config field from either a payload dict or a pydantic config object."""
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _as_retrieve_type(value: Any) -> RetrieveType | None:
    if isinstance(value, RetrieveType):
        return value
    if not isinstance(value, str):
        return None
    try:
        return RetrieveType(value)
    except ValueError:
        return None


def faq_knowledge_base(configured: Sequence[Any] | None, retrieve_type: Any = None) -> str | None:
    """Return the id of the first knowledge base that retrieves by FAQ, if any.

    A knowledge base keeps its own ``retrieve_type``; the request-level value is the
    fallback used by knowledge bases that omit the field, mirroring the knowledge
    service's ``_resolve_retrieve_type``.
    """
    request_type = _as_retrieve_type(retrieve_type)
    for config in configured or []:
        kb_type = _as_retrieve_type(_field(config, "retrieve_type")) or request_type
        if kb_type is RetrieveType.WIKI_FAQ:
            kb_id = _field(config, "kb_id")
            return str(kb_id) if kb_id is not None else ""
    return None


def faq_conflict_code(
    *,
    configured: Sequence[Any] | None,
    retrieve_type: Any = None,
    has_image_query: bool = False,
    file_names_filter: Sequence[str] | None = None,
    metadata_filter_mode: Any = None,
    metadata_filter_groups: Sequence[Any] | None = None,
    metadata_filters_resolved: bool = True,
) -> str | None:
    """Return the first FAQ/parameter conflict code, or ``None`` when compatible."""
    kb_id = faq_knowledge_base(configured, retrieve_type)
    if kb_id is None:
        return None

    if has_image_query:
        code = "KB_FAQ_IMAGE_QUERY_UNSUPPORTED"
    elif file_names_filter:
        code = "KB_FAQ_FILE_FILTER_UNSUPPORTED"
    elif _value_of(metadata_filter_mode) not in (None, MetadataFilterMode.DISABLED.value):
        # FAQ ignores no filter: an enable-looking mode is refused unless it carries
        # no effective condition, and unresolved AUTO is refused even with empty
        # groups (nothing was evaluated yet).
        if any(_group_has_conditions(group) for group in metadata_filter_groups or []):
            code = "KB_FAQ_METADATA_FILTER_UNSUPPORTED"
        elif (
            _value_of(metadata_filter_mode) == MetadataFilterMode.AUTO.value
            and not metadata_filters_resolved
        ):
            code = "KB_FAQ_AUTO_METADATA_UNSUPPORTED"
        else:
            code = None
    else:
        code = None

    if code is None:
        return None
    logger.info(
        "faq_retrieval_rejected code=%s kb_id=%s retrieve_type=%s "
        "image_query=%s file_filter=%s metadata_mode=%s metadata_filter_groups=%s",
        code,
        kb_id,
        _value_of(retrieve_type),
        has_image_query,
        len(file_names_filter or []),
        _value_of(metadata_filter_mode),
        len(metadata_filter_groups or []),
    )
    return code


def faq_conflict_message(**kwargs: Any) -> str | None:
    """Same check as :func:`faq_conflict_code`, rendered as the user-visible sentence."""
    code = faq_conflict_code(**kwargs)
    return _FAQ_BOUNDARY_MESSAGES[code] if code is not None else None


def _value_of(value: Any) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip().lower()
    return text or None


def _group_has_conditions(group: Any) -> bool:
    conditions = _field(group, "conditions")
    return bool(conditions)


__all__ = [
    "faq_conflict_code",
    "faq_conflict_message",
    "faq_knowledge_base",
]