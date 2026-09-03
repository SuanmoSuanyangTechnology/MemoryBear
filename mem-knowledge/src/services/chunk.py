"""Chunk preview and CRUD behavior migrated from the legacy controller."""

from __future__ import annotations

import base64
import logging
import mimetypes
import uuid
from dataclasses import dataclass, field
from typing import Any

from redbear_model import (
    QWEN3_VL_EMBEDDING_DIMENSION,
    EmbeddingPurpose,
    EmbeddingRequest,
    ResolvedModelConfig,
    is_qwen3_vl_embedding,
    resolve_model_async,
)
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.chunk import ChunkCreate, ChunkType
from ..errors import KnowledgeError
from ..models.owned import Document
from ..rag.chunk.metadata import merge_parser_metadata
from ..rag.chunk.preview import preview_binary
from ..rag.models.chunk import DocumentChunk
from ..rag.models.embedding import (
    collect_asset_file_ids,
    prepare_chunk_embedding_contents,
)
from ..rag.retrieval.async_elasticsearch import AsyncChunkStore
from ..repositories.model_registry import AsyncSQLModelRegistry
from ..runtime import ProcessRuntime
from ..tasks.dispatch import TaskDispatcher
from ..utils.datetime_utils import to_timestamp_ms
from . import document as document_service
from . import file as file_service
from . import knowledge as knowledge_service
from .multimodal_image import resolve_storage_images_async

logger = logging.getLogger(__name__)


def _not_found(message: str = "Chunk resource not found") -> KnowledgeError:
    return KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", message)


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
    image2text_id: uuid.UUID | None = None
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
        raise _not_found("Knowledge resource not found")
    document = await document_service.get_document(
        db,
        document_id,
        principal,
        knowledge_id,
    )
    if document is None:
        raise _not_found("Document resource not found")
    file_key = None
    if include_file:
        file = await file_service.get_file(db, document.file_id, principal, knowledge_id)
        if file is None:
            raise _not_found("File resource not found")
        if not file.file_key:
            raise _not_found("File has no storage key (legacy data not migrated)")
        file_key = file.file_key
    return ChunkDocumentSnapshot(
        knowledge_id=knowledge.id,
        document_id=document.id,
        file_id=document.file_id,
        file_name=document.file_name,
        file_created_at=document.created_at,
        parent_child_mode=document.is_parent_child_mode,
        parser_config=dict(document.parser_config or {}),
        embedding_id=knowledge.embedding_id,
        image2text_id=knowledge.image2text_id,
        graph_parser_config=dict(knowledge.parser_config or {}),
        file_key=file_key,
    )


def validate_chunk_create(
    parent_child_mode: bool,
    create_data: ChunkCreate,
) -> None:
    if parent_child_mode:
        if create_data.chunk_type not in {ChunkType.PARENT, ChunkType.CHILD}:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                "父子分块模式下仅允许创建 parent 或 child 类型块",
            )
        if create_data.chunk_type is ChunkType.CHILD and not create_data.parent_id:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                "创建子块时必须提供 parent_id",
            )
        return
    if create_data.chunk_type in {ChunkType.PARENT, ChunkType.CHILD}:
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR",
            "当前文档未启用父子分块模式，不允许创建 parent/child 类型块",
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
) -> ResolvedModelConfig:
    if snapshot.embedding_id is None:
        raise KnowledgeError.from_code(
            "KB_MODEL_UNAVAILABLE",
            "Embedding model config is unavailable",
        )
    try:
        return await resolve_model_async(
            AsyncSQLModelRegistry(db),
            model_config_id=snapshot.embedding_id,
            tenant_id=principal.tenant_id,
        )
    except Exception as exc:
        raise KnowledgeError.from_code(
            "KB_MODEL_UNAVAILABLE",
            "Embedding model config is unavailable",
        ) from exc


async def resolve_vision_config(
    db: AsyncSession,
    snapshot: ChunkDocumentSnapshot,
    principal: Principal,
) -> ResolvedModelConfig:
    if snapshot.image2text_id is None:
        raise KnowledgeError.from_code(
            "KB_MODEL_UNAVAILABLE",
            "image2text model config is unavailable",
            status_code=400,
            response_code=400,
            response_style="http",
        )
    try:
        return await resolve_model_async(
            AsyncSQLModelRegistry(db),
            model_config_id=snapshot.image2text_id,
            tenant_id=principal.tenant_id,
        )
    except Exception as exc:
        raise KnowledgeError.from_code(
            "KB_MODEL_UNAVAILABLE",
            "No available image2text api key found",
            status_code=400,
            response_code=400,
            response_style="http",
        ) from exc


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")).strip()
            for item in content
            if isinstance(item, dict) and item.get("text")
        ).strip()
    return str(content).strip()


async def preview_with_vision(
    runtime: ProcessRuntime,
    snapshot: ChunkDocumentSnapshot,
    binary: bytes,
    vision_config: ResolvedModelConfig,
) -> list[DocumentChunk]:
    suffix = snapshot.file_name.rsplit(".", 1)[-1].lower() if "." in snapshot.file_name else ""
    image_exts = {"png", "jpeg", "jpg", "webp", "gif"}
    audio_exts = {
        "da", "wave", "wav", "mp3", "aac", "flac", "ogg", "aiff", "au",
        "midi", "wma", "realaudio", "vqf", "oggvorbis", "ape",
    }
    video_exts = {"mp4", "mov", "avi", "flv", "mpeg", "mpg", "webm", "wmv", "3gp", "3gpp", "mkv"}
    if suffix not in image_exts | audio_exts | video_exts:
        import asyncio

        return await asyncio.to_thread(
            preview_binary,
            snapshot.file_name,
            binary,
            snapshot.parser_config,
        )

    from langchain_core.messages import HumanMessage
    from redbear_model.runtime import RedBearLLM

    encoded = base64.b64encode(binary).decode("ascii")
    media_type = mimetypes.guess_type(snapshot.file_name)[0] or "application/octet-stream"
    if suffix in image_exts:
        media_block = {
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
        }
        instruction = "Describe the image accurately for knowledge retrieval."
    elif suffix in audio_exts:
        media_block = {
            "type": "input_audio",
            "input_audio": {"data": encoded, "format": suffix},
        }
        instruction = "Transcribe the audio accurately for knowledge retrieval."
    else:
        media_block = {
            "type": "video_url",
            "video_url": {"url": f"data:{media_type};base64,{encoded}"},
        }
        instruction = "Describe and transcribe the video accurately for knowledge retrieval."
    model = RedBearLLM(vision_config, client_pool=runtime.model_runtime.pool)
    response = await model.ainvoke(
        [HumanMessage(content=[{"type": "text", "text": instruction}, media_block])]
    )
    text = _message_text(response)
    if not text:
        raise KnowledgeError.from_code(
            "KB_MODEL_UNAVAILABLE",
            "Image-to-text model returned empty content",
        )
    return [
        DocumentChunk(
            page_content=text,
            metadata={"chunk_type": "chunk", "sort_id": 0, "vision_text": text},
            children=[],
        )
    ]


def build_chunk_store(
    runtime: ProcessRuntime,
    client: Any,
    snapshot: ChunkDocumentSnapshot,
    resolved_embedding: ResolvedModelConfig | None = None,
) -> AsyncChunkStore:
    embed = None
    embed_chunks = None
    embedding_dimension = None
    if resolved_embedding is not None:
        from redbear_model.runtime import RedBearEmbeddings

        model = RedBearEmbeddings(
            resolved_embedding,
            client_pool=runtime.model_runtime.pool,
        )
        if is_qwen3_vl_embedding(resolved_embedding):
            embedding_dimension = QWEN3_VL_EMBEDDING_DIMENSION

            async def embed_multimodal_chunks(
                chunks: list[DocumentChunk],
            ) -> list[list[float] | None]:
                asset_ids = collect_asset_file_ids(chunks)
                images = await resolve_storage_images_async(
                    runtime,
                    snapshot.knowledge_id,
                    asset_ids,
                    phase="index",
                )
                vectors: list[list[float] | None] = []
                for chunk in chunks:
                    contents = prepare_chunk_embedding_contents(chunk, images)
                    if not contents:
                        vectors.append(None)
                        continue
                    result = await model.aembed_contents(
                        EmbeddingRequest(
                            purpose=EmbeddingPurpose.INDEX,
                            contents=contents,
                        )
                    )
                    vectors.append(list(result.vector))
                return vectors

            embed_chunks = embed_multimodal_chunks
        else:
            embed = model.aembed_documents
    return AsyncChunkStore(
        client,
        snapshot.knowledge_id,
        embed=embed,
        embed_chunks=embed_chunks,
        embedding_dimension=embedding_dimension,
        vector_indexed=not is_qwen3_vl_embedding(resolved_embedding)
        if resolved_embedding is not None
        else True,
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
    "resolve_vision_config",
    "preview_with_vision",
    "update_document_chunk_count",
    "validate_chunk_create",
]
