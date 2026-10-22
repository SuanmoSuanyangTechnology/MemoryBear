"""Consistent, scoped Wiki projections without exposing execution records."""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import and_, exists, func, or_, select

from ...models.owned import (
    Document,
    Knowledge,
    WikiDocumentState,
    WikiFolder,
    WikiPage,
    WikiPageLink,
    WikiPageSource,
)
from ...models.references import Workspace
from ...rag.wiki.config import WikiError
from ...rag.wiki.pipeline import readable_markdown, reduce_input
from ...utils.datetime_utils import to_timestamp_ms
from .common import as_uuid

PAGE_COLUMNS = (
    WikiPage.id,
    WikiPage.title,
    WikiPage.summary,
    WikiPage.page_type,
    WikiPage.folder_id,
    WikiPage.version,
    WikiPage.updated_at,
)


def scoped_pages(scope, page_types=()):
    source = WikiPageSource
    invalid = exists(
        select(source.id)
        .join(Document, Document.id == source.document_id)
        .join(WikiDocumentState, WikiDocumentState.document_id == source.document_id)
        .where(
            source.page_id == WikiPage.id,
            or_(
                Document.status != 1,
                Document.kb_id != WikiPage.kb_id,
                WikiDocumentState.source_ready.is_(False),
                WikiDocumentState.requested_version != source.document_wiki_version,
                WikiDocumentState.mapped_version != source.document_wiki_version,
            ),
        )
    )
    conditions = [getattr(WikiPage, key) == as_uuid(value) for key, value in scope.items()]
    conditions += [
        WikiPage.status == "ready",
        ~invalid,
        exists(
            select(source.id).where(source.page_id == WikiPage.id, source.kb_id == WikiPage.kb_id)
        ),
        func.jsonb_array_length(WikiPage.published_sources)
        == select(func.count())
        .select_from(source)
        .where(source.page_id == WikiPage.id, source.kb_id == WikiPage.kb_id)
        .scalar_subquery(),
    ]
    if page_types:
        conditions.append(WikiPage.page_type.in_(page_types))
    return and_(*conditions)


def begin_read(session, scope):
    # Both graph endpoints and page/source reads use one consistent short snapshot.
    session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    knowledge = session.scalar(
        select(Knowledge)
        .join(Workspace, Workspace.id == Knowledge.workspace_id)
        .where(
            Knowledge.id == as_uuid(scope["kb_id"]),
            Knowledge.workspace_id == as_uuid(scope["workspace_id"]),
            Workspace.tenant_id == as_uuid(scope["tenant_id"]),
            Knowledge.status != 2,
        )
    )
    if knowledge is None:
        raise WikiError("WIKI_NOT_FOUND", retryable=False)


def _folders(session, scope):
    return {
        folder.id: folder
        for folder in session.scalars(
            select(WikiFolder).where(
                *(getattr(WikiFolder, key) == as_uuid(value) for key, value in scope.items())
            )
        )
    }


def folder_path(folder_id, folders):
    result, seen = [], set()
    while folder_id in folders and folder_id not in seen:
        seen.add(folder_id)
        folder = folders[folder_id]
        result.append({"id": str(folder.id), "name": folder.name})
        folder_id = folder.parent_id
    return result[::-1]


def page_data(row, folders):
    return {
        "id": str(row.id),
        "page_id": str(row.id),
        "title": row.title,
        "summary": row.summary,
        "type": row.page_type,
        "version": row.version,
        "folder_id": str(row.folder_id) if row.folder_id else None,
        "folder_path": folder_path(row.folder_id, folders),
        "updated_at_ms": to_timestamp_ms(row.updated_at),
    }


def list_pages(
    session, scope, *, page_types=(), keyword=None, page=1, pagesize=50, legacy=False
):
    begin_read(session, scope)
    condition = scoped_pages(scope, page_types)
    keyword = (keyword or "").strip()
    if keyword:
        condition = and_(condition, or_(
            WikiPage.title.icontains(keyword, autoescape=True),
            WikiPage.summary.icontains(keyword, autoescape=True),
            WikiPage.content.icontains(keyword, autoescape=True),
        ))
    total = session.scalar(select(func.count()).select_from(WikiPage).where(condition))
    rows = session.execute(
        select(*PAGE_COLUMNS)
        .where(condition)
        .order_by(WikiPage.id)
        .offset((page - 1) * pagesize)
        .limit(pagesize)
    ).all()
    folders = _folders(session, scope)
    pagination = {
        "page": page,
        "pagesize": pagesize,
        "total": total,
        "hasnext": page * pagesize < total,
    }
    items = [page_data(row, folders) for row in rows]
    if legacy:
        return {"items": items, **pagination, "limit": pagesize}
    return {"items": items, "page": pagination}


def detail(session, scope, page_id):
    begin_read(session, scope)
    conditions = [getattr(WikiPage, key) == as_uuid(value) for key, value in scope.items()]
    page = session.scalar(select(WikiPage).where(*conditions, WikiPage.id == as_uuid(page_id)))
    if page is None:
        raise WikiError("WIKI_NOT_FOUND", retryable=False)
    visible = session.scalar(select(WikiPage.id).where(WikiPage.id == page.id, scoped_pages(scope)))
    result = page_data(page, _folders(session, scope))
    result.update(
        {
            "status": page.status if page.status != "ready" or visible else "updating",
            "content": None,
            "summary": "",
            "sources": [],
            "outlinks": [],
            "reason": page.error_code,
        }
    )
    if not visible:
        blocked = session.execute(
            select(WikiDocumentState.status, WikiDocumentState.error_code)
            .join(WikiPageSource, WikiPageSource.document_id == WikiDocumentState.document_id)
            .where(
                WikiPageSource.page_id == page.id,
                WikiPageSource.kb_id == as_uuid(scope["kb_id"]),
                WikiDocumentState.status.in_(["failed", "cancelled"]),
                or_(
                    WikiDocumentState.source_ready.is_(False),
                    WikiDocumentState.mapped_version != WikiDocumentState.requested_version,
                    WikiPageSource.document_wiki_version != WikiDocumentState.requested_version,
                ),
            )
            .limit(1)
        ).first()
        if blocked:
            result["status"] = "failed"
            result["reason"] = blocked.error_code or "WIKI_SOURCE_FAILED"
        elif result["reason"] is None:
            result["reason"] = (
                "WIKI_UPDATING" if page.status != "unavailable" else "WIKI_SOURCE_UNAVAILABLE"
            )
        return result
    sources = []
    for source, document in session.execute(
        select(WikiPageSource, Document)
        .join(Document, Document.id == WikiPageSource.document_id)
        .where(WikiPageSource.page_id == page.id, WikiPageSource.kb_id == as_uuid(scope["kb_id"]))
        .order_by(WikiPageSource.document_id)
    ):
        sources.append(
            {
                "document_id": str(document.id),
                "title": document.file_name,
                "version": source.document_wiki_version,
                "facts": source.facts,
                "chunk_refs": source.chunk_refs,
                "source_context": source.source_context,
            }
        )
    catalog, _, _ = reduce_input({"sources": sources})
    valid_ids = select(WikiPage.id).where(scoped_pages(scope))
    outlinks = session.execute(
        select(WikiPage.id, WikiPage.title)
        .join(WikiPageLink, WikiPageLink.target_page_id == WikiPage.id)
        .where(WikiPageLink.source_page_id == page.id, WikiPage.id.in_(valid_ids))
    ).all()
    result.update(
        {
            "content": readable_markdown(page.content, [item["ref"] for item in catalog]),
            "summary": page.summary,
            "sources": catalog,
            "outlinks": [{"page_id": str(row.id), "title": row.title} for row in outlinks],
        }
    )
    return result


def _tree_page_node(row, folder_id):
    return {
        "id": str(row.id),
        "node_kind": "page",
        "parent_id": folder_id,
        "title": row.title,
        "page_type": row.page_type,
        "version": row.version,
    }


def _next_tree_cursor(items, has_next):
    if not items or not has_next:
        return None
    last = items[-1]
    return ("0:" if last["node_kind"] == "folder" else "1:") + last["id"]


def _tree_cursor_page(session, items, page_query, folder_id, cursor, pagesize):
    """仅保留旧调用的游标语义，不作为新接口的主要分页方式。"""
    items = [item for item in items if "0:" + item["id"] > cursor]
    if cursor.startswith("1:"):
        page_query = page_query.where(WikiPage.id > as_uuid(cursor[2:]))
    items.extend(
        _tree_page_node(row, folder_id)
        for row in session.execute(page_query.order_by(WikiPage.id).limit(pagesize + 1))
    )
    has_next = len(items) > pagesize
    items = items[:pagesize]
    return {
        "parent_id": folder_id,
        "items": items,
        "next_cursor": _next_tree_cursor(items, has_next),
    }


def tree(
    session, scope, *, folder_id=None, page_types=(), page=1, pagesize=50, cursor=None, legacy=False
):
    begin_read(session, scope)
    parent_id = as_uuid(folder_id) if folder_id else None
    folders = _folders(session, scope)
    if parent_id and parent_id not in folders:
        raise WikiError("WIKI_NOT_FOUND", retryable=False)
    counts = Counter()
    direct = session.execute(
        select(WikiPage.folder_id, func.count())
        .where(scoped_pages(scope, page_types))
        .group_by(WikiPage.folder_id)
    ).all()
    for folder, count in direct:
        seen = set()
        while folder in folders and folder not in seen:
            counts[folder] += count
            seen.add(folder)
            folder = folders[folder].parent_id
    items = [
        {
            "id": str(folder.id),
            "node_kind": "folder",
            "parent_id": folder_id,
            "title": folder.name,
            "has_children": True,
            "page_count": counts[folder.id],
        }
        for folder in folders.values()
        if folder.parent_id == parent_id and counts[folder.id]
    ]
    items.sort(key=lambda item: item["id"])
    page_query = select(*PAGE_COLUMNS).where(
        scoped_pages(scope, page_types), WikiPage.folder_id == parent_id
    )
    if cursor or legacy:
        return _tree_cursor_page(session, items, page_query, folder_id, cursor or "", pagesize)
    folder_count = len(items)
    page_count = session.scalar(
        select(func.count()).select_from(WikiPage).where(
            scoped_pages(scope, page_types), WikiPage.folder_id == parent_id
        )
    )
    total = folder_count + page_count
    offset = (page - 1) * pagesize
    items = items[offset : offset + pagesize]
    remaining = pagesize - len(items)
    # 目录和页面共用一个页码，仅查询这一页所需的页面节点。
    if remaining and offset < total:
        rows = session.execute(
            page_query.order_by(WikiPage.id)
            .offset(max(0, offset - folder_count))
            .limit(remaining)
        )
        items.extend(_tree_page_node(row, folder_id) for row in rows)
    has_next = page * pagesize < total
    return {
        "parent_id": folder_id,
        "items": items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "hasnext": has_next,
        },
    }


def index(session, scope):
    begin_read(session, scope)
    counts = {
        kind: count
        for kind, count in session.execute(
            select(WikiPage.page_type, func.count())
            .where(scoped_pages(scope))
            .group_by(WikiPage.page_type)
        )
    }
    folder_counts = defaultdict(int)
    folders = _folders(session, scope)
    for folder, count in session.execute(
        select(WikiPage.folder_id, func.count())
        .where(scoped_pages(scope))
        .group_by(WikiPage.folder_id)
    ):
        seen = set()
        while folder in folders and folder not in seen:
            folder_counts[folder] += count
            seen.add(folder)
            folder = folders[folder].parent_id
    return {
        "counts": counts,
        "total_pages": sum(counts.values()),
        "folders": [
            {
                "id": str(key),
                "name": folders[key].name,
                "parent_id": str(folders[key].parent_id) if folders[key].parent_id else None,
                "page_count": count,
            }
            for key, count in folder_counts.items()
        ],
    }


def graph(
    session,
    scope,
    *,
    mode="overview",
    center_page_id=None,
    page_types=(),
    depth=1,
    node_limit=500,
    edge_limit=2000,
):
    begin_read(session, scope)
    condition = scoped_pages(scope, page_types)
    valid_ids = select(WikiPage.id).where(condition)
    edge_condition = and_(
        WikiPageLink.kb_id == as_uuid(scope["kb_id"]),
        WikiPageLink.source_page_id.in_(valid_ids),
        WikiPageLink.target_page_id.in_(valid_ids),
    )
    total_nodes = session.scalar(select(func.count()).select_from(WikiPage).where(condition))
    total_edges = session.scalar(
        select(func.count()).select_from(WikiPageLink).where(edge_condition)
    )
    truncated = False
    if mode == "ego":
        center = as_uuid(center_page_id)
        if session.scalar(select(WikiPage.id).where(condition, WikiPage.id == center)) is None:
            raise WikiError("WIKI_NOT_FOUND", retryable=False)
        ids, frontier = {center}, {center}
        for _ in range(depth):
            edges = list(
                session.execute(
                    select(WikiPageLink.source_page_id, WikiPageLink.target_page_id)
                    .where(
                        edge_condition,
                        or_(
                            WikiPageLink.source_page_id.in_(frontier),
                            WikiPageLink.target_page_id.in_(frontier),
                        ),
                    )
                    .order_by(WikiPageLink.source_page_id, WikiPageLink.target_page_id)
                    .limit(edge_limit + 1)
                )
            )
            truncated |= len(edges) > edge_limit
            neighbors = {value for edge in edges[:edge_limit] for value in edge} - ids
            ordered = sorted(neighbors, key=str)
            capacity = max(0, node_limit - len(ids))
            truncated |= len(ordered) > capacity
            frontier = set(ordered[:capacity])
            ids.update(frontier)
            if not frontier:
                break
        query = select(*PAGE_COLUMNS).where(condition, WikiPage.id.in_(ids))
    else:
        query = select(*PAGE_COLUMNS).where(condition).limit(node_limit)
        truncated = total_nodes > node_limit
    rows = session.execute(query.order_by(WikiPage.id)).all()
    ids = [row.id for row in rows]
    edges = list(
        session.execute(
            select(WikiPageLink.source_page_id, WikiPageLink.target_page_id)
            .where(
                edge_condition,
                WikiPageLink.source_page_id.in_(ids),
                WikiPageLink.target_page_id.in_(ids),
            )
            .order_by(WikiPageLink.source_page_id, WikiPageLink.target_page_id)
            .limit(edge_limit + 1)
        )
    )
    truncated |= len(edges) > edge_limit
    return {
        "nodes": [
            {"id": str(row.id), "title": row.title, "type": row.page_type, "version": row.version}
            for row in rows
        ],
        "edges": [{"source": str(a), "target": str(b)} for a, b in edges[:edge_limit]],
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "returned_nodes": len(rows),
        "returned_edges": min(len(edges), edge_limit),
        "truncated": truncated,
        "totals_scope": "knowledge_base",
    }
