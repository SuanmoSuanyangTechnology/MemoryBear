"""Chunk preview and CRUD behavior migrated from the legacy controller."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from functools import partial
from typing import Any, cast

from redbear_model import (
    QWEN3_VL_EMBEDDING_DIMENSION,
    EmbeddingPurpose,
    EmbeddingRequest,
    ModelConfigSnapshot,
)
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.chunk import ChunkCreate, ChunkType
from ..error_mapping import map_model_error
from ..errors import KnowledgeError
from ..integrations.model.embedding import RedBearEmbeddings
from ..integrations.model.errors import ModelInvokeClientError
from ..integrations.model.invoke_backend import ref_from_view
from ..integrations.model.views import is_qwen3_vl_embedding_view
from ..model_errors import MODEL_FIELD_ROLES
from ..models.owned import Document
from ..parser_configuration import to_runtime
from ..rag.chunk.metadata import merge_parser_metadata
from ..rag.models.chunk import DocumentChunk
from ..rag.retrieval.async_elasticsearch import AsyncChunkStore
from ..repositories.model_registry import AsyncSQLModelRegistry
from ..repositories.parser_configuration import (
    AsyncParserConfigurationRepository,
    document_configuration,
)
from ..repositories.reference import ReferenceRepository
from ..runtime import ProcessRuntime
from ..tasks.dispatch import TaskDispatcher
from ..utils.datetime_utils import to_timestamp_ms
from . import document as document_service
from . import file as file_service
from . import knowledge as knowledge_service
from .document_models import resolve_document_models
from .document_processing import (
    ParseDocumentSnapshot,
    build_document_vision_model,
    document_media_field,
    parse_document_chunks,
)
from .document_task_lifecycle import guard_chunk_mutation
from .multimodal_image import resolve_storage_images_async

logger = logging.getLogger(__name__)


def _not_found(code: str = "KB_CHUNK_NOT_FOUND") -> KnowledgeError:
    return KnowledgeError.from_code(code)


@dataclass(frozen=True)
class ChunkDocumentSnapshot:
    knowledge_id: uuid.UUID
    document_id: uuid.UUID
    file_id: uuid.UUID
    file_name: str
    file_created_at: Any
    parent_child_mode: bool
    parser_config: dict[str, Any]
    embedding_id: uuid.UUID | None
    workspace_id: uuid.UUID
    tenant_id: uuid.UUID
    rag_enabled: bool
    image2text_id: uuid.UUID | None = None
    audio2text_id: uuid.UUID | None = None
    video2text_id: uuid.UUID | None = None
    model_sources: dict[str, str | None] = field(default_factory=dict)
    graph_parser_config: dict[str, Any] = field(default_factory=dict)
    file_key: str | None = None


async def get_chunk_document_snapshot(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
    document_id: uuid.UUID,
    principal: Principal,
    *,
    include_file: bool = False,
) -> ChunkDocumentSnapshot:
    knowledge = await knowledge_service.get_knowledge(db, knowledge_id, principal)
    if knowledge is None:
        raise _not_found("KB_KNOWLEDGE_NOT_FOUND")
    document = await document_service.get_document(
        db,
        document_id,
        principal,
        knowledge_id,
    )
    if document is None:
        raise _not_found("KB_DOCUMENT_NOT_FOUND")
    file_key = None
    if include_file:
        file = await file_service.get_file(db, document.file_id, principal, knowledge_id)
        if file is None:
            raise _not_found("KB_FILE_NOT_FOUND")
        if not file.file_key:
            raise _not_found("KB_FILE_STORAGE_KEY_MISSING")
        file_key = file.file_key
    configuration = await AsyncParserConfigurationRepository(db).get_effective_knowledge(
        knowledge_id
    )
    if configuration is None:
        raise ValueError(f"Knowledge configuration {knowledge.id} not found")
    workspace = await ReferenceRepository.get_workspace(
        db, cast(uuid.UUID, knowledge.workspace_id)
    )
    if workspace is None:
        raise ValueError(f"Workspace {knowledge.workspace_id} not found")
    saved = document_configuration(document)
    models = resolve_document_models(saved, configuration, workspace)
    return ChunkDocumentSnapshot(
        knowledge_id=knowledge.id,
        document_id=document.id,
        file_id=document.file_id,
        file_name=document.file_name,
        file_created_at=document.created_at,
        parent_child_mode=saved.chunk_method == "parent_child",
        parser_config=to_runtime(saved),
        embedding_id=knowledge.embedding_id,
        workspace_id=cast(uuid.UUID, knowledge.workspace_id),
        tenant_id=cast(uuid.UUID, workspace.tenant_id),
        rag_enabled=configuration.index_strategy in {"rag", "rag_wiki"},
        image2text_id=models.vision,
        audio2text_id=models.audio,
        video2text_id=models.video,
        model_sources=models.sources,
        graph_parser_config=to_runtime(configuration),
        file_key=file_key,
    )


def validate_chunk_create(
    parent_child_mode: bool,
    create_data: ChunkCreate,
) -> None:
    if parent_child_mode:
        if create_data.chunk_type not in {ChunkType.PARENT, ChunkType.CHILD}:
            raise KnowledgeError.from_code(
                "KB_PARENT_CHILD_CHUNK_TYPE_REQUIRED",
            )
        if create_data.chunk_type is ChunkType.CHILD and not create_data.parent_id:
            raise KnowledgeError.from_code(
                "KB_CHILD_PARENT_ID_REQUIRED",
            )
        return
    if create_data.chunk_type in {ChunkType.PARENT, ChunkType.CHILD}:
        raise KnowledgeError.from_code(
            "KB_PARENT_CHILD_MODE_DISABLED",
        )


def build_new_chunks(
    snapshot: ChunkDocumentSnapshot,
    items: list[ChunkCreate],
    *,
    current_sort_id: int,
    id_factory: Any = None,
) -> list[DocumentChunk]:
    create_id = id_factory or (lambda: uuid.uuid4().hex)
    chunks = []
    sort_id = current_sort_id
    for create_data in items:
        validate_chunk_create(snapshot.parent_child_mode, create_data)
        sort_id += 1
        metadata = {
            "doc_id": create_id(),
            "file_id": str(snapshot.file_id),
            "file_name": snapshot.file_name,
            "file_created_at": to_timestamp_ms(snapshot.file_created_at),
            "document_id": str(snapshot.document_id),
            "knowledge_id": str(snapshot.knowledge_id),
            "sort_id": sort_id,
            "status": 1,
            **create_data.type_metadata,
        }
        if create_data.is_qa:
            metadata.update(create_data.qa_metadata)
        chunks.append(
            DocumentChunk(
                page_content=create_data.chunk_content,
                metadata=metadata,
            )
        )
    return chunks


def materialize_preview_chunks(
    snapshot: ChunkDocumentSnapshot,
    parsed: list[DocumentChunk],
) -> list[DocumentChunk]:
    system_base = {
        "file_id": str(snapshot.file_id),
        "file_name": snapshot.file_name,
        "file_created_at": to_timestamp_ms(snapshot.file_created_at),
        "document_id": str(snapshot.document_id),
        "knowledge_id": str(snapshot.knowledge_id),
        "status": 1,
    }
    if snapshot.parent_child_mode:
        parents: list[DocumentChunk] = []
        children: list[DocumentChunk] = []
        child_sort_id = 0
        for parent_sort_id, parsed_parent in enumerate(parsed):
            parent_id = uuid.uuid4().hex
            parents.append(
                DocumentChunk(
                    page_content=parsed_parent.page_content,
                    metadata=merge_parser_metadata(
                        {
                            **system_base,
                            "doc_id": parent_id,
                            "sort_id": parent_sort_id,
                            "chunk_type": "parent",
                        },
                        {"metadata": parsed_parent.metadata},
                    ),
                )
            )
            for parsed_child in parsed_parent.children or []:
                children.append(
                    DocumentChunk(
                        page_content=parsed_child.page_content,
                        metadata=merge_parser_metadata(
                            {
                                **system_base,
                                "doc_id": uuid.uuid4().hex,
                                "sort_id": child_sort_id,
                                "chunk_type": "child",
                                "parent_id": parent_id,
                            },
                            {"metadata": parsed_child.metadata},
                        ),
                    )
                )
                child_sort_id += 1
        return [*parents, *children]
    return [
        DocumentChunk(
            page_content=chunk.page_content,
            metadata=merge_parser_metadata(
                {
                    **system_base,
                    "doc_id": uuid.uuid4().hex,
                    "sort_id": index,
                    "chunk_type": (chunk.metadata or {}).get("chunk_type", "chunk"),
                },
                {"metadata": chunk.metadata},
            ),
        )
        for index, chunk in enumerate(parsed)
    ]


def nest_parent_child_page(
    parents: list[DocumentChunk],
    children: list[DocumentChunk],
    *,
    page: int,
    pagesize: int,
    total: int,
) -> dict[str, Any]:
    children_by_parent: dict[str, list[DocumentChunk]] = {}
    for child in children:
        parent_id = (child.metadata or {}).get("parent_id")
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(child)
    for values in children_by_parent.values():
        values.sort(key=lambda item: (item.metadata or {}).get("sort_id", 0))
    for parent in parents:
        parent.children = children_by_parent.get(
            (parent.metadata or {}).get("doc_id"),
            [],
        )
    return {
        "items": parents,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "has_next": page * pagesize < total,
        },
    }


async def list_chunks(
    store: AsyncChunkStore,
    snapshot: ChunkDocumentSnapshot,
    *,
    page: int,
    pagesize: int,
    keywords: str | None,
) -> dict[str, Any]:
    if not snapshot.parent_child_mode:
        total, items = await store.search_by_segment(
            document_id=str(snapshot.document_id),
            query=keywords,
            pagesize=pagesize,
            page=page,
            asc=True,
        )
        return {
            "items": items,
            "page": {
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "has_next": page * pagesize < total,
            },
        }

    total_parents, parents = await store.search_by_segment(
        document_id=str(snapshot.document_id),
        query=keywords,
        pagesize=pagesize,
        page=page,
        asc=True,
        chunk_types="parent",
    )
    if total_parents == 0 and not parents:
        hits = [
            hit
            async for hit in store.iter_by_segment(
                document_id=str(snapshot.document_id),
                query=keywords,
                asc=True,
            )
        ]
        chunks = [store.hit_to_chunk(hit) for hit in hits]
        parents = [
            chunk for chunk in chunks if (chunk.metadata or {}).get("chunk_type") == "parent"
        ]
        if not parents:
            offset = (page - 1) * pagesize
            items = chunks[offset : offset + pagesize]
            return {
                "items": items,
                "page": {
                    "page": page,
                    "pagesize": pagesize,
                    "total": len(chunks),
                    "has_next": page * pagesize < len(chunks),
                },
            }
        total_parents = len(parents)
        offset = (page - 1) * pagesize
        parents = parents[offset : offset + pagesize]
        parent_ids = {(parent.metadata or {}).get("doc_id") for parent in parents}
        children = [
            chunk
            for chunk in chunks
            if (chunk.metadata or {}).get("chunk_type") == "child"
            and (chunk.metadata or {}).get("parent_id") in parent_ids
        ]
        return nest_parent_child_page(
            parents,
            children,
            page=page,
            pagesize=pagesize,
            total=total_parents,
        )

    parent_ids = [parent.metadata["doc_id"] for parent in parents]
    child_hits = [
        hit
        async for hit in store.iter_by_segment(
            document_id=str(snapshot.document_id),
            asc=True,
            chunk_types="child",
            parent_ids=parent_ids,
        )
    ]
    children = [store.hit_to_chunk(hit) for hit in child_hits]
    return nest_parent_child_page(
        parents,
        children,
        page=page,
        pagesize=pagesize,
        total=total_parents,
    )


async def require_owned_chunk(
    store: AsyncChunkStore,
    snapshot: ChunkDocumentSnapshot,
    doc_id: str,
) -> DocumentChunk:
    chunk = await store.get_by_segment(doc_id)
    if chunk is None or str((chunk.metadata or {}).get("document_id")) != str(
        snapshot.document_id
    ):
        raise _not_found()
    return chunk


async def update_document_chunk_count(
    db: AsyncSession,
    snapshot: ChunkDocumentSnapshot,
    delta: int,
) -> None:
    try:
        await db.execute(
            update(Document)
            .where(
                Document.id == snapshot.document_id,
                Document.kb_id == snapshot.knowledge_id,
            )
            .values(chunk_num=Document.chunk_num + delta)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def resolve_embedding_config(
    db: AsyncSession,
    snapshot: ChunkDocumentSnapshot,
    principal: Principal,
) -> ModelConfigSnapshot | None:
    if not snapshot.rag_enabled:
        return None
    if snapshot.embedding_id is None:
        raise KnowledgeError.from_code(
            "KB_EMBEDDING_MODEL_UNAVAILABLE",
        )
    try:
        config = await AsyncSQLModelRegistry(db).get_model_config(
            snapshot.embedding_id,
            principal.tenant_id,
        )
    except Exception as exc:
        raise KnowledgeError.from_code(
            "KB_EMBEDDING_MODEL_UNAVAILABLE",
        ) from exc
    # 存量口径：不存在/不可见/下线/停用一律折叠为同一错误码
    if config is None or config.is_deprecated or not config.is_active:
        raise KnowledgeError.from_code(
            "KB_EMBEDDING_MODEL_UNAVAILABLE",
        )
    return config


async def preview_document(
    runtime: ProcessRuntime,
    snapshot: ChunkDocumentSnapshot,
    binary: bytes,
) -> list[DocumentChunk]:
    """Use the worker's parser and selected models, with bounded pages and no asset writes."""
    parse_snapshot = ParseDocumentSnapshot(
        document_id=snapshot.document_id, knowledge_id=snapshot.knowledge_id,
        workspace_id=snapshot.workspace_id, tenant_id=snapshot.tenant_id,
        file_id=snapshot.file_id, file_name=snapshot.file_name,
        source_file_name=snapshot.file_name,
        file_created_at_ms=to_timestamp_ms(snapshot.file_created_at),
        parser_config=snapshot.parser_config, parent_child_mode=snapshot.parent_child_mode,
        embedding_id=None, llm_id=None, image2text_id=snapshot.image2text_id,
        audio2text_id=snapshot.audio2text_id, video2text_id=snapshot.video2text_id,
        rag_enabled=snapshot.rag_enabled, model_sources=snapshot.model_sources,
    )

    def parse():
        vision_model = build_document_vision_model(runtime, parse_snapshot, snapshot.file_key or "")
        return parse_document_chunks(
            runtime, parse_snapshot, binary, lambda *args, **kwargs: None, vision_model,
            preview=True,
        )

    try:
        parsed = await asyncio.to_thread(parse)
    except ModelInvokeClientError as exc:
        role = MODEL_FIELD_ROLES[document_media_field(snapshot.file_name)]
        error = map_model_error(exc, model_role=role)
        if error.http_status_override == 503:
            # GET retries at the gateway must not invoke the model twice.
            error.http_status_override = 500
        raise error from exc
    if snapshot.parent_child_mode:
        from ..rag.models.chunk import ChildDocumentChunk

        children, parents, parent_map = parsed
        return [
            DocumentChunk(
                page_content=parent["content_with_weight"], metadata=parent.get("metadata", {}),
                children=[ChildDocumentChunk(
                    page_content=child["content_with_weight"], metadata=child.get("metadata", {}),
                ) for child_index, child in enumerate(children)
                    if parent_map[child_index] == parent_index],
            ) for parent_index, parent in enumerate(parents)
        ]
    return [DocumentChunk(page_content=chunk["content_with_weight"],
                          metadata=chunk.get("metadata", {})) for chunk in parsed]


def build_chunk_store(
    runtime: ProcessRuntime,
    client: Any,
    snapshot: ChunkDocumentSnapshot,
    embedding_config: ModelConfigSnapshot | None = None,
    *,
    tenant_id: uuid.UUID,
    for_mutation: bool = True,
) -> AsyncChunkStore:
    embed = None
    embed_chunks = None
    embed_unit_contents = None
    image_resolver = None
    embedding_dimension = None
    multimodal = (
        snapshot.rag_enabled and embedding_config is not None
        and is_qwen3_vl_embedding_view(embedding_config)
    )
    # Read-only paths (e.g. chunk listing) only need the multimodal marker to
    # collapse unit docs; skip building the embedding model client entirely.
    if snapshot.rag_enabled and embedding_config is not None and for_mutation:
        model = RedBearEmbeddings.for_invoke_ref(
            ref_from_view(embedding_config, tenant_id),
            pool=runtime.model_runtime,
            multimodal=multimodal,
        )
        if multimodal:
            embedding_dimension = QWEN3_VL_EMBEDDING_DIMENSION

            async def embed_unit_contents(contents: list[Any]) -> Any:
                result = await model.aembed_contents(
                    EmbeddingRequest(
                        purpose=EmbeddingPurpose.INDEX,
                        contents=tuple(contents),
                    )
                )
                return list(result.vector)

            async def image_resolver(asset_ids: list[str]) -> dict[str, Any]:
                return await resolve_storage_images_async(
                    runtime,
                    snapshot.knowledge_id,
                    asset_ids,
                    phase="index",
                )
        else:
            embed = model.aembed_documents
    return AsyncChunkStore(
        client,
        snapshot.knowledge_id,
        vectorize=snapshot.rag_enabled,
        embed=embed,
        embed_chunks=embed_chunks,
        embed_unit_contents=embed_unit_contents,
        image_resolver=image_resolver,
        embedding_dimension=embedding_dimension,
        # Match worker indexes: multimodal vectors use script_score recall.
        vector_indexed=not multimodal,
        multimodal=multimodal,
        mutation_context=(
            partial(
                guard_chunk_mutation, runtime, snapshot.knowledge_id, snapshot.document_id, client
            )
            if for_mutation else None
        ),
    )


async def dispatch_graph_best_effort(snapshot: ChunkDocumentSnapshot) -> None:
    try:
        await document_service.dispatch_document_graph_sync(
            TaskDispatcher(),
            snapshot.knowledge_id,
            snapshot.document_id,
            snapshot.graph_parser_config,
            dispatch_legacy=False,
        )
    except Exception:
        logger.error(
            "Failed to dispatch graph sync after chunk mutation knowledge_id=%s "
            "document_id=%s",
            snapshot.knowledge_id,
            snapshot.document_id,
        )


__all__ = [
    "ChunkDocumentSnapshot",
    "build_chunk_store",
    "build_new_chunks",
    "dispatch_graph_best_effort",
    "get_chunk_document_snapshot",
    "list_chunks",
    "materialize_preview_chunks",
    "nest_parent_child_page",
    "require_owned_chunk",
    "resolve_embedding_config",
    "preview_document",
    "update_document_chunk_count",
    "validate_chunk_create",
]
