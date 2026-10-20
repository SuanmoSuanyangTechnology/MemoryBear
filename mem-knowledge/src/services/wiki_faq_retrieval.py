"""FAQ 索引同步与纯向量召回；数据库资格不依赖展示状态 change_pending。"""

import asyncio
import logging
import random
import uuid

from ..api.schemas.chunk import ImageRetrievalQuery, KnowledgeRetrievalSource, RetrieveType
from ..api.schemas.knowledge_metadata import MetadataFilterMode
from ..api.schemas.knowledge_retrieval import KnowledgeRetrievalResult
from ..error_mapping import map_text_embedding_error
from ..errors import KnowledgeError
from ..rag.faq.index import (
    FAQ_ID_BATCH_SIZE,
    FAQ_SYNC_CONCURRENCY,
    FAQTarget,
    embedding_for,
    is_indexed,
    missing_projections,
    projection_id,
    query_vector,
    write_projection,
)
from ..rag.models.chunk import DocumentChunk
from ..rag.retrieval.async_elasticsearch import AsyncElasticSearchRetrieval
from ..rag.retrieval.models import RetrievalSearchOptions
from ..rag.wiki.config import WikiError
from ..rag.wiki.diagnostics import diagnostic_json, exception_details
from ..repositories.wiki_faq import retrieval as repository
from .knowledge_retrieval_preparation import KnowledgeRetrievalPreparation as Preparation
from .wiki.runtime import transaction

logger = logging.getLogger(__name__)


def validate_request(request):
    if request.source == KnowledgeRetrievalSource.EXTERNAL_API:
        raise KnowledgeError.from_code("KB_HTTP_FORBIDDEN")
    if (
        isinstance(request.normalized_query, ImageRetrievalQuery)
        or request.file_names_filter
        or request.metadata_filters
        or request.metadata_filter_mode != MetadataFilterMode.MANUAL
        or any(
            "retrieve_type" in config.model_fields_set
            and config.retrieve_type != RetrieveType.WIKI_FAQ
            for config in request.knowledge_bases
        )
    ):
        logger.warning("wiki-FAQ 请求不支持图片、文档过滤或混合召回类型 stage=validate_request")
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR")


async def _target_for_sync(runtime, scope, row):
    if row["embedding_id"] is None:
        raise KnowledgeError.from_code("KB_RETRIEVAL_EMBEDDING_MODEL_NOT_CONFIGURED")
    async with runtime.database.async_session() as db:
        model = await Preparation._snapshot_model(db, row["embedding_id"], scope["tenant_id"])
    return FAQTarget(scope=scope, embedding=model, top_k=1, score_threshold=None)


async def index_state(runtime, scope, faq_id, *, synchronize=False, expected_row_version=None):
    # 身份/作用域/并发错误保留正常HTTP错误语义，只有外部索引故障转为明确同步结果。
    try:
        row = await asyncio.to_thread(
            transaction,
            runtime,
            repository.load_for_sync,
            scope,
            faq_id,
            expected_row_version,
        )
    except WikiError as exc:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND") from exc
    base = {"faq_id": str(faq_id), "content_revision": row["content_revision"]}
    if not row["eligible"]:
        return {**base, "status": "not_required", "error_code": None}
    index_name = None
    try:
        target = await _target_for_sync(runtime, scope, row)
        index_name = target.index_name
        client = await runtime.elasticsearch.client()
        if synchronize:
            await write_projection(runtime, client, target, row)
        ready = await is_indexed(client, target, row)
        current = await asyncio.to_thread(
            transaction,
            runtime,
            repository.load_for_sync,
            scope,
            faq_id,
        )
        if not current["eligible"]:
            return {**base, "status": "not_required", "error_code": None}
        current_target = await _target_for_sync(runtime, scope, current)
        if (
            current["content_revision"] != row["content_revision"]
            or current_target.model_key != target.model_key
        ):
            status = "pending"
        else:
            status = "ready" if ready else "pending"
        return {**base, "status": status, "error_code": None}
    except Exception as exc:
        logger.exception(
            "FAQ 索引处理失败 kb_id=%s faq_id=%s revision=%s index=%s stage=index_sync details=%s",
            scope["kb_id"],
            faq_id,
            row["content_revision"],
            index_name,
            diagnostic_json(exception_details(exc)),
        )
        return {
            **base,
            "status": "failed",
            "error_code": getattr(exc, "code", "KB_FAQ_INDEX_FAILED"),
        }


async def synchronize_results(runtime, scope, results):
    semaphore = asyncio.Semaphore(FAQ_SYNC_CONCURRENCY)

    async def sync_result(result):
        faq = result.get("faq", result)
        if not faq.get("id") or not faq.get("is_enabled"):
            return
        async with semaphore:
            try:
                faq["indexing"] = await index_state(runtime, scope, faq["id"], synchronize=True)
            except KnowledgeError as exc:
                # 已提交后被另一请求删除/改动，不把本次管理提交伪装成回滚失败。
                faq["indexing"] = {"status": "superseded", "error_code": exc.code}
            except Exception as exc:
                logger.exception(
                    "FAQ 管理已提交，索引同步失败 kb_id=%s faq_id=%s stage=load_index details=%s",
                    scope["kb_id"],
                    faq["id"],
                    diagnostic_json(exception_details(exc)),
                )
                faq["indexing"] = {"status": "failed", "error_code": "KB_FAQ_INDEX_FAILED"}

    await asyncio.gather(*(sync_result(result) for result in results))


def excluded_question(row, query):
    return query.strip().casefold() in {q.strip().casefold() for q in row["negative_questions"]}


def result_chunk(row, target, score):
    answers = row["answers"] if row["answer_strategy"] == "all" else [random.choice(row["answers"])]
    answer = "\n\n".join(answers)
    return DocumentChunk(
        page_content=f"question: {row['standard_question']}\nanswer: {answer}",
        metadata={
            "chunk_type": "wiki-FAQ",
            "doc_id": row["id"],
            "faq_id": row["id"],
            "knowledge_id": str(target.scope["kb_id"]),
            "wiki_page_id": row["wiki_page_id"],
            "question": row["standard_question"],
            "answer": answer,
            "answers": answers,
            "answer_strategy": row["answer_strategy"],
            "content_revision": row["content_revision"],
            "score": score,
        },
    )


async def _search_faq_vectors(store, target, vector, options):
    try:
        return await store.search_by_query_vector(vector, options)
    except Exception as exc:
        logger.exception(
            "FAQ 向量查询失败 kb_id=%s index=%s stage=vector_search details=%s",
            target.scope["kb_id"],
            target.index_name,
            diagnostic_json(exception_details(exc)),
        )
        raise


async def _gather_with_cleanup(coroutines):
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _ensure_current_projections(runtime, client, target, versions, model_semaphore):
    identities = {projection_id(faq_id, revision): faq_id for faq_id, revision in versions.items()}
    try:
        missing = await missing_projections(client, target, list(identities))
        ready = set(identities) - missing
        missing_ids = [identities[identity] for identity in missing]
        for offset in range(0, len(missing_ids), FAQ_ID_BATCH_SIZE):
            # 只读取缺失投影的当前内容；释放Session后才调用模型/ES。
            async with runtime.database.async_session() as db:
                rows = await repository.hydrate(
                    db, target.scope, missing_ids[offset : offset + FAQ_ID_BATCH_SIZE]
                )

            async def repair(row):
                async with model_semaphore:
                    try:
                        await write_projection(runtime, client, target, row)
                    except Exception as exc:
                        logger.exception(
                            "FAQ 投影补建失败 kb_id=%s faq_id=%s revision=%s "
                            "index=%s stage=index_repair details=%s",
                            target.scope["kb_id"],
                            row["id"],
                            row["content_revision"],
                            target.index_name,
                            diagnostic_json(exception_details(exc)),
                        )
                        raise
                    return projection_id(row["id"], row["content_revision"])

            current_rows = [
                row
                for row in rows.values()
                if row["content_revision"] == versions[uuid.UUID(row["id"])]
            ]
            for start in range(0, len(current_rows), FAQ_SYNC_CONCURRENCY):
                ready.update(
                    await _gather_with_cleanup(
                        repair(row) for row in current_rows[start : start + FAQ_SYNC_CONCURRENCY]
                    )
                )
        return [identity for identity in identities if identity in ready]
    except Exception as exc:
        logger.exception(
            "FAQ 索引修复失败 kb_id=%s index=%s stage=index_repair details=%s",
            target.scope["kb_id"],
            target.index_name,
            diagnostic_json(exception_details(exc)),
        )
        raise KnowledgeError.from_code("KB_FAQ_INDEX_FAILED") from exc


async def retrieve(runtime, request, principal):
    validate_request(request)
    async with runtime.database.async_session() as db:
        targets = await Preparation.prepare_faq_with_db(db, request, principal)
    if not targets:
        return KnowledgeRetrievalResult()
    client = await runtime.elasticsearch.client()
    store = AsyncElasticSearchRetrieval(client)
    semaphore = asyncio.Semaphore(FAQ_SYNC_CONCURRENCY)
    model_semaphore = asyncio.Semaphore(FAQ_SYNC_CONCURRENCY)

    async def search_target(target):
        async with semaphore:
            async with runtime.database.async_session() as db:
                versions = await repository.eligible_versions(db, target.scope)
            versions = {
                faq_id: revision
                for faq_id, revision, negatives in versions
                if request.query_text.strip().casefold()
                not in {q.strip().casefold() for q in negatives}
            }
            if not versions:
                return target, []
            identities = await _ensure_current_projections(
                runtime, client, target, versions, model_semaphore
            )
            if not identities:
                return target, []
            try:
                async with model_semaphore:
                    vector = await query_vector(
                        embedding_for(runtime, target), target, request.query_text
                    )
            except Exception as exc:
                logger.exception(
                    "FAQ 查询向量化失败 kb_id=%s index=%s stage=query_embedding details=%s",
                    target.scope["kb_id"],
                    target.index_name,
                    diagnostic_json(exception_details(exc)),
                )
                mapped = map_text_embedding_error(exc)
                if mapped is None or mapped is exc:
                    raise
                raise mapped from exc
            candidates = []
            for offset in range(0, len(identities), FAQ_ID_BATCH_SIZE):
                candidates.extend(
                    await _search_faq_vectors(
                        store,
                        target,
                        vector,
                        RetrievalSearchOptions(
                            indices=target.index_name,
                            top_k=target.top_k,
                            score_threshold=target.score_threshold,
                            file_names_filter=(),
                            document_ids_include=None,
                            extra_filters=(
                                {
                                    "ids": {
                                        "values": identities[offset : offset + FAQ_ID_BATCH_SIZE]
                                    }
                                },
                            ),
                        ),
                    )
                )
            candidates.sort(key=lambda chunk: (-chunk.metadata["score"], chunk.metadata["doc_id"]))
            return target, candidates[: target.top_k]

    groups = await _gather_with_cleanup(search_target(target) for target in targets)
    # ES查询之后重新解析授权；分享撤销/知识库停用/模型切换均不能沿用旧快照放行。
    async with runtime.database.async_session() as db:
        current_targets = await Preparation.prepare_faq_with_db(db, request, principal)
        current = {str(target.scope["kb_id"]): target for target in current_targets}
        output = []
        for target, candidates in groups:
            fresh = current.get(str(target.scope["kb_id"]))
            if fresh is None or fresh.scope != target.scope or fresh.model_key != target.model_key:
                continue
            ids = [uuid.UUID(chunk.metadata["doc_id"]) for chunk in candidates]
            rows = await repository.hydrate(db, fresh.scope, ids)
            for chunk in candidates:
                row = rows.get(chunk.metadata["doc_id"])
                if (
                    row is not None
                    and row["content_revision"] == chunk.metadata.get("content_revision")
                    and not excluded_question(row, request.query_text)
                ):
                    output.append(result_chunk(row, fresh, chunk.metadata["score"]))
    output.sort(key=lambda chunk: (-chunk.metadata["score"], chunk.metadata["faq_id"]))
    logger.info(
        "wiki-FAQ 召回完成 workspace_id=%s kb_count=%s result_count=%s",
        principal.workspace_id,
        len(targets),
        min(len(output), request.top_k),
    )
    return KnowledgeRetrievalResult(chunks=output[: request.top_k])
