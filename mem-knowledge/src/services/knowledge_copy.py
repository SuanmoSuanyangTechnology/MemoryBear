"""Atomic configuration copying for ordinary private knowledge bases."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.common import SuccessEnvelope
from ..api.schemas.knowledge_metadata import KnowledgeMetadataCreate
from ..errors import KnowledgeError
from ..models.owned import Knowledge, KnowledgeType, PermissionType
from ..models.references import ModelConfig
from ..rag.knowledge_graph import (
    GraphPipeline,
    require_graph_mapping,
    resolve_graph_pipeline,
)
from ..repositories import knowledge as knowledge_repository
from ..repositories.knowledge_metadata import KnowledgeMetadataRepository
from ..repositories.reference import ReferenceRepository
from ..utils.datetime_utils import utcnow_naive
from . import knowledge as knowledge_service
from .knowledge_metadata import KnowledgeMetadataService

_MODEL_REFERENCE_FIELDS = (
    "embedding_id",
    "reranker_id",
    "llm_id",
    "image2text_id",
)


def choose_copy_name(source_name: str, occupied_names: set[str]) -> str:
    """Return the first available numbered copy name."""
    base = f"{source_name}_副本"
    if base not in occupied_names:
        return base

    suffix = 2
    while f"{base}{suffix}" in occupied_names:
        suffix += 1
    return f"{base}{suffix}"


def copy_parser_config(source_config: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy parser settings while selecting the current graph pipeline."""
    if not isinstance(source_config, dict):
        raise ValueError("Source parser_config must be an object")

    resolve_graph_pipeline(source_config)
    result = deepcopy(source_config)
    graph = dict(require_graph_mapping(result))
    graph["pipeline"] = GraphPipeline.EVIDENCE.value
    result["graphrag"] = graph
    return result


def _validation(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_VALIDATION_ERROR", message)


def _resource_not_found() -> KnowledgeError:
    return KnowledgeError.from_code(
        "KB_RESOURCE_NOT_FOUND",
        "Knowledge resource not found",
    )


def _principal_invalid(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_PRINCIPAL_INVALID", message)


def _model_unavailable(field_name: str) -> KnowledgeError:
    return KnowledgeError.from_code(
        "KB_MODEL_UNAVAILABLE",
        f"Source model reference is unavailable: {field_name}",
    )


async def _validate_principal_references(
    db: AsyncSession,
    principal: Principal,
) -> None:
    workspace = await ReferenceRepository.get_workspace(db, principal.workspace_id)
    if (
        workspace is None
        or workspace.is_active is not True
        or workspace.tenant_id != principal.tenant_id
    ):
        raise _principal_invalid("Invalid knowledge workspace principal")

    user = await ReferenceRepository.get_user(db, principal.actor_id)
    if (
        user is None
        or user.is_active is not True
        or user.tenant_id != principal.tenant_id
    ):
        raise _principal_invalid("Invalid knowledge actor principal")


def _validate_source(source: dict[str, Any]) -> None:
    if source["status"] not in (0, 1):
        raise _resource_not_found()
    if source["type"] != KnowledgeType.General:
        raise _validation("Only general knowledge bases can be copied")
    if source["permission_id"] != PermissionType.Private:
        raise _validation("Only private knowledge bases can be copied")
    if source["builtin_metadata_enabled"] not in (0, 1):
        raise _validation("Source builtin metadata setting is invalid")


async def _resolve_copy_parent_id(
    db: AsyncSession,
    source: dict[str, Any],
    workspace_id: uuid.UUID,
) -> uuid.UUID:
    parent_id = source["parent_id"]
    if parent_id is None or parent_id == workspace_id:
        return workspace_id

    parent = await knowledge_repository.get_knowledge_by_id_in_workspace_async(
        db,
        parent_id,
        workspace_id,
    )
    if (
        parent is None
        or parent.type != KnowledgeType.FOLDER
        or parent.status != 1
    ):
        raise _validation("Source parent folder is invalid")
    return parent.id


def _validate_metadata_fields(
    metadata_fields: list[dict[str, Any]],
    tenant_id: uuid.UUID,
) -> None:
    for field in metadata_fields:
        if field["tenant_id"] != tenant_id:
            raise _validation("Source metadata field tenant is invalid")
        name = field["name"]
        if name in KnowledgeMetadataService.BUILTIN_FIELD_NAMES:
            raise _validation(f"Source metadata field conflicts with builtin field: {name}")
        try:
            KnowledgeMetadataCreate.model_validate(
                {"name": name, "type": field["type"]}
            )
        except ValidationError as exc:
            raise _validation(f"Source metadata field is invalid: {name}") from exc


def _is_model_visible(model: ModelConfig, tenant_id: uuid.UUID) -> bool:
    return model.tenant_id == tenant_id or model.is_public is True


async def _validate_model_references(
    db: AsyncSession,
    source: dict[str, Any],
    tenant_id: uuid.UUID,
) -> None:
    model_ids = list(
        dict.fromkeys(
            source[field_name]
            for field_name in _MODEL_REFERENCE_FIELDS
            if source[field_name] is not None
        )
    )
    models = await ReferenceRepository.get_model_configs(db, model_ids)
    models_by_id = {model.id: model for model in models}
    for field_name in _MODEL_REFERENCE_FIELDS:
        model_id = source[field_name]
        if model_id is None:
            continue
        model = models_by_id.get(model_id)
        if (
            model is None
            or model.is_active is not True
            or not _is_model_visible(model, tenant_id)
        ):
            raise _model_unavailable(field_name)


def _build_knowledge_values(
    source: dict[str, Any],
    principal: Principal,
    *,
    knowledge_id: uuid.UUID,
    name: str,
    parent_id: uuid.UUID,
    parser_config: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    return {
        "id": knowledge_id,
        "external_id": None,
        "workspace_id": principal.workspace_id,
        "created_by": principal.actor_id,
        "parent_id": parent_id,
        "name": name,
        "description": source["description"],
        "avatar": source["avatar"],
        "type": KnowledgeType.General.value,
        "permission_id": PermissionType.Private.value,
        "embedding_id": source["embedding_id"],
        "reranker_id": source["reranker_id"],
        "llm_id": source["llm_id"],
        "image2text_id": source["image2text_id"],
        "doc_num": 0,
        "chunk_num": 0,
        "parser_id": source["parser_id"],
        "parser_config": parser_config,
        "status": 1,
        "builtin_metadata_enabled": source["builtin_metadata_enabled"],
        "created_at": now,
        "updated_at": now,
    }


def _build_metadata_values(
    metadata_fields: list[dict[str, Any]],
    principal: Principal,
    *,
    knowledge_id: uuid.UUID,
    now: datetime,
) -> list[dict[str, Any]]:
    return [
        {
            "id": uuid.uuid4(),
            "tenant_id": principal.tenant_id,
            "knowledge_id": knowledge_id,
            "name": field["name"],
            "type": field["type"],
            "created_by": principal.actor_id,
            "updated_by": principal.actor_id,
            "created_at": now,
            "updated_at": now,
        }
        for field in metadata_fields
    ]


async def copy_knowledge_configuration(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
    principal: Principal,
) -> dict[str, Any]:
    """Copy one ordinary knowledge configuration in a single transaction."""
    try:
        await _validate_principal_references(db, principal)
        await knowledge_repository.lock_knowledge_copy_names_async(
            db,
            principal.workspace_id,
        )
        snapshot = await knowledge_repository.get_knowledge_copy_snapshot_async(
            db,
            knowledge_id,
            principal.workspace_id,
        )
        if snapshot is None:
            raise _resource_not_found()
        source, metadata_fields = snapshot

        _validate_source(source)
        parent_id = await _resolve_copy_parent_id(
            db,
            source,
            principal.workspace_id,
        )
        _validate_metadata_fields(metadata_fields, principal.tenant_id)
        await _validate_model_references(db, source, principal.tenant_id)
        try:
            parser_config = copy_parser_config(source["parser_config"])
        except ValueError as exc:
            raise _validation(str(exc)) from exc

        occupied_names = await knowledge_repository.get_knowledge_copy_names_async(
            db,
            principal.workspace_id,
            source["name"],
        )
        copy_name = choose_copy_name(source["name"], occupied_names)
        copied_id = uuid.uuid4()
        now = utcnow_naive()
        values = _build_knowledge_values(
            source,
            principal,
            knowledge_id=copied_id,
            name=copy_name,
            parent_id=parent_id,
            parser_config=parser_config,
            now=now,
        )
        metadata_values = _build_metadata_values(
            metadata_fields,
            principal,
            knowledge_id=copied_id,
            now=now,
        )

        copied: Knowledge = knowledge_repository.stage_knowledge_copy(db, values)
        KnowledgeMetadataRepository.stage_copy_fields(db, metadata_values)
        await db.flush()
        if source["parser_id"] is None:
            await db.refresh(copied, attribute_names=["parser_id"])
        response_data = await knowledge_service.knowledge_to_data(db, copied)
        SuccessEnvelope[dict[str, Any]](data=response_data).model_dump_json()
        await db.commit()
        return response_data
    except KnowledgeError:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise KnowledgeError.from_code(
            "KB_DATABASE_UNAVAILABLE",
            "Knowledge database operation failed",
        ) from exc
    except BaseException:
        await db.rollback()
        raise
