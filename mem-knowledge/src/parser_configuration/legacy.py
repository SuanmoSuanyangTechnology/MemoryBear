"""Legacy write rules at the configuration boundary, separate from migration repair."""

from copy import deepcopy
from typing import Any, cast

from ..errors import KnowledgeError
from ..rag.parser_config import (
    normalize_document_parser_config,
    normalize_knowledge_parser_config_update,
    normalize_new_knowledge_parser_config,
)
from .conversion import KnowledgeContext, from_legacy, parse_bool, to_legacy
from .entities import (
    PLACEHOLDERS,
    CommonConfiguration,
    ConfigurationError,
    DocumentConfiguration,
    KnowledgeConfiguration,
)
from .fields import Scope, fields_for


def _changes_source(data: Any, current: KnowledgeConfiguration) -> bool:
    saved = to_legacy(current)
    return isinstance(data, dict) and any(
        spec.legacy_path in data
        and (
            type(data[spec.legacy_path]) is not type(saved.get(spec.legacy_path))
            or data[spec.legacy_path] != saved.get(spec.legacy_path)
        )
        for spec in fields_for(Scope.KNOWLEDGE)
        if spec.api_path.startswith("sources.")
    )


def _parent_child_enabled(value: Any) -> bool:
    return parse_bool(value) or (isinstance(value, str) and value.strip().lower() == "y")


def _preserves_default_chunking(
    current: CommonConfiguration, converted: CommonConfiguration,
) -> bool:
    return (
        current.chunk_method == "default"
        and converted.chunk_method == "custom"
        and all(
            getattr(current, name) == getattr(converted, name)
            for name in (
                "chunk_token_num", "chunk_overlap", "delimiters",
                "parent_chunk_mode", "parent_chunk_token_num", "parent_chunk_delimiters",
            )
        )
    )


def validate_legacy_qa(data: dict[str, Any], *, knowledge: bool = False) -> None:
    if "parent_chunk_mode" in data and not isinstance(data["parent_chunk_mode"], str):
        raise ConfigurationError("invalid_string", "parent_chunk_mode")
    parent_child = (
        _parent_child_enabled(data["parent_child_mode"])
        if "parent_child_mode" in data
        else data.get("parent_chunk_mode") in {"paragraph", "full-doc"}
    )
    if knowledge and data.get("parent_chunk_mode") in {"paragraph", "full-doc"}:
        parent_child = True
    try:
        questions = float(data.get("auto_questions") or 0)
    except (TypeError, ValueError, OverflowError):
        raise ConfigurationError("invalid_number", "auto_questions") from None
    if parent_child and questions > 0:
        raise KnowledgeError.from_code("KB_PARSER_CONFIG_PARENT_CHILD_QA_CONFLICT")


def create_knowledge_configuration(values: dict[str, Any]) -> KnowledgeConfiguration:
    try:
        requested = deepcopy(values.get("parser_config") or {})
        validate_legacy_qa(requested, knowledge=True)
        if "parent_child_mode" in requested:
            requested["parent_child_mode"] = _parent_child_enabled(requested["parent_child_mode"])
        data = normalize_new_knowledge_parser_config(requested)
        if values.get("type") == "Web":
            # The historical constructor's sample URL is not a submitted source.
            data["entry_url"] = requested.get("entry_url")
        return cast(
            KnowledgeConfiguration,
            from_legacy(
                data,
                Scope.KNOWLEDGE,
                context=KnowledgeContext(
                    knowledge_type=values.get("type") or "General",
                    image2text_id=values.get("image2text_id"),
                    audio2text_id=values.get("audio2text_id"),
                    video2text_id=values.get("video2text_id"),
                ),
            ).config,
        )
    except ValueError as exc:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_PARSER_CONFIG_INVALID") from exc


def immutable_field(field: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_KNOWLEDGE_FIELD_IMMUTABLE", params={"field": field})


def update_knowledge_configuration(
    current: KnowledgeConfiguration,
    updates: dict[str, Any],
) -> KnowledgeConfiguration:
    if "type" in updates and updates["type"] != current.knowledge_type:
        raise immutable_field("type")
    if "parser_config" not in updates:
        return current.model_copy(
            update={
                name: updates[name]
                for name in ("image2text_id", "audio2text_id", "video2text_id")
                if name in updates
            }
        )
    try:
        data = to_legacy(current)
        if "parser_config" in updates:
            requested = updates["parser_config"]
            if isinstance(requested, dict):
                validate_legacy_qa(requested, knowledge=True)
                platform = requested.get("_third_party_platform", current.third_party_platform)
                if (
                    current.third_party_platform is not None
                    and platform != current.third_party_platform
                ):
                    raise immutable_field("third_party_platform")
                # Adding the other platform's credentials cannot change the source platform.
                other_id = (
                    "feishu_app_id" if current.third_party_platform == "yuque" else "yuque_user_id"
                )
                other = requested.get(other_id)
                if (
                    current.third_party_platform
                    and isinstance(other, str)
                    and (other.strip() and other.strip() not in PLACEHOLDERS)
                ):
                    raise immutable_field("third_party_platform")
            data = normalize_knowledge_parser_config_update(data, requested)
        if data.get("wiki", {}).get("enabled", False) != (
            current.index_strategy in {"wiki", "rag_wiki"}
        ):
            raise immutable_field("index_strategy")
        validate_legacy_qa(data, knowledge=True)
        if current.index_strategy == "wiki" and float(data.get("auto_questions") or 0) > 0:
            raise ValueError("wiki_qa_conflict")
        if "parent_child_mode" in data:
            data["parent_child_mode"] = _parent_child_enabled(data["parent_child_mode"])
        converted = cast(
            KnowledgeConfiguration,
            from_legacy(
                data,
                Scope.KNOWLEDGE,
                context=KnowledgeContext(
                    knowledge_type=current.knowledge_type,
                    image2text_id=updates.get("image2text_id", current.image2text_id),
                    audio2text_id=updates.get("audio2text_id", current.audio2text_id),
                    video2text_id=updates.get("video2text_id", current.video2text_id),
                ),
                source_config=None if _changes_source(requested, current) else current,
            ).config,
        )
        if _preserves_default_chunking(current, converted):
            converted.chunk_method = "default"
        # Legacy wiki.enabled only exposes one bit; it cannot distinguish wiki/rag_wiki.
        return converted.model_copy(
            update={
                "index_strategy": current.index_strategy,
                "multimodal_enabled": current.multimodal_enabled,
            }
        )
    except ValueError as exc:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_PARSER_CONFIG_INVALID") from exc


def document_configuration_from_legacy(
    data: dict[str, Any],
    knowledge: KnowledgeConfiguration,
    *,
    current: DocumentConfiguration | None = None,
) -> DocumentConfiguration:
    if not isinstance(data, dict):
        raise KnowledgeError.from_code("KB_PARSER_CONFIG_OBJECT_REQUIRED")
    try:
        validate_legacy_qa(data)
        normalized = normalize_document_parser_config(data)
        if knowledge.index_strategy == "wiki" and float(normalized.get("auto_questions") or 0) > 0:
            raise ValueError("wiki_qa_conflict")
        config = cast(DocumentConfiguration, from_legacy(normalized, Scope.DOCUMENT).config)
        if current is not None and _preserves_default_chunking(current, config):
            config.chunk_method = "default"
        return config.model_copy(
            update={
                "multimodal_enabled": (
                    current.multimodal_enabled if current else knowledge.multimodal_enabled
                ),
                **(
                    {
                        name: getattr(current, name)
                        for name in (
                            "vision_model_id",
                            "audio_model_id",
                            "video_model_id",
                        )
                    }
                    if current
                    else {}
                ),
            }
        )
    except ValueError as exc:
        raise KnowledgeError.from_code("KB_DOCUMENT_PARSER_CONFIG_INVALID") from exc


def validate_document_chunk_mode(
    knowledge: KnowledgeConfiguration,
    document: DocumentConfiguration,
    *,
    current: DocumentConfiguration | None = None,
) -> None:
    if current is not None and document.chunk_method == current.chunk_method:
        return
    if knowledge.chunk_method is not None and (
        (knowledge.chunk_method == "parent_child") != (document.chunk_method == "parent_child")
    ):
        raise KnowledgeError.from_code("KB_DOCUMENT_CHUNK_MODE_CHANGE_FORBIDDEN")


def reflect_first_document(
    knowledge: KnowledgeConfiguration,
    data: dict[str, Any],
) -> KnowledgeConfiguration:
    merged = to_legacy(knowledge)
    merged.update(normalize_document_parser_config(data))
    if "parent_child_mode" in merged:
        merged["parent_child_mode"] = _parent_child_enabled(merged["parent_child_mode"])
    try:
        validate_legacy_qa(merged, knowledge=True)
        config = cast(
            KnowledgeConfiguration,
            from_legacy(
                merged,
                Scope.KNOWLEDGE,
                context=KnowledgeContext(
                    knowledge_type=knowledge.knowledge_type,
                    image2text_id=knowledge.image2text_id,
                    audio2text_id=knowledge.audio2text_id,
                    video2text_id=knowledge.video2text_id,
                ),
                source_config=knowledge if knowledge.source_issues() else None,
            ).config,
        )
    except ValueError as exc:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_PARSER_CONFIG_INVALID") from exc
    if config.third_party_platform != knowledge.third_party_platform:
        raise immutable_field("third_party_platform")
    return config.model_copy(
        update={
            "index_strategy": knowledge.index_strategy,
            "multimodal_enabled": knowledge.multimodal_enabled,
        }
    )
