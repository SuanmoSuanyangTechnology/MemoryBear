"""Versioned management over the shared knowledge rules and configuration rows."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.query_validation import parse_uuid_filter
from ..api.schemas.knowledge import KnowledgeCreate, KnowledgeUpdate
from ..application.runtime import ProcessRuntime
from ..errors import KnowledgeError
from ..errors.model_errors import MODEL_FIELD_ROLES, ModelFailure, model_error
from ..models.owned import Knowledge, PermissionType
from ..models.references import Workspace
from ..parser_configuration import to_api
from ..parser_configuration.api import create_configuration, patch_configuration
from ..parser_configuration.legacy import immutable_field
from ..repositories import knowledge as repository
from ..repositories import knowledge_v1 as directory_repository
from ..repositories.parser_configuration import knowledge_configuration
from ..repositories.reference import ReferenceRepository
from ..utils.datetime_utils import to_timestamp_ms
from . import knowledge as shared
from .folder_hierarchy import validate_knowledge_hierarchy
from .knowledge_model_validation import (
    validate_requested_knowledge_model_availability,
    validate_requested_knowledge_models,
)

MODEL_FIELDS = ("embedding", "reranker", "llm")
MULTIMODAL_MODEL_FIELDS = ("image2text", "audio2text", "video2text")
SUMMARY_FIELDS = (
    "id",
    "workspace_id",
    "created_by",
    "parent_id",
    "name",
    "description",
    "avatar",
    "permission_id",
    "embedding_id",
    "reranker_id",
    "llm_id",
    "doc_num",
    "chunk_num",
    "parser_id",
    "external_id",
    "status",
)


async def get_knowledge(
    db: AsyncSession,
    knowledge_id: UUID,
    principal: Principal,
    *,
    for_update: bool = False,
) -> Knowledge:
    owner = await repository.get_knowledge_by_id_in_workspace_async(
        db, knowledge_id, principal.workspace_id, for_update=for_update,
    )
    if owner is None or owner.status == 2:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_NOT_FOUND", status_code=404)
    return owner


async def project_knowledges(
    db: AsyncSession,
    owners: list[Knowledge],
    *,
    shared_sources: dict[UUID, Knowledge | None] | None = None,
) -> list[dict[str, Any]]:
    if not owners:
        return []
    workspace_id = cast(UUID, owners[0].workspace_id)
    if shared_sources is None:
        shared_sources = await directory_repository.get_shared_sources(
            db, workspace_id,
            [cast(UUID, owner.id) for owner in owners
             if owner.permission_id == PermissionType.Share],
        )
    effective = {owner.id: owner for owner in owners}
    for owner in owners:
        if owner.permission_id != PermissionType.Share:
            continue
        if owner.id not in shared_sources:
            raise KnowledgeError.from_code("KB_KNOWLEDGE_NOT_FOUND", status_code=404)
        source = shared_sources[owner.id]
        if source is None or source.status == 2:
            raise KnowledgeError.from_code("KB_SHARE_SOURCE_UNAVAILABLE")
        effective[owner.id] = source
    users = await ReferenceRepository.get_users(
        db,
        list({cast(UUID, owner.created_by) for owner in owners}),
    )
    users_by_id = {user.id: user for user in users}
    workspace_ids = {user.current_workspace_id for user in users if user.current_workspace_id}
    workspace_ids.update(
        source.workspace_id for source in shared_sources.values() if source is not None
    )
    workspaces = (
        (await db.execute(select(Workspace).where(Workspace.id.in_(workspace_ids)))).scalars().all()
    )
    workspace_names = {workspace.id: workspace.name for workspace in workspaces}
    model_ids = {
        getattr(owner, f"{field}_id")
        for owner in effective.values()
        for field in MODEL_FIELDS + MULTIMODAL_MODEL_FIELDS
    }
    models = await ReferenceRepository.get_model_configs(db, list(model_ids - {None}))
    bases = await ReferenceRepository.get_model_bases(
        db,
        list({cast(UUID, model.model_id) for model in models if model.model_id}),
    )
    bases_by_id = {base.id: base for base in bases}
    summaries = {
        model.id: shared._model_summary(model, bases_by_id.get(model.model_id)).model_dump(
            mode="json"
        )
        for model in models
    }
    parent_ids = {
        cast(UUID | None, owner.parent_id)
        for owner in owners if owner.permission_id != PermissionType.Share
    }
    folders = await directory_repository.get_ancestor_folders(db, workspace_id, parent_ids)
    paths = {
        parent_id: folder_path(parent_id, folders)
        for parent_id in parent_ids
    }
    result = []
    for owner in owners:
        user = users_by_id.get(owner.created_by)
        if user is None:
            raise KnowledgeError.from_code("KB_KNOWLEDGE_CREATOR_NOT_FOUND")
        source = effective[owner.id]
        is_shared = owner.permission_id == PermissionType.Share
        config = knowledge_configuration(source)
        item = {field: getattr(owner, field) for field in SUMMARY_FIELDS}
        if is_shared:
            for field in ("name", "description", "avatar", "doc_num", "chunk_num"):
                item[field] = getattr(source, field)
        item.update(
            {
                "type": "Folder" if owner.is_folder else "document",
                "source": config.source,
                "index_strategy": config.index_strategy,
                "processing_configuration": to_api(config),
                "folder_path": [] if is_shared else paths[cast(UUID | None, owner.parent_id)],
                "is_shared": is_shared,
                "source_status": source.status if is_shared else None,
                "source_workspace": {
                    "id": source.workspace_id,
                    "name": workspace_names.get(source.workspace_id),
                } if is_shared else None,
                "created_at": to_timestamp_ms(cast(datetime, owner.created_at)),
                "updated_at": to_timestamp_ms(cast(datetime, owner.updated_at)),
                "created_user": shared._user_summary(
                    user,
                    cast(str | None, workspace_names.get(user.current_workspace_id)),
                ).model_dump(mode="json"),
            }
        )
        for field in MODEL_FIELDS:
            model_id = getattr(source, f"{field}_id")
            item[f"{field}_id"] = model_id
            item[field] = summaries.get(model_id)
        for field in MULTIMODAL_MODEL_FIELDS:
            item[field] = summaries.get(getattr(source, f"{field}_id"))
        result.append(item)
    return result


def folder_path(parent_id: UUID | None, folders: dict) -> list[dict[str, Any]]:
    path = []
    seen = set()
    while parent_id in folders:
        if parent_id in seen:
            return []
        seen.add(parent_id)
        folder = folders[parent_id]
        path.append({"id": folder.id, "name": folder.name})
        parent_id = folder.parent_id
    return list(reversed(path))


async def folder_tree(
    db: AsyncSession, principal: Principal, parent_id: UUID | None = None,
) -> dict[str, Any]:
    if parent_id is not None and await directory_repository.get_folder(
        db, principal.workspace_id, parent_id,
    ) is None:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_NOT_FOUND", status_code=404)
    folders = await directory_repository.get_folder_children(db, principal.workspace_id, parent_id)
    counts, children = await directory_repository.directory_counts(
        db, principal.workspace_id, [folder.id for folder in folders],
    )
    return {
        "folders": [
            {
                "id": folder.id,
                "name": folder.name,
                "parent_id": parent_id,
                "knowledge_count": counts.get(folder.id, 0),
                "has_children": folder.id in children,
            }
            for folder in folders
        ],
    }


async def list_knowledges(db: AsyncSession, principal: Principal, **query) -> dict[str, Any]:
    if query.get("is_shared") and query.get("parent_id") not in (None, principal.workspace_id):
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR", status_code=422,
            diagnostic="query.parent_id: shared knowledge has no folder hierarchy",
        )
    try:
        query["kb_ids"] = parse_uuid_filter(query["kb_ids"], field="kb_ids")
        exclude_types = query.get("exclude_types")
        query["exclude_types"] = (
            {item.strip() for item in exclude_types.split(",")} if exclude_types else set()
        )
        if query["exclude_types"] - {"Folder", "document"}:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                diagnostic="query.exclude_types: expected comma-separated Folder or document",
            )
        total, owners, sources = await directory_repository.list_knowledges(
            db, principal.workspace_id, **query
        )
    except KnowledgeError as exc:
        if exc.code != "KB_VALIDATION_ERROR":
            raise
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR",
            status_code=422,
            diagnostic=exc.diagnostic,
        ) from None
    items = await project_knowledges(db, owners, shared_sources=sources)
    return {
        "items": items,
        "page": {
            "page": query["page"],
            "pagesize": query["pagesize"],
            "total": total,
            "hasnext": query["page"] * query["pagesize"] < total,
        },
    }


async def create_knowledge(
    db: AsyncSession,
    values: dict[str, Any],
    principal: Principal,
    runtime: ProcessRuntime,
) -> Knowledge:
    config = create_configuration(values)
    owner_values = {
        key: value
        for key, value in values.items()
        if key not in {"processing_configuration", "type", "source", "index_strategy"}
    }
    owner_values.update(
        {
            "type": config.knowledge_type,
            "image2text_id": config.image2text_id,
            "audio2text_id": config.audio2text_id,
            "video2text_id": config.video2text_id,
        }
    )
    await db.run_sync(
        validate_knowledge_hierarchy,
        principal.workspace_id,
        owner_values,
        runtime.settings.kb_knowledge_folder_max_depth,
    )
    prepared = await shared._prepare_knowledge_create(
        db,
        KnowledgeCreate.model_validate(owner_values),
        principal,
        workspace_id=principal.workspace_id,
        check_name=True,
        configuration=config,
    )
    if config.knowledge_type != "Folder":
        models = {
            field: getattr(prepared, field)
            for field in (
                "embedding_id",
                "reranker_id",
                "llm_id",
                "image2text_id",
                "audio2text_id",
                "video2text_id",
            )
        }
        await validate_requested_knowledge_models(db, models, principal.tenant_id)
        await validate_requested_knowledge_model_availability(
            models, principal, runtime.model_runtime
        )
    return await repository.create_knowledge_async(db, prepared, configuration=config)


async def update_status(
    db: AsyncSession, knowledge_id: UUID, status: int, principal: Principal,
) -> dict[str, Any]:
    owner = await get_knowledge(db, knowledge_id, principal, for_update=True)
    if owner.is_folder:
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR", status_code=422, diagnostic="Folder: unsupported status update",
        )
    if owner.permission_id == PermissionType.Memory:
        raise KnowledgeError.from_code("KB_HTTP_FORBIDDEN")

    data = (await project_knowledges(db, [owner]))[0]
    if owner.status != status:
        owner.status = status
        await db.commit()
        data["status"] = status
    return data


async def prepare_update(
    db: AsyncSession,
    knowledge_id: UUID,
    values: dict[str, Any],
    principal: Principal,
    runtime: ProcessRuntime,
) -> shared.KnowledgeUpdatePlan:
    owner = await get_knowledge(db, knowledge_id, principal)
    if (
        "permission_id" in values
        and values["permission_id"] != owner.permission_id
        and PermissionType.Share in {owner.permission_id, values["permission_id"]}
    ):
        raise immutable_field("permission_id")
    config = knowledge_configuration(owner)
    for field, current in (
        ("type", "Folder" if owner.is_folder else "document"),
        ("source", config.source),
        ("index_strategy", config.index_strategy),
    ):
        if field in values and values[field] != current:
            raise immutable_field(field)
    if owner.is_folder and values.keys() - {"name", "type", "parent_id", "description", "avatar"}:
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR",
            status_code=422,
            diagnostic="Folder: unsupported field",
        )
    nested = values.get("processing_configuration", {})
    next_config = patch_configuration(config, nested)
    updates = {
        key: value
        for key, value in values.items()
        if key not in {"type", "source", "index_strategy", "processing_configuration"}
    }
    if "parent_id" in updates and updates["parent_id"] is None:
        updates["parent_id"] = principal.workspace_id
    models = {}
    if not owner.is_folder:
        workspace = await ReferenceRepository.get_workspace(db, principal.workspace_id)
        for field, workspace_field, missing in (
            ("embedding_id", "embedding", "KB_WORKSPACE_EMBEDDING_MODEL_NOT_CONFIGURED"),
            ("reranker_id", "rerank", "KB_WORKSPACE_RERANK_MODEL_NOT_CONFIGURED"),
            ("llm_id", "llm", "KB_WORKSPACE_LLM_MODEL_NOT_CONFIGURED"),
        ):
            model_id = updates.get(field, getattr(owner, field))
            if model_id is None:
                if field not in updates:
                    raise model_error(MODEL_FIELD_ROLES[field], ModelFailure.CONFIG_INVALID)
                default = getattr(workspace, workspace_field, None)
                if not default:
                    raise KnowledgeError.from_code(missing)
                model_id = UUID(str(default))
                updates[field] = model_id
            models[field] = model_id
        for field in ("image2text_id", "audio2text_id", "video2text_id"):
            models[field] = getattr(next_config, field)
        await validate_requested_knowledge_models(db, models, principal.tenant_id)
        await validate_requested_knowledge_model_availability(
            models, principal, runtime.model_runtime
        )
    return await shared.prepare_knowledge_update(
        db,
        knowledge_id,
        KnowledgeUpdate.model_validate(updates),
        principal,
        max_folder_depth=runtime.settings.kb_knowledge_folder_max_depth,
        processing_configuration=nested,
    )
