"""Visible v1 cards and bounded directory/ancestor reads."""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..api.query_validation import execute_list_page
from ..models.owned import Knowledge, KnowledgeShare, PermissionType

SOURCE_DISPLAY_FIELDS = {
    "name", "description", "avatar", "doc_num", "chunk_num", "source", "index_strategy",
    "embedding_id", "reranker_id", "llm_id",
}


def visible_knowledge_filters(workspace_id: UUID) -> list:
    return [
        Knowledge.workspace_id == workspace_id,
        Knowledge.permission_id.not_in((PermissionType.Share, PermissionType.Memory)),
        Knowledge.status != 2,
    ]


def folder_query(workspace_id: UUID):
    return select(Knowledge.id, Knowledge.name, Knowledge.parent_id).where(
        Knowledge.workspace_id == workspace_id,
        Knowledge.type == "Folder",
        Knowledge.permission_id.not_in((PermissionType.Share, PermissionType.Memory)),
        Knowledge.status != 2,
    )


def root_filter(workspace_id: UUID):
    visible_folders = folder_query(workspace_id).with_only_columns(Knowledge.id)
    return or_(
        Knowledge.parent_id.is_(None),
        Knowledge.parent_id == workspace_id,
        Knowledge.parent_id.not_in(visible_folders),
    )


async def get_folder(db: AsyncSession, workspace_id: UUID, folder_id: UUID):
    return (await db.execute(folder_query(workspace_id).where(Knowledge.id == folder_id))).first()


async def get_folder_children(db: AsyncSession, workspace_id: UUID, parent_id: UUID | None):
    stmt = folder_query(workspace_id).where(
        root_filter(workspace_id) if parent_id is None else Knowledge.parent_id == parent_id,
    ).order_by(Knowledge.created_at.desc(), Knowledge.id.desc())
    return (await db.execute(stmt)).all()


async def get_ancestor_folders(
    db: AsyncSession, workspace_id: UUID, parent_ids: Collection[UUID | None],
) -> dict:
    folders = {}
    visited = {None, workspace_id}
    pending = set(parent_ids) - visited
    while pending:
        visited.update(pending)
        rows = (
            await db.execute(folder_query(workspace_id).where(Knowledge.id.in_(pending)))
        ).all()
        folders.update((row.id, row) for row in rows)
        pending = {row.parent_id for row in rows} - visited
    return folders


def knowledge_query(workspace_id: UUID, *, is_shared: bool):
    if not is_shared:
        return select(Knowledge).where(*visible_knowledge_filters(workspace_id)), Knowledge
    source = aliased(Knowledge, name="source_knowledge")
    stmt = (
        select(Knowledge, source)
        .join(KnowledgeShare, KnowledgeShare.target_kb_id == Knowledge.id)
        .join(source, and_(
            source.id == KnowledgeShare.source_kb_id,
            source.workspace_id == KnowledgeShare.source_workspace_id,
        ))
        .where(
            Knowledge.workspace_id == workspace_id,
            KnowledgeShare.target_workspace_id == workspace_id,
            Knowledge.permission_id == PermissionType.Share,
            Knowledge.type != "Folder",
            Knowledge.status != 2,
            source.status != 2,
            source.type != "Folder",
            source.permission_id.not_in((PermissionType.Share, PermissionType.Memory)),
        )
    )
    return stmt, source


async def directory_counts(
    db: AsyncSession, workspace_id: UUID, folder_ids: Collection[UUID],
) -> tuple[dict, set]:
    if not folder_ids:
        return {}, set()
    counts = (
        await db.execute(
            select(Knowledge.parent_id, func.count(Knowledge.id))
            .where(
                *visible_knowledge_filters(workspace_id),
                Knowledge.type != "Folder",
                Knowledge.parent_id.in_(folder_ids),
            )
            .group_by(Knowledge.parent_id)
        )
    ).all()
    children = (
        await db.execute(
            folder_query(workspace_id).with_only_columns(Knowledge.parent_id)
            .where(Knowledge.parent_id.in_(folder_ids)).distinct()
        )
    ).scalars().all()
    return dict(counts), set(children)


async def get_shared_sources(
    db: AsyncSession, workspace_id: UUID, target_ids: Collection[UUID],
) -> dict[UUID, Knowledge | None]:
    if not target_ids:
        return {}
    rows = (
        await db.execute(
            select(KnowledgeShare.target_kb_id, Knowledge)
            .outerjoin(Knowledge, and_(
                Knowledge.id == KnowledgeShare.source_kb_id,
                Knowledge.workspace_id == KnowledgeShare.source_workspace_id,
            ))
            .where(
                KnowledgeShare.target_workspace_id == workspace_id,
                KnowledgeShare.target_kb_id.in_(target_ids),
            )
        )
    ).all()
    return dict(rows)


async def list_knowledges(
    db: AsyncSession,
    workspace_id: UUID,
    *,
    parent_id: UUID | None,
    kb_ids: list[UUID] | None,
    keywords: str | None,
    page: int,
    pagesize: int,
    orderby: str | None,
    desc: bool,
    is_shared: bool = False,
    status: int | None = None,
    exclude_types: Collection[str] = (),
) -> tuple[int, list[Knowledge], dict[UUID, Knowledge]]:
    stmt, source = knowledge_query(workspace_id, is_shared=is_shared)
    if status is not None:
        stmt = stmt.where(Knowledge.status == status)
    if "Folder" in exclude_types:
        stmt = stmt.where(Knowledge.type != "Folder")
    if "document" in exclude_types:
        stmt = stmt.where(Knowledge.type == "Folder")
    if not is_shared:
        if parent_id == workspace_id:
            stmt = stmt.where(root_filter(workspace_id))
        elif parent_id is not None:
            stmt = stmt.where(
                Knowledge.parent_id == parent_id,
                Knowledge.parent_id.in_(folder_query(workspace_id).with_only_columns(Knowledge.id)),
            )
    if kb_ids is not None:
        stmt = stmt.where(Knowledge.id.in_(kb_ids))
    if keywords:
        stmt = stmt.where(or_(
            source.name.ilike(f"%{keywords}%"), source.description.ilike(f"%{keywords}%"),
        ))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    orderby = orderby or "created_at"
    ordering_owner = source if orderby in SOURCE_DISPLAY_FIELDS else Knowledge
    result = await execute_list_page(
        db, stmt.offset((page - 1) * pagesize).limit(pagesize), ordering_owner,
        orderby=orderby, desc=desc, tie_breaker=Knowledge.id,
    )
    rows = result.all()
    owners = [row[0] for row in rows]
    sources = {row[0].id: row[1] for row in rows} if is_shared else {}
    return total, owners, sources
