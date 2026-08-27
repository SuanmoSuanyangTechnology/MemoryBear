"""Knowledge sharing behavior copied from the legacy async service."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.knowledge import KnowledgeCreate
from ..api.schemas.knowledge_share import KnowledgeShareCreate, WorkspaceSummary
from ..errors import KnowledgeError
from ..models.owned import KnowledgeShare, PermissionType
from ..repositories import knowledge as knowledge_repository
from ..repositories import knowledge_share as share_repository
from ..repositories.reference import ReferenceRepository
from ..utils.datetime_utils import to_timestamp_ms
from . import knowledge as knowledge_service


def _not_found(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", message)


async def share_to_data(
    db: AsyncSession,
    share: KnowledgeShare,
) -> dict[str, Any]:
    target_knowledge = await knowledge_repository.get_knowledge_by_id_async(
        db,
        share.target_kb_id,
    )
    target_workspace = await ReferenceRepository.get_workspace(
        db,
        share.target_workspace_id,
    )
    shared_user = await ReferenceRepository.get_user(db, share.shared_by)
    if target_knowledge is None or target_workspace is None or shared_user is None:
        raise KnowledgeError.from_code(
            "KB_REFERENCE_NOT_FOUND",
            "Knowledge share reference is incomplete",
        )
    target_data = await knowledge_service.knowledge_to_data(db, target_knowledge)
    user_data = knowledge_service._user_summary(shared_user, None).model_dump(mode="json")
    workspace_data = WorkspaceSummary.model_validate(target_workspace).model_dump(mode="json")
    return {
        "id": str(share.id),
        "source_kb_id": str(share.source_kb_id),
        "source_workspace_id": str(share.source_workspace_id),
        "target_kb_id": str(share.target_kb_id),
        "target_workspace_id": str(share.target_workspace_id),
        "shared_by": str(share.shared_by),
        "created_at": to_timestamp_ms(share.created_at),
        "updated_at": to_timestamp_ms(share.updated_at),
        "target_kb": target_data,
        "target_workspace": workspace_data,
        "shared_user": user_data,
    }


async def list_shares(
    db: AsyncSession,
    kb_id: uuid.UUID,
    principal: Principal,
    *,
    page: int,
    pagesize: int,
    orderby: str | None,
    desc: bool,
) -> tuple[int, list[dict[str, Any]]]:
    source = await knowledge_service.get_knowledge(db, kb_id, principal)
    if source is None:
        raise _not_found("Source knowledge does not exist")
    filters = [
        KnowledgeShare.source_workspace_id == principal.workspace_id,
        KnowledgeShare.source_kb_id == kb_id,
    ]
    total, shares = await share_repository.get_knowledgeshares_paginated_async(
        db,
        filters,
        page,
        pagesize,
        orderby,
        desc,
    )
    return total, [await share_to_data(db, share) for share in shares]


async def create_share(
    db: AsyncSession,
    create_data: KnowledgeShareCreate,
    principal: Principal,
) -> dict[str, Any]:
    target_workspace = await ReferenceRepository.get_workspace(
        db,
        create_data.target_workspace_id,
    )
    if target_workspace is None:
        raise _not_found("Target workspace does not exist")
    source = await knowledge_service.get_knowledge(db, create_data.source_kb_id, principal)
    if source is None:
        raise _not_found("Source knowledge does not exist")
    mirrored = KnowledgeCreate(
        workspace_id=create_data.target_workspace_id,
        created_by=principal.actor_id,
        parent_id=create_data.target_workspace_id,
        name=source.name,
        description=source.description,
        avatar=source.avatar,
        type=source.type,
        permission_id=PermissionType.Share,
        embedding_id=source.embedding_id,
        reranker_id=source.reranker_id,
        llm_id=source.llm_id,
        image2text_id=source.image2text_id,
        doc_num=source.doc_num,
        chunk_num=source.chunk_num,
        parser_id=source.parser_id,
        parser_config=source.parser_config,
    )
    target = await knowledge_service.create_shared_knowledge(
        db,
        mirrored,
        principal,
        create_data.target_workspace_id,
    )
    share_create = create_data.model_copy(deep=True)
    share_create.source_workspace_id = principal.workspace_id
    share_create.shared_by = principal.actor_id
    share_create.target_kb_id = target.id
    share = await share_repository.create_knowledgeshare_async(db, share_create)
    return await share_to_data(db, share)


async def get_share(
    db: AsyncSession,
    share_id: uuid.UUID,
    principal: Principal,
) -> KnowledgeShare | None:
    return await share_repository.get_knowledgeshare_by_id_in_source_workspace_async(
        db,
        share_id,
        principal.workspace_id,
    )


async def delete_share(
    db: AsyncSession,
    share_id: uuid.UUID,
    principal: Principal,
) -> None:
    share = await get_share(db, share_id, principal)
    if share is None:
        raise _not_found("Knowledge share does not exist")
    await knowledge_repository.delete_knowledge_by_id_async(db, share.target_kb_id)
    await share_repository.delete_knowledgeshare_by_id_in_source_workspace_async(
        db,
        share_id,
        principal.workspace_id,
    )
