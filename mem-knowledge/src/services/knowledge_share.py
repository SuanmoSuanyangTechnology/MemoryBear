"""Knowledge sharing behavior copied from the legacy async service."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.knowledge import KnowledgeCreate
from ..api.schemas.knowledge_share import (
    KnowledgeShareCreate,
    KnowledgeShareRequest,
    KnowledgeShareResult,
    WorkspaceSummary,
)
from ..errors import KnowledgeError
from ..models.owned import KnowledgeShare, KnowledgeType, PermissionType
from ..repositories import knowledge as knowledge_repository
from ..repositories import knowledge_share as share_repository
from ..repositories.reference import ReferenceRepository
from ..utils.datetime_utils import to_timestamp_ms
from . import knowledge as knowledge_service


def _not_found(code: str) -> KnowledgeError:
    return KnowledgeError.from_code(code)


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
            "KB_KNOWLEDGE_SHARE_REFERENCE_INCOMPLETE",
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
        raise _not_found("KB_SOURCE_KNOWLEDGE_NOT_FOUND")
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
        raise _not_found("KB_TARGET_WORKSPACE_NOT_FOUND")
    source = await knowledge_service.get_knowledge(db, create_data.source_kb_id, principal)
    if source is None:
        raise _not_found("KB_SOURCE_KNOWLEDGE_NOT_FOUND")
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
        audio2text_id=source.audio2text_id,
        video2text_id=source.video2text_id,
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
    try:
        share = await get_share(db, share_id, principal)
        if share is None:
            raise _not_found("KB_KNOWLEDGE_SHARE_NOT_FOUND")

        target_kb_id = share.target_kb_id
        await knowledge_repository.lock_share_source_async(db, share.source_kb_id)
        deleted = await share_repository.delete_knowledgeshare_by_id_in_source_workspace_async(
            db,
            share.id,
            principal.workspace_id,
            commit=False,
        )
        if not deleted:
            raise _not_found("KB_KNOWLEDGE_SHARE_NOT_FOUND")

        # The target deletion commits both deletes together.
        await knowledge_repository.delete_knowledge_by_id_async(db, target_kb_id)
    except Exception:
        await db.rollback()
        raise


async def create_or_reuse_share(
    db: AsyncSession,
    create_data: KnowledgeShareRequest,
    principal: Principal,
) -> dict[str, Any]:
    """Create or reuse a reference while holding the source lock through commit."""
    try:
        source = await knowledge_repository.lock_share_source_async(db, create_data.source_kb_id)
        if source is None:
            raise KnowledgeError.from_code("KB_SHARE_SOURCE_NOT_FOUND")
        if source.workspace_id != principal.workspace_id:
            raise KnowledgeError.from_code("KB_SHARE_SOURCE_OUTSIDE_WORKSPACE")
        source_workspace = await ReferenceRepository.get_workspace(db, source.workspace_id)
        target_workspace = await ReferenceRepository.get_workspace(
            db,
            create_data.target_workspace_id,
        )
        if source_workspace is None:
            raise KnowledgeError.from_code("KB_SHARE_SOURCE_UNAVAILABLE")
        if target_workspace is None:
            raise KnowledgeError.from_code("KB_SHARE_TARGET_NOT_FOUND")
        if (
            source_workspace.tenant_id != principal.tenant_id
            or target_workspace.tenant_id != principal.tenant_id
        ):
            raise KnowledgeError.from_code("KB_SHARE_CROSS_TENANT")
        if not source_workspace.is_active or source.status != 1:
            raise KnowledgeError.from_code("KB_SHARE_SOURCE_UNAVAILABLE")
        if not target_workspace.is_active:
            raise KnowledgeError.from_code("KB_SHARE_TARGET_UNAVAILABLE")
        if source.workspace_id == target_workspace.id:
            raise KnowledgeError.from_code("KB_SHARE_SAME_WORKSPACE")
        if source.permission_id != PermissionType.Private or source.type not in (
            KnowledgeType.General,
            KnowledgeType.Web,
            KnowledgeType.ThirdParty,
        ):
            raise KnowledgeError.from_code("KB_SHARE_SOURCE_NOT_ALLOWED")

        shares = await share_repository.get_shares_for_target_async(
            db,
            source.id,
            target_workspace.id,
        )
        active = []
        for share in shares:
            target = await knowledge_repository.get_knowledge_by_id_async(db, share.target_kb_id)
            if (
                share.source_workspace_id == source.workspace_id
                and target is not None
                and target.workspace_id == target_workspace.id
                and target.permission_id == PermissionType.Share
                and target.status == 1
            ):
                active.append(share)
        if len(active) > 1:
            raise KnowledgeError.from_code("KB_SHARE_DUPLICATE")
        if shares and not active:
            raise KnowledgeError.from_code("KB_SHARE_REFERENCE_UNAVAILABLE")
        created = not active
        if active:
            share = active[0]
        else:
            target = await knowledge_repository.create_knowledge_async(
                db,
                KnowledgeCreate(
                    workspace_id=target_workspace.id,
                    parent_id=target_workspace.id,
                    created_by=principal.actor_id,
                    name=source.name,
                    description=source.description,
                    avatar=source.avatar,
                    type=source.type,
                    permission_id=PermissionType.Share,
                    embedding_id=source.embedding_id,
                    reranker_id=source.reranker_id,
                    llm_id=source.llm_id,
                    image2text_id=source.image2text_id,
                    audio2text_id=source.audio2text_id,
                    video2text_id=source.video2text_id,
                    doc_num=source.doc_num,
                    chunk_num=source.chunk_num,
                    parser_id=source.parser_id,
                    parser_config=source.parser_config,
                ),
                preserve_source_parser_config=True,
                commit=False,
            )
            target.builtin_metadata_enabled = source.builtin_metadata_enabled
            share = await share_repository.create_knowledgeshare_async(
                db,
                KnowledgeShareCreate(
                    source_kb_id=source.id,
                    source_workspace_id=source.workspace_id,
                    target_kb_id=target.id,
                    target_workspace_id=target_workspace.id,
                    shared_by=principal.actor_id,
                ),
                commit=False,
            )
        result = KnowledgeShareResult.model_validate(share)
        result.created = created
        data = result.model_dump(mode="json")
        await db.commit()
        return data
    except Exception:
        await db.rollback()
        raise
