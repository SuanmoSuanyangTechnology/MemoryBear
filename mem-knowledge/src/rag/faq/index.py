"""FAQ 投影适配器：复用文本向量化及 Chunk Store，修订身份隔离迟到写入。"""

import hashlib
import json
import logging
import math
import uuid
from dataclasses import dataclass
from typing import Any

from elasticsearch import BadRequestError
from redbear_model import EmbeddingPurpose, EmbeddingRequest, TextEmbeddingContent

from ...integrations.model.embedding import RedBearEmbeddings
from ...integrations.model.invoke_backend import ref_from_view
from ...integrations.model.views import is_qwen3_vl_embedding_view
from ..models.chunk import DocumentChunk, chunk_retrieval_content
from ..retrieval.async_elasticsearch import AsyncChunkStore
from ..retrieval.models import ModelRuntimeSnapshot
from ..wiki.diagnostics import diagnostic_json, exception_details

FAQ_ID_BATCH_SIZE = 500
FAQ_SYNC_CONCURRENCY = 3
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FAQTarget:
    scope: dict[str, Any]
    embedding: ModelRuntimeSnapshot
    top_k: int
    score_threshold: float | None

    @property
    def model_key(self):
        contract = {
            "id": str(self.embedding.model_config_id),
            "name": self.embedding.model_name,
            "provider": self.embedding.provider,
            "profile": self.embedding.profile.model_dump(mode="json"),
            "config_fingerprint": self.embedding.config_fingerprint,
        }
        return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()[:24]

    @property
    def index_name(self):
        return f"wiki_faq_{self.scope['kb_id']}_{self.model_key}".lower()


def projection_id(faq_id, revision):
    return f"{faq_id}:{revision}"


def embedding_for(runtime, target):
    return RedBearEmbeddings.for_invoke_ref(
        ref_from_view(
            target.embedding, target.embedding.tenant_id, workspace_id=target.scope["workspace_id"]
        ),
        pool=runtime.model_runtime,
        multimodal=is_qwen3_vl_embedding_view(target.embedding),
    )


async def query_vector(model, target, query):
    if is_qwen3_vl_embedding_view(target.embedding):
        result = await model.aembed_contents(
            EmbeddingRequest(
                purpose=EmbeddingPurpose.RETRIEVAL,
                contents=(TextEmbeddingContent(text=query),),
            )
        )
        return list(result.vector)
    return await model.aembed_query(query)


def projection(row):
    # 反例不进入正向向量；问题集合仍共用同一 FAQ 身份与答案集合。
    question = "\n".join([row["standard_question"], *row["similar_questions"]])
    return DocumentChunk(
        page_content=question,
        metadata={
            "doc_id": row["id"],
            "projection_id": projection_id(row["id"], row["content_revision"]),
            "content_revision": row["content_revision"],
            "chunk_type": "qa",
            "question": question,
            "status": 1,
        },
    )


async def is_indexed(client, target, row):
    if not await client.indices.exists(index=target.index_name):
        return False
    return bool(
        await client.exists(
            index=target.index_name,
            id=projection_id(row["id"], row["content_revision"]),
            realtime=False,
        )
    )


async def delete_projection(client, scope, faq_id):
    """删除当前知识库已有 FAQ 索引中该 FAQ 的全部修订，不依赖模型配置、不创建索引。"""
    knowledge_id = str(uuid.UUID(str(scope["kb_id"])))
    identity = str(uuid.UUID(str(faq_id)))
    index_pattern = f"wiki_faq_{knowledge_id}_*"
    response = await client.indices.refresh(
        index=index_pattern, allow_no_indices=True, ignore_unavailable=True
    )
    AsyncChunkStore._raise_on_failed_response(response, "FAQ deleted projection refresh")
    response = await client.delete_by_query(
        index=index_pattern,
        query={"term": {"metadata.doc_id": identity}},
        allow_no_indices=True,
        ignore_unavailable=True,
        conflicts="proceed",
        refresh=True,
    )
    AsyncChunkStore._raise_on_failed_response(response, "FAQ deleted projection cleanup")


async def write_projection(runtime, client, target, row):
    if await is_indexed(client, target, row):
        return
    model = embedding_for(runtime, target)
    multimodal = is_qwen3_vl_embedding_view(target.embedding)

    async def embed_chunks(chunks):
        if not multimodal:
            vectors = await model.aembed_documents([chunk_retrieval_content(c) for c in chunks])
            if len(vectors) != len(chunks) or any(
                not vector or not all(math.isfinite(value) for value in vector)
                for vector in vectors
            ):
                raise ValueError("FAQ embedding 返回数量不匹配、空向量或非有限数值")
            return vectors
        result = []
        for chunk in chunks:
            embedded = await model.aembed_contents(
                EmbeddingRequest(
                    purpose=EmbeddingPurpose.INDEX,
                    contents=(TextEmbeddingContent(text=chunk_retrieval_content(chunk)),),
                )
            )
            result.append(list(embedded.vector))
        return result

    store = AsyncChunkStore(
        client,
        target.scope["kb_id"],
        index_name=target.index_name,
        document_id_field="projection_id",
        embed_chunks=embed_chunks,
        multimodal=False,
        vector_indexed=not multimodal,
    )
    chunks = [projection(row)]
    try:
        await store.add_chunks(chunks)
    except BadRequestError as exc:
        error = (exc.body or {}).get("error", {})
        if not isinstance(error, dict) or error.get("type") != "resource_already_exists_exception":
            raise
        # 两个 FAQ 首次建同一集合的并发，不视为模型/索引业务失败。
        await store.add_chunks(chunks)
    response = await client.indices.refresh(index=target.index_name)
    store._raise_on_failed_response(response, "FAQ index refresh")
    # 只清理更早修订；迟到的旧同步不能删除新版记录。
    try:
        response = await client.delete_by_query(
            index=target.index_name,
            query={
                "bool": {
                    "filter": [
                        {"term": {"metadata.doc_id": row["id"]}},
                        {"range": {"metadata.content_revision": {"lt": row["content_revision"]}}},
                    ]
                }
            },
            conflicts="proceed",
            refresh=False,
        )
        store._raise_on_failed_response(response, "FAQ obsolete projection cleanup")
    except Exception as exc:
        # 旧修订已被数据库身份过滤；清理失败不能把已就绪的新索引报成失败。
        logger.warning(
            "FAQ 旧索引清理失败 faq_id=%s revision=%s stage=cleanup details=%s",
            row["id"],
            row["content_revision"],
            diagnostic_json(exception_details(exc)),
        )
