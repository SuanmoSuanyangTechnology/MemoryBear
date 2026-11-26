"""Explicit, detached snapshots for the frozen knowledge and document responses."""

from copy import deepcopy
from typing import Any

from ..api.schemas.document import Document as DocumentData
from ..api.schemas.knowledge import Knowledge as KnowledgeData
from ..api.schemas.knowledge import ModelConfigSummary, UserSummary
from ..models.owned import Document, Knowledge
from ..parser_configuration import to_legacy
from ..repositories.parser_configuration import document_configuration, knowledge_configuration


def project_document(document: Document) -> DocumentData:
    """Capture public fields while loaded; serialization needs no ORM session."""
    return DocumentData.model_validate({
        "id": document.id,
        "kb_id": document.kb_id,
        "created_by": document.created_by,
        "file_id": document.file_id,
        "file_name": document.file_name,
        "file_ext": document.file_ext,
        "file_size": document.file_size,
        "file_meta": deepcopy(document.file_meta),
        "parser_id": document.parser_id,
        "parser_config": to_legacy(document_configuration(document)),
        "parent_child_mode": document.parent_child_mode,
        "chunk_num": document.chunk_num,
        "progress": document.progress,
        "progress_msg": document.progress_msg,
        "process_begin_at": document.process_begin_at,
        "process_duration": document.process_duration,
        "run": document.run,
        "status": document.status,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    })


def document_to_data(document: Document) -> dict[str, Any]:
    return project_document(document).model_dump(mode="json")


def project_knowledge(
    knowledge: Knowledge,
    created_user: UserSummary,
    models: dict[str, ModelConfigSummary | None],
) -> KnowledgeData:
    """Preserve legacy parser extras and the manager's model summary unchanged."""
    return KnowledgeData.model_validate({
        "id": knowledge.id,
        "workspace_id": knowledge.workspace_id,
        "created_by": knowledge.created_by,
        "parent_id": knowledge.parent_id,
        "name": knowledge.name,
        "description": knowledge.description,
        "avatar": knowledge.avatar,
        "type": knowledge.type,
        "permission_id": knowledge.permission_id,
        "embedding_id": knowledge.embedding_id,
        "reranker_id": knowledge.reranker_id,
        "llm_id": knowledge.llm_id,
        "image2text_id": knowledge.image2text_id,
        "audio2text_id": knowledge.audio2text_id,
        "video2text_id": knowledge.video2text_id,
        "doc_num": knowledge.doc_num,
        "chunk_num": knowledge.chunk_num,
        "parser_id": knowledge.parser_id,
        "parser_config": to_legacy(knowledge_configuration(knowledge)),
        "external_id": knowledge.external_id,
        "status": knowledge.status,
        "created_at": knowledge.created_at,
        "updated_at": knowledge.updated_at,
        "created_user": created_user.model_copy(deep=True),
        "embedding": deepcopy(models.get("embedding")),
        "reranker": deepcopy(models.get("reranker")),
        "llm": deepcopy(models.get("llm")),
        "image2text": deepcopy(models.get("image2text")),
        "audio2text": deepcopy(models.get("audio2text")),
        "video2text": deepcopy(models.get("video2text")),
    })
