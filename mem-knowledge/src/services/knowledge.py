"""Knowledge business behavior copied from the legacy async service."""

from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.knowledge import (
    KnowledgeCreate,
    KnowledgeUpdate,
    ModelConfigSummary,
    UserSummary,
)
from ..errors import KnowledgeError
from ..models.owned import Knowledge, KnowledgeType, PermissionType
from ..models.references import ModelBase, ModelConfig, User
from ..rag.knowledge_graph.config import is_graph_enabled
from ..rag.parser_config import normalize_knowledge_parser_config_update
from ..repositories import knowledge as knowledge_repository
from ..repositories.knowledge_share import get_knowledgeshare_by_id_async
from ..repositories.reference import ReferenceRepository
from ..utils.datetime_utils import utcnow_naive

logger = logging.getLogger(__name__)

_SHARE_MIRRORED_MODEL_FIELDS = (
    ("embedding_id", "embedding"),
    ("reranker_id", "reranker"),
    ("llm_id", "llm"),
    ("image2text_id", "image2text"),
)


@dataclass(frozen=True)
class KnowledgeSnapshot:
    id: uuid.UUID
    workspace_id: uuid.UUID
    parser_config: dict[str, Any]


def knowledge_snapshot(knowledge: Knowledge) -> KnowledgeSnapshot:
    return KnowledgeSnapshot(
        id=knowledge.id,
        workspace_id=knowledge.workspace_id,
        parser_config=deepcopy(knowledge.parser_config or {}),
    )


def _not_found(message: str = "Knowledge resource not found") -> KnowledgeError:
    return KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", message)


def _conflict(message: str) -> KnowledgeError:
    return KnowledgeError.from_code(
        "KB_CONFLICT",
        message,
        status_code=400,
        response_code=400,
        response_style="http",
    )


def _reference_not_found(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_REFERENCE_NOT_FOUND", message)


def _as_uuid(value: object) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def build_knowledge_list_filters(
    principal: Principal,
    *,
    parent_id: uuid.UUID | None,
    keywords: str | None,
    kb_ids: str | None,
) -> list:
    filters = [Knowledge.workspace_id == principal.workspace_id]
    if keywords:
        filters.append(
            or_(
                Knowledge.name.ilike(f"%{keywords}%"),
                Knowledge.description.ilike(f"%{keywords}%"),
            )
        )
    if kb_ids:
        filters.append(Knowledge.id.in_(kb_ids.split(",")))
    else:
        filters.append(Knowledge.status != 2)
        filters.append(
            Knowledge.parent_id
            == (parent_id if parent_id is not None else principal.workspace_id)
        )
    filters.append(Knowledge.permission_id != PermissionType.Memory)
    return filters


def _user_summary(user: User, current_workspace_name: str | None) -> UserSummary:
    return UserSummary(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        current_workspace_id=user.current_workspace_id,
        current_workspace_name=current_workspace_name,
        preferred_language=user.preferred_language,
        phone=user.phone,
    )


def _model_summary(
    model: ModelConfig,
    model_base: ModelBase | None,
) -> ModelConfigSummary:
    return ModelConfigSummary(
        id=model.id,
        name=model.name,
        type=model.type,
        logo=model.logo,
        description=model.description,
        provider=model.provider,
        config=model.config,
        is_active=model.is_active,
        is_public=model.is_public,
        load_balance_strategy=model.load_balance_strategy,
        capability=model.capability or [],
        is_omni=model.is_omni,
        model_id=model.model_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
        is_deprecated=bool(model_base and model_base.is_deprecated),
    )


async def knowledge_to_data(
    db: AsyncSession,
    knowledge: Knowledge,
) -> dict[str, Any]:
    user = await ReferenceRepository.get_user(db, knowledge.created_by)
    if user is None:
        raise _reference_not_found("Knowledge creator does not exist")
    current_workspace = None
    if user.current_workspace_id is not None:
        current_workspace = await ReferenceRepository.get_workspace(
            db,
            user.current_workspace_id,
        )

    model_ids = [
        model_id
        for model_id in (
            knowledge.embedding_id,
            knowledge.reranker_id,
            knowledge.llm_id,
            knowledge.image2text_id,
        )
        if model_id is not None
    ]
    models = await ReferenceRepository.get_model_configs(db, model_ids)
    models_by_id = {model.id: model for model in models}
    base_ids = [model.model_id for model in models if model.model_id is not None]
    model_bases = await ReferenceRepository.get_model_bases(db, base_ids)
    bases_by_id = {model_base.id: model_base for model_base in model_bases}

    data = {
        column.name: getattr(knowledge, column.name)
        for column in Knowledge.__table__.columns
    }
    data["created_user"] = _user_summary(
        user,
        current_workspace.name if current_workspace else None,
    )
    for id_field, model_field in _SHARE_MIRRORED_MODEL_FIELDS:
        model_id = data[id_field]
        model = models_by_id.get(model_id)
        data[model_field] = (
            _model_summary(model, bases_by_id.get(model.model_id)) if model else None
        )

    from ..api.schemas.knowledge import Knowledge as KnowledgeSchema

    return KnowledgeSchema.model_validate(data).model_dump(mode="json")


async def build_knowledge_detail_data(
    db: AsyncSession,
    knowledge: Knowledge,
) -> dict[str, Any]:
    data = await knowledge_to_data(db, knowledge)
    if knowledge.permission_id != PermissionType.Share:
        return data
    share = await get_knowledgeshare_by_id_async(db, knowledge.id)
    if share is None:
        return data
    source = await knowledge_repository.get_knowledge_by_id_async(db, share.source_kb_id)
    if source is None or source.status == 2:
        return data
    source_data = await knowledge_to_data(db, source)
    for id_field, model_field in _SHARE_MIRRORED_MODEL_FIELDS:
        data[id_field] = source_data[id_field]
        data[model_field] = source_data[model_field]
    return data


async def _build_folder_trees(
    db: AsyncSession,
    items: list[Knowledge],
    workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    folder_ids = [item.id for item in items if item.type == KnowledgeType.FOLDER]
    if not folder_ids:
        return [await knowledge_to_data(db, item) for item in items]

    children_by_parent: dict[uuid.UUID, list[Knowledge]] = {}
    pending_parent_ids = list(folder_ids)
    visited_parent_ids: set[uuid.UUID] = set()
    all_folder_ids = list(folder_ids)
    while pending_parent_ids:
        current_parent_ids = [
            parent_id
            for parent_id in pending_parent_ids
            if parent_id not in visited_parent_ids
        ]
        if not current_parent_ids:
            break
        visited_parent_ids.update(current_parent_ids)
        children = await knowledge_repository.get_knowledges_by_parent_ids_async(
            db,
            current_parent_ids,
            workspace_id,
        )
        pending_parent_ids = []
        for child in children:
            children_by_parent.setdefault(child.parent_id, []).append(child)
            if child.type == KnowledgeType.FOLDER:
                all_folder_ids.append(child.id)
                pending_parent_ids.append(child.id)

    knowledge_ids_by_folder: dict[uuid.UUID, set[uuid.UUID]] = {}

    def collect(folder_id: uuid.UUID, ancestors: set[uuid.UUID]) -> set[uuid.UUID]:
        if folder_id in knowledge_ids_by_folder:
            return knowledge_ids_by_folder[folder_id]
        if folder_id in ancestors:
            return set()
        knowledge_ids = {folder_id}
        next_ancestors = ancestors | {folder_id}
        for child in children_by_parent.get(folder_id, []):
            if child.id in next_ancestors:
                continue
            knowledge_ids.add(child.id)
            if child.type == KnowledgeType.FOLDER:
                knowledge_ids.update(collect(child.id, next_ancestors))
        knowledge_ids_by_folder[folder_id] = knowledge_ids
        return knowledge_ids

    for folder_id in dict.fromkeys(all_folder_ids):
        collect(folder_id, set())
    counted_ids = list(
        {
            knowledge_id
            for knowledge_ids in knowledge_ids_by_folder.values()
            for knowledge_id in knowledge_ids
        }
    )
    document_counts = await knowledge_repository.get_document_counts_by_knowledge_ids_async(
        db,
        counted_ids,
    )
    folder_counts = {
        folder_id: sum(document_counts.get(knowledge_id, 0) for knowledge_id in ids)
        for folder_id, ids in knowledge_ids_by_folder.items()
    }

    async def build_item(
        item: Knowledge,
        ancestors: set[uuid.UUID],
    ) -> dict[str, Any]:
        data = await knowledge_to_data(db, item)
        if item.id in folder_counts:
            data["doc_num"] = folder_counts[item.id]
        if item.type == KnowledgeType.FOLDER:
            next_ancestors = ancestors | {item.id}
            data["children"] = [
                await build_item(child, next_ancestors)
                for child in children_by_parent.get(item.id, [])
                if child.id not in next_ancestors
            ]
        return data

    return [await build_item(item, set()) for item in items]


async def list_knowledges(
    db: AsyncSession,
    principal: Principal,
    *,
    parent_id: uuid.UUID | None,
    page: int,
    pagesize: int,
    orderby: str | None,
    desc: bool,
    keywords: str | None,
    kb_ids: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    filters = build_knowledge_list_filters(
        principal,
        parent_id=parent_id,
        keywords=keywords,
        kb_ids=kb_ids,
    )
    total, items = await knowledge_repository.get_knowledges_paginated_async(
        db,
        filters,
        page,
        pagesize,
        orderby,
        desc,
    )
    return total, await _build_folder_trees(db, items, principal.workspace_id)


async def get_knowledge(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
    principal: Principal,
) -> Knowledge | None:
    return await knowledge_repository.get_knowledge_by_id_in_workspace_async(
        db,
        knowledge_id,
        principal.workspace_id,
    )


async def _prepare_knowledge_create(
    db: AsyncSession,
    create_data: KnowledgeCreate,
    principal: Principal,
    *,
    workspace_id: uuid.UUID,
    check_name: bool,
) -> KnowledgeCreate:
    knowledge = create_data.model_copy(deep=True)
    knowledge.workspace_id = workspace_id
    knowledge.created_by = principal.actor_id
    if knowledge.parent_id is None:
        knowledge.parent_id = workspace_id

    if check_name and await knowledge_repository.get_knowledge_by_name_async(
        db,
        knowledge.name,
        workspace_id,
    ):
        raise _conflict(f"The knowledge base name already exists: {knowledge.name}")
    if knowledge.external_id and await knowledge_repository.get_knowledge_by_external_id_async(
        db,
        knowledge.external_id,
        workspace_id,
    ):
        raise KnowledgeError.from_code(
            "KB_CONFLICT",
            f"external_id already exists in this workspace: {knowledge.external_id}",
            status_code=400,
            response_code=1001,
            response_style="business",
        )

    workspace = await ReferenceRepository.get_workspace(db, workspace_id)
    if workspace is None:
        raise _reference_not_found("Workspace does not exist")
    if knowledge.embedding_id is None:
        if not workspace.embedding:
            raise _reference_not_found("Workspace embedding model is not configured")
        knowledge.embedding_id = _as_uuid(workspace.embedding)
    if knowledge.reranker_id is None:
        if not workspace.rerank:
            raise _reference_not_found("Workspace rerank model is not configured")
        knowledge.reranker_id = _as_uuid(workspace.rerank)
    if knowledge.llm_id is None:
        if not workspace.llm:
            raise _reference_not_found("Workspace LLM model is not configured")
        knowledge.llm_id = _as_uuid(workspace.llm)
    if knowledge.image2text_id is None:
        model = await ReferenceRepository.get_latest_vision_model(db, workspace.tenant_id)
        if model is None:
            raise _reference_not_found("No vision model is available for the tenant")
        knowledge.image2text_id = model.id
    return knowledge


async def create_knowledge(
    db: AsyncSession,
    create_data: KnowledgeCreate,
    principal: Principal,
) -> Knowledge:
    requested_parent_id = create_data.parent_id
    if requested_parent_id and requested_parent_id != principal.workspace_id:
        parent = await get_knowledge(db, requested_parent_id, principal)
        if parent is None:
            raise _not_found("The parent knowledge base does not exist or access is denied")
    knowledge = await _prepare_knowledge_create(
        db,
        create_data,
        principal,
        workspace_id=principal.workspace_id,
        check_name=True,
    )
    return await knowledge_repository.create_knowledge_async(db, knowledge)


async def create_shared_knowledge(
    db: AsyncSession,
    create_data: KnowledgeCreate,
    principal: Principal,
    target_workspace_id: uuid.UUID,
) -> Knowledge:
    knowledge = await _prepare_knowledge_create(
        db,
        create_data,
        principal,
        workspace_id=target_workspace_id,
        check_name=False,
    )
    return await knowledge_repository.create_knowledge_async(
        db,
        knowledge,
        preserve_source_parser_config=True,
    )


async def update_knowledge(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
    update_data: KnowledgeUpdate,
    principal: Principal,
) -> KnowledgeMutationOutcome:
    plan = await prepare_knowledge_update(
        db,
        knowledge_id,
        update_data,
        principal,
    )
    return await apply_knowledge_update(db, plan, principal)


@dataclass(frozen=True)
class KnowledgeUpdatePlan:
    knowledge_id: uuid.UUID
    update_fields: dict[str, Any]
    embedding_changed: bool
    delete_vector_index: bool
    graph_enabled_before: bool | None


@dataclass(frozen=True)
class KnowledgeMutationOutcome:
    response_data: dict[str, Any] | None
    knowledge_id: uuid.UUID
    parser_config: dict[str, Any]
    invalidate_workspace_id: uuid.UUID | None


async def prepare_knowledge_update(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
    update_data: KnowledgeUpdate,
    principal: Principal,
) -> KnowledgeUpdatePlan:
    knowledge = await get_knowledge(db, knowledge_id, principal)
    if knowledge is None:
        raise _not_found()
    update_dict = update_data.model_dump(exclude_unset=True)
    if "parent_id" in update_dict:
        parent_id = update_dict["parent_id"]
        if parent_id is not None and parent_id != principal.workspace_id:
            parent = await get_knowledge(db, parent_id, principal)
            if parent is None:
                raise _not_found("The parent knowledge base does not exist or access is denied")
    if "name" in update_dict and update_dict["name"] != knowledge.name:
        if await knowledge_repository.get_knowledge_by_name_async(
            db,
            update_dict["name"],
            principal.workspace_id,
        ):
            raise _conflict(f"The knowledge base name already exists: {update_dict['name']}")
    graph_enabled_before = None
    if "parser_config" in update_dict:
        try:
            graph_enabled_before = is_graph_enabled(knowledge.parser_config)
            update_dict["parser_config"] = normalize_knowledge_parser_config_update(
                knowledge.parser_config,
                update_dict["parser_config"],
            )
        except ValueError as exc:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                str(exc),
            ) from exc
    embedding_changed = (
        "embedding_id" in update_dict and update_dict["embedding_id"] != knowledge.embedding_id
    )
    return KnowledgeUpdatePlan(
        knowledge_id=knowledge.id,
        update_fields=update_dict,
        embedding_changed=embedding_changed,
        delete_vector_index=bool(
            embedding_changed and knowledge.embedding_id and knowledge.reranker_id
        ),
        graph_enabled_before=graph_enabled_before,
    )


async def apply_knowledge_update(
    db: AsyncSession,
    plan: KnowledgeUpdatePlan,
    principal: Principal,
) -> KnowledgeMutationOutcome:
    knowledge = await get_knowledge(db, plan.knowledge_id, principal)
    if knowledge is None:
        raise _not_found()
    if plan.embedding_changed:
        from ..repositories.document import stage_reset_documents_progress_by_kb_id_async

        await stage_reset_documents_progress_by_kb_id_async(db, knowledge.id)
        knowledge.chunk_num = 0
    for field, value in plan.update_fields.items():
        if hasattr(knowledge, field):
            setattr(knowledge, field, value)
    knowledge.updated_at = utcnow_naive()
    await db.flush()
    await db.refresh(knowledge)
    outcome = await build_knowledge_mutation_outcome(db, knowledge)
    await db.commit()
    return outcome


async def build_knowledge_mutation_outcome(
    db: AsyncSession,
    knowledge: Knowledge,
    *,
    invalidate_workspace_id: uuid.UUID | None = None,
) -> KnowledgeMutationOutcome:
    workspace_id = (
        knowledge.workspace_id
        if knowledge.name == "USER_RAG_MERORY"
        else invalidate_workspace_id
    )
    return KnowledgeMutationOutcome(
        response_data=await knowledge_to_data(db, knowledge),
        knowledge_id=knowledge.id,
        parser_config=deepcopy(knowledge.parser_config or {}),
        invalidate_workspace_id=workspace_id,
    )


async def invalidate_storage_type_cache(
    redis_manager: Any,
    workspace_id: uuid.UUID,
) -> None:
    try:
        client = await redis_manager.client()
        pattern = f"cache:storage_type:{workspace_id}:*"
        async for key in client.scan_iter(match=pattern, count=500):
            await client.unlink(key)
    except Exception as exc:
        logger.warning(
            "Failed to invalidate storage type cache error_type=%s",
            type(exc).__name__,
        )


async def soft_delete_knowledge(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
    principal: Principal,
) -> KnowledgeMutationOutcome:
    knowledge = await get_knowledge(db, knowledge_id, principal)
    if knowledge is None:
        raise _not_found()
    knowledge.status = 2
    knowledge.updated_at = utcnow_naive()
    await db.flush()
    outcome = KnowledgeMutationOutcome(
        response_data=None,
        knowledge_id=knowledge.id,
        parser_config=deepcopy(knowledge.parser_config or {}),
        invalidate_workspace_id=(
            knowledge.workspace_id if knowledge.name == "USER_RAG_MERORY" else None
        ),
    )
    await db.commit()
    return outcome
