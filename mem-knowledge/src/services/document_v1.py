"""Document configuration commands over the shared file and configuration repositories."""

from asyncio import to_thread
from copy import deepcopy
from hashlib import sha256
from os.path import splitext
from typing import Any, cast
from uuid import UUID, uuid4

from error_core.messages import code_catalog
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.document_v1 import ProcessingDispatchResult
from ..errors import KnowledgeError
from ..models.owned import Document, Knowledge
from ..parser_configuration import DocumentConfiguration, Scope, from_api, new_configuration, to_api
from ..parser_configuration.api import configuration_error
from ..parser_configuration.entities import ConfigurationError, KnowledgeConfiguration
from ..repositories.parser_configuration import (
    document_configuration,
    knowledge_configuration,
)
from ..runtime import ProcessRuntime
from ..tasks.dispatch import TaskDispatcher
from . import document as shared
from . import file as file_service
from . import knowledge as knowledge_service
from .knowledge_file_storage import generate_kb_file_key
from .knowledge_model_validation import (
    validate_requested_knowledge_model_availability,
    validate_requested_knowledge_models,
)


async def require_knowledge(db: AsyncSession, kb_id: UUID, principal: Principal) -> Knowledge:
    knowledge = await knowledge_service.get_knowledge(db, kb_id, principal)
    if knowledge is None or knowledge.status == 2:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_NOT_FOUND")
    return knowledge


async def get_document(db: AsyncSession, document_id: UUID, principal: Principal) -> Document:
    document = await shared.get_document(db, document_id, principal)
    if document is None:
        raise KnowledgeError.from_code("KB_DOCUMENT_NOT_FOUND")
    await require_knowledge(db, cast(UUID, document.kb_id), principal)
    return document


def document_to_data(document: Document) -> dict[str, Any]:
    data = shared.document_to_data(document)
    data.pop("parser_config")
    data["processing_configuration"] = to_api(document_configuration(document))
    return data


def document_preset(knowledge: KnowledgeConfiguration) -> DocumentConfiguration:
    values = new_configuration(Scope.DOCUMENT).model_dump()
    for name in values.keys() - {"extras"}:
        if hasattr(knowledge, name):
            values[name] = deepcopy(getattr(knowledge, name))
    values["chunk_method"] = knowledge.chunk_method or "custom"
    values["auto_questions"] = knowledge.auto_questions or 0
    return DocumentConfiguration.model_validate(values)


def merge_configuration(
    current: DocumentConfiguration,
    nested: dict[str, Any],
    knowledge: KnowledgeConfiguration,
) -> DocumentConfiguration:
    try:
        config = cast(DocumentConfiguration, from_api(nested, current))
        if knowledge.index_strategy == "wiki" and config.auto_questions:
            raise ConfigurationError("wiki_qa_conflict", "auto_questions")
        return config
    except ConfigurationError as exc:
        raise configuration_error(exc) from None


async def validate_models(
    db: AsyncSession,
    configuration: DocumentConfiguration,
    principal: Principal,
    runtime: ProcessRuntime,
) -> None:
    models = {
        "image2text_id": configuration.vision_model_id,
        "audio2text_id": configuration.audio_model_id,
        "video2text_id": configuration.video_model_id,
    }
    await validate_requested_knowledge_models(db, models, principal.tenant_id)
    await validate_requested_knowledge_model_availability(models, principal, runtime.model_runtime)


async def prepare_rechunk(
    db: AsyncSession,
    document_id: UUID,
    principal: Principal,
) -> shared.ParseDocumentSnapshot:
    document = await get_document(db, document_id, principal)
    file = await file_service.get_file(
        db,
        cast(UUID, document.file_id),
        principal,
        cast(UUID, document.kb_id),
    )
    if file is None:
        raise KnowledgeError.from_code("KB_FILE_NOT_FOUND")
    if not file.file_key or not str(file.file_key).strip():
        raise KnowledgeError.from_code("KB_FILE_STORAGE_KEY_MISSING")
    return shared.ParseDocumentSnapshot(
        document_id,
        str(file.file_key),
        str(file.file_name),
        cast(UUID, document.kb_id) if document_configuration(document).doc_type == "qa" else None,
    )


def processing_from_dispatch(result: shared.ParseDispatchResult, locale: str) -> dict[str, Any]:
    task_id = None
    if result.task_id:
        try:
            parsed = UUID(result.task_id)
            task_id = str(parsed) if parsed.int else None
        except (ValueError, TypeError, AttributeError):
            pass
    if result.error is not None:
        status = (
            "dispatch_failed"
            if result.publication in {"not_attempted", "not_published"}
            else "dispatch_unknown"
        )
    elif result.publication == "published":
        status = "submitted" if task_id else "dispatch_unknown"
    else:
        status = "already_processing"
    data: dict[str, Any] = {"status": status}
    if task_id and status != "dispatch_failed":
        data["task_id"] = task_id
    if status in {"dispatch_failed", "dispatch_unknown"}:
        code = (
            "KB_TASK_DISPATCH_FAILED" if status == "dispatch_failed" else "KB_TASK_DISPATCH_UNKNOWN"
        )
        data["error"] = {"code": code, "message": code_catalog(locale)[code]}
    return ProcessingDispatchResult.model_validate(data).model_dump(mode="json", exclude_none=True)


async def dispatch_processing(
    runtime: ProcessRuntime,
    snapshot: shared.ParseDocumentSnapshot,
    locale: str,
) -> dict[str, Any]:
    result = await shared.claim_and_dispatch_parse(
        runtime,
        TaskDispatcher(runtime=runtime, classify_preparation_errors=True),
        snapshot,
        preserve_unknown=True,
    )
    return processing_from_dispatch(result, locale)


async def prepare_upload(
    db: AsyncSession,
    kb_id: UUID,
    parent_id: UUID,
    filename: str,
    content: bytes,
    nested: dict[str, Any],
    principal: Principal,
    runtime: ProcessRuntime,
) -> file_service.UploadPlan:
    knowledge = await require_knowledge(db, kb_id, principal)
    await file_service.require_parent_folder(db, kb_id, parent_id, principal)
    preset = knowledge_configuration(knowledge)
    configuration = merge_configuration(document_preset(preset), nested, preset)
    await validate_models(db, configuration, principal, runtime)
    file_id = uuid4()
    extension = splitext(filename)[1].lower()
    return file_service.UploadPlan(
        file_id=file_id,
        file_key=generate_kb_file_key(kb_id, file_id, extension),
        kb_id=kb_id,
        parent_id=parent_id,
        created_by=principal.actor_id,
        file_name=filename,
        file_ext=extension,
        file_size=len(content),
        parser_id=str(knowledge.parser_id),
        configuration=configuration,
        digest=(await to_thread(sha256, content)).hexdigest(),
    )
