"""Resolve saved document model choices without loading or invoking any models."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ..parser_configuration import DocumentConfiguration, KnowledgeConfiguration


@dataclass(frozen=True)
class DocumentModels:
    vision: UUID | None
    audio: UUID | None
    video: UUID | None
    sources: dict[str, str | None]


def resolve_document_models(
    document: DocumentConfiguration,
    knowledge: KnowledgeConfiguration,
    workspace: Any,
) -> DocumentModels:
    selected: dict[str, UUID | None] = {}
    sources: dict[str, str | None] = {}
    for kind, knowledge_field in (
        ("vision", "image2text_id"),
        ("audio", "audio2text_id"),
        ("video", "video2text_id"),
    ):
        selected[kind], sources[kind] = None, None
        if not document.multimodal_enabled:
            continue
        for source, value in (
            ("document", getattr(document, f"{kind}_model_id")),
            ("knowledge", getattr(knowledge, knowledge_field)),
            ("workspace", getattr(workspace, kind)),
        ):
            if value:
                selected[kind], sources[kind] = UUID(str(value)), source
                break
    return DocumentModels(selected["vision"], selected["audio"], selected["video"], sources)
