import asyncio
import json
import logging
import math
import uuid
from typing import Callable

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig, var_child_runnable_config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memory.enums import Neo4jNodeType, TripletPredicate, StorageType
from app.core.memory.exceptions import (
    MemoryModelType,
    MemoryRetrievalBusinessError,
    MemoryRetrievalImpact,
    MemoryRetrievalStage,
)
from app.core.memory.models.service_models import (
    Memory,
    MemorySearchResult,
    RelationMemory,
    RelationSearchResult,
    EntityPair
)
from app.core.memory.models.service_models import MemoryContext
from app.core.memory.prompt import prompt_manager
from app.core.memory.read_services.search_engine.result_builder import MetadataBuilder
from app.core.memory.read_services.search_engine.result_builder import data_builder_factory
from app.core.memory.read_services.search_engine.tools import make_entity_search_tool, make_relation_search_tool, \
    make_user_source_lookup_tool
from app.core.memory.read_services.search_engine.search_policy import (
    build_content_search_filters,
)
from app.core.memory.retrieval_trace.models import (
    RetrievalExecutionTrace,
    build_score_trace,
    diagnostic_fusion_score,
    finite_or_none,
    normalized_keyword_score,
)
from app.core.memory.storage.custom import (
    get_active_entities_by_ids,
    get_entity_pair_relations,
    get_user_entity_id,
    get_user_metadata,
    get_user_sources_for_entities,
)
from app.core.memory.storage.enums import MemoryNodeType, MemoryNodeLabel
from app.core.memory.storage.models import StorageReadResult
from app.core.memory.storage.models.dto import StorageItem
from app.core.memory.storage.service import get_storage_service
from app.core.models import RedBearEmbeddings, RedBearLLM, RedBearRerank
from app.core.models.llm import StructResponse
from app.core.rag.nlp.search import knowledge_retrieval
from app.db import get_async_db_context
from app.models import Conversation, MemoryMessage
from app.repositories import knowledge_repository
from app.schemas.app_schema import FileInput, FileType, TransferMethod

logger = logging.getLogger(__name__)

DEFAULT_ALPHA = 0.6
DEFAULT_FULLTEXT_SCORE_THRESHOLD = 1.5
DEFAULT_COSINE_SCORE_THRESHOLD = 0.5
DEFAULT_CONTENT_SCORE_THRESHOLD = 0.5

MAX_RERANK_CHARS_PER_DOC = 2000

RELATIONSHIP_LOOP_LIMIT = 6


class Neo4jSearchService:
    def __init__(
            self,
            ctx: MemoryContext,
            embedder: RedBearEmbeddings | None = None,
            llm: RedBearLLM | None = None,
            reranker: RedBearRerank | None = None,
            includes: list[MemoryNodeLabel] | None = None,
            on_error: Callable[[MemoryRetrievalBusinessError], None] | None = None,
            alpha: float = DEFAULT_ALPHA,
            fulltext_score_threshold: float = DEFAULT_FULLTEXT_SCORE_THRESHOLD,
            cosine_score_threshold: float = DEFAULT_COSINE_SCORE_THRESHOLD,
            content_score_threshold: float = DEFAULT_CONTENT_SCORE_THRESHOLD
    ):
        self.ctx = ctx
        self.alpha = alpha
        self.fulltext_score_threshold = fulltext_score_threshold
        self.cosine_score_threshold = cosine_score_threshold
        self.content_score_threshold = content_score_threshold

        self.embedder: RedBearEmbeddings | None = embedder
        self.llm: RedBearLLM | None = llm
        self.reranker: RedBearRerank | None = reranker
        self.on_error = on_error

        self.includes = includes
        if includes is None:
            self.includes = [
                MemoryNodeType.STATEMENT,
                MemoryNodeType.CHUNK,
                MemoryNodeType.EXTRACTED_ENTITY,
                MemoryNodeType.MEMORY_SUMMARY,
                MemoryNodeType.PERCEPTUAL,
                MemoryNodeType.DIALOGUE,
                # Neo4jNodeType.COMMUNITY
            ]

        self.relation_search_tool = make_relation_search_tool(self.ctx)
        self.entity_search_tool = make_entity_search_tool(self.ctx)
        self.user_source_lookup_tool = make_user_source_lookup_tool(self.ctx)
        self._user_source_looked_up_ids = set()
        self.service = get_storage_service()

    def _build_score_sidecar(
            self,
            keyword_results: StorageReadResult,
            embedding_results: StorageReadResult,
    ) -> dict[tuple[MemoryNodeLabel | None, str], dict]:
        """旁路保留关键词、语义分数，不修改原始召回记录和排序。"""
        sidecar: dict[tuple[MemoryNodeLabel | None, str], dict] = {}
        for record in keyword_results.items:
            node_id = str(record.data.get("id") or "")
            if not node_id:
                continue
            item = sidecar.setdefault((record.label, node_id), {})
            item["keyword_hit"] = True
            item["keyword_score"] = normalized_keyword_score(
                record.data.get("score"), self.fulltext_score_threshold
            )
        for record in embedding_results.items:
            node_id = str(record.data.get("id") or "")
            if not node_id:
                continue
            item = sidecar.setdefault((record.label, node_id), {})
            item["semantic_hit"] = True
            item["semantic_score"] = finite_or_none(record.data.get("score")) or 0.0
        for item in sidecar.values():
            item["fusion_score"] = diagnostic_fusion_score(
                item.get("keyword_score", 0.0),
                item.get("semantic_score", 0.0),
                self.alpha,
            )
        return sidecar

    async def _keyword_search(
            self,
            query: str,
            limit: int
    ) -> StorageReadResult:
        return await self.service.search_by_fulltext(
            node_filters=build_content_search_filters(
                self.includes,
                self.ctx.end_user_id,
            ),
            text=query,
            pre_limit=limit,
        )

    async def _embedding_search(
            self,
            query: str,
            limit: int
    ) -> StorageReadResult:
        query_embed = await self.embedder.aembed_query(query)
        return await self.service.search_by_embedding(
            node_filters=build_content_search_filters(
                self.includes,
                self.ctx.end_user_id,
            ),
            embed=query_embed,
            pre_limit=limit,
        )

    def _rerank(
            self,
            keyword_results: StorageReadResult,
            embedding_results: StorageReadResult,
            limit: int,
    ) -> StorageReadResult:
        keyword_items = self._normalize_kw_scores(keyword_results.items)

        kw_norm_map = {}
        for item in keyword_items:
            item_id = item.data["id"]
            kw_norm_map[item_id] = float(item.data.get("normalized_kw_score", 0))

        emb_norm_map = {}
        for item in embedding_results.items:
            item_id = item.data["id"]
            emb_norm_map[item_id] = float(item.data.get("score", 0))

        combined: dict[str, dict] = {}
        label_map: dict[str, MemoryNodeLabel | None] = {}
        for item in keyword_items:
            item_id = item.data["id"]
            combined[item_id] = item.data.copy()
            combined[item_id]["kw_score"] = kw_norm_map.get(item_id, 0)
            combined[item_id]["embedding_score"] = emb_norm_map.get(item_id, 0)
            label_map[item_id] = item.label

        for item in embedding_results.items:
            item_id = item.data["id"]
            if item_id in combined:
                combined[item_id]["embedding_score"] = emb_norm_map.get(item_id, 0)
            else:
                combined[item_id] = item.data.copy()
                combined[item_id]["kw_score"] = kw_norm_map.get(item_id, 0)
                combined[item_id]["embedding_score"] = emb_norm_map.get(item_id, 0)
                label_map[item_id] = item.label

        for item in combined.values():
            kw = float(item.get("kw_score", 0) or 0)
            emb = float(item.get("embedding_score", 0) or 0)
            base = self.alpha * emb + (1 - self.alpha) * kw
            item["content_score"] = base + min(1 - base, 0.1 * kw * emb)

        results = sorted(
            combined.values(), key=lambda x: x["content_score"], reverse=True
        )
        # results = [
        #     res for res in results
        #     if res["content_score"] > self.content_score_threshold
        # ]
        results = results[:limit]

        items = [
            StorageItem(label=label_map.get(merged["id"]), data=merged)
            for merged in results
        ]

        logger.debug(
            f"[MemorySearch] rerank: merged={len(combined)}, after_threshold={len(items)} "
            f"(alpha={self.alpha})"
        )
        backend = (
            keyword_results.backend
            if keyword_results.backend is not None
            and keyword_results.backend == embedding_results.backend
            else None
        )
        return StorageReadResult(backend=backend, items=items, total=len(items))

    async def _hybrid_search_with_model_rerank(
            self,
            kw_results: StorageReadResult,
            emb_results: StorageReadResult,
            query: str,
            limit: int,
            score_sidecar: dict[tuple[MemoryNodeLabel | None, str], dict],
    ) -> tuple[list[Memory], str, list[str]]:
        seen: dict[str, StorageItem] = {}
        for record in kw_results.items:
            rid = record.data.get("id", "")
            if rid and rid not in seen:
                seen[rid] = record
        for record in emb_results.items:
            rid = record.data.get("id", "")
            if rid and rid not in seen:
                seen[rid] = record

        if not seen:
            return [], "skipped", []

        memories: list[Memory] = []
        for record in seen.values():
            memory = data_builder_factory(record.label, record.data)
            score_info = score_sidecar.get((record.label, str(memory.id)), {})
            result_memory = Memory(
                score=memory.score,
                content=memory.content,
                data=memory.data,
                source=record.label,
                query=query,
                id=memory.id,
            )
            result_memory.retrieval_trace = build_score_trace(
                node_id=result_memory.id,
                node_type=record.label.value,
                final_score=result_memory.score,
                rank_basis="input_order",
                keyword_hit=bool(score_info.get("keyword_hit")),
                semantic_hit=bool(score_info.get("semantic_hit")),
                keyword_score=score_info.get("keyword_score"),
                semantic_score=score_info.get("semantic_score"),
                fusion_score=score_info.get("fusion_score"),
                matched_queries=[query],
            )
            memories.append(result_memory)

        rerank_applied = False
        documents = []
        try:
            documents = [
                Document(
                    page_content=mem.content[:MAX_RERANK_CHARS_PER_DOC],
                    metadata={"index": i},
                )
                for i, mem in enumerate(memories)
            ]
            reranked = []
            if documents:
                try:
                    reranked = await asyncio.to_thread(
                        self.reranker.compress_documents,
                        documents,
                        query,
                        top_n=min(limit, len(documents))
                    )
                except Exception as e:
                    if self.on_error is not None:
                        self.on_error(
                            MemoryRetrievalBusinessError.model_call_failed(
                                MemoryRetrievalStage.RERANK,
                                e,
                                model_type=MemoryModelType.RERANK,
                                impact=MemoryRetrievalImpact.ORDERING_DEGRADED,
                            )
                        )
                    raise

            try:
                index_to_score = {
                    doc.metadata["index"]: doc.metadata.get("relevance_score", 0.0)
                    for doc in reranked
                }
                for i, mem in enumerate(memories):
                    if i in index_to_score:
                        mem.score = index_to_score[i]
                        if mem.retrieval_trace is not None:
                            mem.retrieval_trace.rerank_score = finite_or_none(index_to_score[i])
                            mem.retrieval_trace.final_score = float(mem.score)
                            mem.retrieval_trace.rank_basis = "rerank_score"
            except Exception as e:
                if self.on_error is not None:
                    self.on_error(
                        MemoryRetrievalBusinessError.structured_result_parse_failed(
                            MemoryRetrievalStage.RERANK,
                            e,
                            model_type=MemoryModelType.RERANK,
                            impact=MemoryRetrievalImpact.ORDERING_DEGRADED,
                        )
                    )
                raise

            rerank_applied = True
        except Exception as e:
            logger.warning(
                f"[Neo4jSearch] Model rerank failed, falling back to content_score: {e}",
                exc_info=True,
            )
            index_to_score = {}

        degraded_reasons: list[str] = []
        rerank_status = "completed" if rerank_applied else "degraded"
        if not rerank_applied:
            degraded_reasons.append("rerank_failed")
        elif len(index_to_score) < len(memories):
            rerank_status = "degraded"
            degraded_reasons.append("rerank_partial_result")

        for memory in memories:
            if memory.retrieval_trace is not None:
                memory.retrieval_trace.final_score = float(memory.score)
        memories.sort(key=lambda x: x.score, reverse=True)
        memories = memories[:limit]
        if rerank_applied:
            logger.info(
                f"[Neo4jSearch] Model rerank applied: {len(documents)} → {len(memories)} memories"
            )
        return memories, rerank_status, degraded_reasons

    def _normalize_kw_scores(self, items: list[StorageItem]) -> list[StorageItem]:
        if not items:
            return []
        scores = [float(it.data.get("score", 0) or 0) for it in items]
        for it, s in zip(items, scores):
            it.data[f"normalized_kw_score"] = 1 / (1 + math.exp(-(s - self.fulltext_score_threshold) / 2)) if s else 0
        return items

    async def keyword_search(
            self,
            query: str,
            limit: int = 10,
    ) -> MemorySearchResult:
        """仅全文检索，不做 embedding / rerank / 关系检索。"""
        kw_results = await self._keyword_search(query, limit)

        if kw_results.total == 0:
            return MemorySearchResult(
                memories=[],
                execution_trace=RetrievalExecutionTrace(
                    keyword_status="completed",
                    semantic_status="skipped",
                    rerank_status="skipped",
                ),
            )

        all_records = self._normalize_kw_scores(kw_results.items)

        for r in all_records:
            cs = float(r.data.get("normalized_kw_score", 0))
            r.data["content_score"] = cs
            r.data["score"] = cs

        all_records.sort(key=lambda x: x.data["score"], reverse=True)

        memories = []
        for record in all_records[:limit]:
            memory = data_builder_factory(record.label, record.data)
            result_memory = Memory(
                score=memory.score,
                content=memory.content,
                data=memory.data,
                source=record.label,
                query=query,
                id=memory.id
            )
            result_memory.retrieval_trace = build_score_trace(
                node_id=result_memory.id,
                node_type=record.label.value,
                final_score=result_memory.score,
                rank_basis="keyword_score",
                keyword_hit=True,
                keyword_score=record.data.get("normalized_kw_score"),
                matched_queries=[query],
            )
            memories.append(result_memory)
        return MemorySearchResult(
            memories=memories,
            execution_trace=RetrievalExecutionTrace(
                keyword_status="completed",
                semantic_status="skipped",
                rerank_status="skipped",
                keyword_hit_count=kw_results.total,
                raw_hit_count=kw_results.total,
                merged_count=len(memories),
            ),
        )

    async def hybrid_search(
            self,
            query: str,
            limit: int = 10,
    ) -> MemorySearchResult:
        kw_task = self._keyword_search(query, limit)
        emb_task = self._embedding_search(query, limit)
        kw_results, emb_results = await asyncio.gather(kw_task, emb_task, return_exceptions=True)

        keyword_failed = isinstance(kw_results, BaseException)
        semantic_failed = isinstance(emb_results, BaseException)
        if keyword_failed:
            logger.warning(f"[MemorySearch] keyword search error: {kw_results}")
            kw_results = StorageReadResult()
        if semantic_failed:
            logger.warning(f"[MemorySearch] embedding search error: {emb_results}")
            if self.on_error is not None:
                self.on_error(
                    MemoryRetrievalBusinessError.model_call_failed(
                        MemoryRetrievalStage.VECTOR_SEARCH,
                        emb_results,
                        model_type=MemoryModelType.EMBEDDING,
                    )
                )
            emb_results = StorageReadResult()

        score_sidecar = self._build_score_sidecar(kw_results, emb_results)
        rerank_status = "skipped"
        degraded_reasons: list[str] = []
        if keyword_failed:
            degraded_reasons.append("keyword_search_failed")
        if semantic_failed:
            degraded_reasons.append("semantic_search_failed")

        if self.reranker is not None:
            memories, rerank_status, rerank_reasons = await self._hybrid_search_with_model_rerank(
                kw_results, emb_results, query, limit, score_sidecar
            )
            degraded_reasons.extend(rerank_reasons)
        else:
            memories = []
            reranked = self._rerank(
                kw_results,
                emb_results,
                limit
            )
            for record in reranked.items:
                node_type = record.label
                memory = data_builder_factory(node_type, record.data)
                result_memory = Memory(
                    score=memory.score,
                    content=memory.content,
                    data=memory.data,
                    source=node_type,
                    query=query,
                    id=memory.id
                )
                score_info = score_sidecar.get((node_type, str(memory.id)), {})
                fusion_score = finite_or_none(record.data.get("content_score"))
                rank_basis = (
                    "source_adjusted_score"
                    if fusion_score is not None and result_memory.score != fusion_score
                    else "fusion_score"
                )
                result_memory.retrieval_trace = build_score_trace(
                    node_id=result_memory.id,
                    node_type=node_type.value,
                    final_score=result_memory.score,
                    rank_basis=rank_basis,
                    keyword_hit=bool(score_info.get("keyword_hit")),
                    semantic_hit=bool(score_info.get("semantic_hit")),
                    keyword_score=record.data.get("kw_score"),
                    semantic_score=record.data.get("embedding_score"),
                    fusion_score=fusion_score,
                    matched_queries=[query],
                )
                memories.append(result_memory)
            memories.sort(key=lambda x: x.score, reverse=True)
            memories = memories[:limit]

        return MemorySearchResult(
            memories=memories,
            execution_trace=RetrievalExecutionTrace(
                keyword_status="failed" if keyword_failed else "completed",
                semantic_status="failed" if semantic_failed else "completed",
                rerank_status=rerank_status,
                keyword_hit_count=kw_results.total,
                semantic_hit_count=emb_results.total,
                raw_hit_count=kw_results.total + emb_results.total,
                merged_count=len(memories),
                degraded_reasons=degraded_reasons,
            ),
        )

    async def _run_relation_agent(self, query: str) -> RelationSearchResult:
        system_prompt = prompt_manager.render(
            name="relation_search",
            predicates=[p.to_dict() for p in TripletPredicate],
            loop_limit=RELATIONSHIP_LOOP_LIMIT - 1
        )

        tools = [self.relation_search_tool, self.entity_search_tool, self.user_source_lookup_tool]
        tool_map = {t.name: t for t in tools}
        llm_with_tools = self.llm.bind_tools(tools)

        messages: list[SystemMessage | HumanMessage | AIMessage | ToolMessage] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=(
                f"<user-query>{query}</user-query>"
            ))
        ]
        for _ in range(RELATIONSHIP_LOOP_LIMIT):
            _config_token = var_child_runnable_config.set(
                RunnableConfig(callbacks=[], tags=[], metadata={})
            )
            try:
                try:
                    response: AIMessage = await llm_with_tools.ainvoke(messages)
                except Exception as e:
                    if self.on_error is not None:
                        self.on_error(
                            MemoryRetrievalBusinessError.model_call_failed(
                                MemoryRetrievalStage.RELATION_SEARCH,
                                e,
                                model_type=MemoryModelType.LLM,
                            )
                        )
                    raise
                messages.append(response)

                if not response.tool_calls:
                    break

                async def run_tool(tc):
                    try:
                        tool = tool_map[tc["name"]]
                        result = await tool.ainvoke(tc["args"])
                        return ToolMessage(
                            content=json.dumps(result, ensure_ascii=False),
                            tool_call_id=tc["id"],
                        )
                    except Exception as e:
                        return ToolMessage(
                            content=json.dumps({"error": str(e)}),
                            tool_call_id=tc["id"],
                        )

                tool_messages = await asyncio.gather(*[
                    run_tool(tc) for tc in response.tool_calls
                ])

                messages.extend(tool_messages)
            finally:
                var_child_runnable_config.reset(_config_token)

        self._collect_user_source_lookup_ids(messages)

        final_message = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            messages[-1],
        )
        try:
            return final_message | StructResponse(RelationSearchResult)
        except Exception as e:
            logger.debug(
                "[RelationSearch] LLM final message parsing failed, "
                "falling back to tool-call extraction", exc_info=True
            )
            if self.on_error is not None:
                self.on_error(
                    MemoryRetrievalBusinessError.structured_result_parse_failed(
                        MemoryRetrievalStage.RELATION_SEARCH,
                        e,
                        model_type=MemoryModelType.LLM,
                    )
                )
            return self._extract_pairs_from_messages(messages)

    @staticmethod
    def _extract_pairs_from_messages(
            messages: list[SystemMessage | HumanMessage | AIMessage | ToolMessage],
    ) -> RelationSearchResult:
        """Extract EntityPairs from relation_search_tool results when LLM output is unusable."""
        import ast

        tool_results: dict[str, list[dict]] = {}
        for msg in messages:
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                try:
                    tool_results[msg.tool_call_id] = ast.literal_eval(msg.content)
                except (ValueError, SyntaxError):
                    tool_results[msg.tool_call_id] = []

        pairs: list[EntityPair] = []
        seen = set()
        for msg in messages:
            if not isinstance(msg, AIMessage) or not msg.tool_calls:
                continue
            for tc in msg.tool_calls:
                if tc["name"] != "relation_search_tool":
                    continue
                source_id = str(tc.get("args", {}).get("source_id") or "__user__")
                results = tool_results.get(tc["id"], [])
                for item in results:
                    target_id = str(item.get("id", ""))
                    if not target_id:
                        continue
                    key = (source_id, target_id)
                    if key not in seen:
                        seen.add(key)
                        pairs.append(EntityPair(source_id=source_id, target_id=target_id))

        return RelationSearchResult(pairs=pairs)

    def _collect_user_source_lookup_ids(
            self,
            messages: list[SystemMessage | HumanMessage | AIMessage | ToolMessage],
    ) -> None:
        """从 agent 消息历史中收集 user_source_lookup_tool 调用过的 entity_ids。"""
        looked_up: set[str] = set()
        for msg in messages:
            if not isinstance(msg, AIMessage) or not msg.tool_calls:
                continue
            for tc in msg.tool_calls:
                if tc.get("name") != "user_source_lookup_tool":
                    continue
                entity_ids = tc.get("args", {}).get("entity_ids", [])
                if isinstance(entity_ids, list):
                    looked_up.update(entity_ids)
        self._user_source_looked_up_ids = looked_up

    async def _fetch_relation_data(
            self,
            pairs: list[EntityPair]
    ) -> tuple[list[dict], list[dict]]:
        user_entity_id = await get_user_entity_id(self.ctx.end_user_id) or ""
        batch_pairs = [
            {
                "source_id": (
                    user_entity_id
                    if pair.source_id == "__user__"
                    else pair.source_id
                ),
                "target_id": pair.target_id,
            }
            for pair in pairs
        ]
        relation_records = await get_entity_pair_relations(
            self.ctx.end_user_id,
            batch_pairs,
        )

        all_entity_ids = {user_entity_id}
        for record in relation_records:
            all_entity_ids.add(record.get("source_id", ""))
            all_entity_ids.add(record.get("target_id", ""))
        for pair in pairs:
            source_id = (
                user_entity_id
                if pair.source_id == "__user__"
                else pair.source_id
            )
            all_entity_ids.add(source_id)
            all_entity_ids.add(pair.target_id)
        all_entity_ids.discard("")

        entity_records = await get_active_entities_by_ids(
            list(all_entity_ids)
        )
        return relation_records, entity_records

    @staticmethod
    def _build_relation_memories(
            relation_records: list[dict],
            entity_records: list[dict]
    ) -> list[RelationMemory]:
        name_map = {r.get("id", ""): r.get("name", "") for r in entity_records}
        desc_map = {r.get("id", ""): r.get("description", "") for r in entity_records}

        relations = []
        seen = set()
        for rec in relation_records:
            rel_key = (
                str(rec.get("source_id", "")),
                str(rec.get("relation_predicate", "")),
                str(rec.get("target_id", ""))
            )
            if rel_key in seen:
                continue
            seen.add(rel_key)
            relations.append(RelationMemory(
                source=name_map.get(rec.get("source_id", ""), rec.get("source_name", "")),
                relation=str(rec.get("relation_predicate", "")),
                target=name_map.get(rec.get("target_id", ""), rec.get("target_name", "")),
                target_desc=desc_map.get(rec.get("target_id", ""), ""),
                target_id=str(rec.get("target_id", "")),
            ))

        return relations

    async def relation_search(
            self,
            query: str
    ) -> MemorySearchResult:
        result = await self._run_relation_agent(query)

        if not result.pairs:
            return MemorySearchResult(memories=[], relations=[])

        relation_records, entity_records = await self._fetch_relation_data(result.pairs)
        relations = self._build_relation_memories(relation_records, entity_records)

        if self._user_source_looked_up_ids:
            try:
                user_source_map = await self.fetch_user_sources_for_entities(self._user_source_looked_up_ids)
            except Exception as e:
                logger.warning(f"[RelationSearch] UserSource 回溯失败: {e}")
                user_source_map = {}
            for rel_memory in relations:
                if rel_memory.target_id in user_source_map:
                    source_text = user_source_map[rel_memory.target_id]
                    rel_memory.target_desc = (
                        f"{rel_memory.target_desc}\n"
                        f"<original-context>{source_text}</original-context>"
                    )

        logger.info(f"[RelationSearch] resolved {len(relations)} relations from {len(result.pairs)} pairs")
        return MemorySearchResult(memories=[], relations=relations)

    async def memory_l0(self) -> Memory:
        end_user_id = self.ctx.end_user_id
        user_meta = await get_user_metadata(end_user_id)
        metadata = MetadataBuilder(user_meta)
        return Memory(
            score=1,
            source=Neo4jNodeType.EXTRACTEDENTITY,
            query='',
            id=end_user_id,
            content=metadata.content,
            data=metadata.data,
        )

    async def fetch_user_sources_for_entities(
            self,
            entity_ids: set[str],
    ) -> dict[str, str]:
        """给定一组 ExtractedEntity ID，查询其 HAS_ORIGINAL_CONTENT 边，返回
        {entity_id: original_text} 映射。

        若一个 entity 对应多个 UserSource（多次提及），合并所有 original_text。
        """
        if not entity_ids:
            return {}

        records = await get_user_sources_for_entities(
            self.ctx.end_user_id,
            list(entity_ids),
        )

        result: dict[str, list[str]] = {}
        for rec in records:
            eid = rec.get("entity_id", "")
            text = rec.get("original_text", "")
            if not eid or not text:
                continue
            if eid not in result:
                result[eid] = []
            result[eid].append(text)

        return {eid: "\n---\n".join(texts) for eid, texts in result.items()}

    async def resolve_perceptual_content(
            self,
            query: str,
            perceptual_memories: list[Memory],
            llm: RedBearLLM,
    ) -> list[Memory]:
        """对 Perceptual 类型的记忆调用多模态模型解析实际文件内容。

        增强模型上下文，并单独保留公开展示文本（不改变 score / id / source）。
        失败时保留原 summary，不中断主流程。
        """
        enhanced = []
        for mem in perceptual_memories:
            if mem.source != Neo4jNodeType.PERCEPTUAL:
                enhanced.append(mem)
                continue

            file_path = mem.data.get("file_path", "")
            file_name = mem.data.get("file_name", "")
            file_type = mem.data.get("file_type", "")
            perceptual_type = mem.data.get("perceptual_type", "")

            if not file_path:
                logger.debug("[Perceptual] 跳过无 file_path 的 Perceptual 记忆")
                enhanced.append(mem)
                continue

            try:
                parsed = await self._call_multimodal_for_query(
                    file_path, file_name, file_type, perceptual_type, query, llm,
                    on_error=self.on_error,
                )

                display_content = parsed.strip() if isinstance(parsed, str) else ""
                if display_content:
                    mem.data["_perceptual_display_content"] = display_content
                    # 完整结构继续供后续总结和最终回答使用，不直接投影给前端。
                    mem.content = (
                        f"<history-file-input>\n"
                        f"<file-name>{file_name}</file-name>\n"
                        f"<file-summary>{mem.data.get('summary', '')}</file-summary>\n"
                        f"<file-analysis>{display_content}</file-analysis>\n"
                        f"</history-file-input>\n"
                    )
            except Exception as e:
                logger.warning(
                    f"[Perceptual] 多模态解析失败 file={file_name}: {e}，回退使用 summary"
                )

            enhanced.append(mem)

        return enhanced

    @staticmethod
    async def _call_multimodal_for_query(
            file_path: str,
            file_name: str,
            file_type: str,
            perceptual_type: str | int,
            query: str,
            llm: RedBearLLM,
            on_error: Callable[[MemoryRetrievalBusinessError], None] | None = None,
    ) -> str:
        """调用多模态 LLM，让模型针对 query 解析文件内容。

        支持图片（VISION）和文档（TEXT/DOCUMENT）类型。
        返回模型对文件的针对性分析文本。
        """
        # 类型和文件类别必须同时匹配，避免将视频作为图片发送给模型。
        if isinstance(perceptual_type, bool):
            return ""
        if isinstance(perceptual_type, int):
            perceptual_type_int = perceptual_type
        elif isinstance(perceptual_type, str) and perceptual_type.strip().isdigit():
            perceptual_type_int = int(perceptual_type.strip())
        else:
            return ""
        normalized_file_type = str(file_type or "").strip().lower()
        if perceptual_type_int == 1 and normalized_file_type == FileType.IMAGE.value:
            file_input_type = FileType.IMAGE
        elif perceptual_type_int == 3 and normalized_file_type == FileType.DOCUMENT.value:
            file_input_type = FileType.DOCUMENT
        else:
            return ""

        from app.services.multimodal_service import MultimodalService

        file_input = FileInput(
            type=file_input_type,
            transfer_method=TransferMethod.REMOTE_URL,
            url=file_path,
            file_type=file_type or "",
        )

        # 使用 MultimodalService 格式化文件内容
        multimodal_svc = MultimodalService(
            db=None,
            api_config=llm.get_config(),
        )
        formatted = await multimodal_svc.process_files(
            files=[file_input],
            document_image_recognition=True,
            # 公开感知结果不能包含文件处理异常；失败时返回空列表并回退已有 summary。
            include_processing_errors=False,
        )

        if not formatted:
            return ""

        # 构造 prompt：让模型关注 query 相关的文件内容
        prompt = (
            f"请根据以下问题，分析文件 '{file_name}' 中相关的内容：\n\n"
            f"问题：{query}\n\n"
            f"请仅提取与问题直接相关的信息，以简洁的要点形式回答。"
        )

        # 调用 LLM（支持多模态输入）
        try:
            response = await llm.ainvoke([HumanMessage(content=[
                {"type": "text", "text": prompt},
                *formatted,
            ])])
        except Exception as e:
            if on_error is not None:
                on_error(MemoryRetrievalBusinessError.model_call_failed(
                    MemoryRetrievalStage.PERCEPTUAL_ANALYSIS,
                    e,
                    model_type=MemoryModelType.LLM,
                    impact=MemoryRetrievalImpact.INCOMPLETE,
                ))
            raise

        return response.content if hasattr(response, 'content') else str(response)


class RAGSearchService:
    def __init__(self, ctx: MemoryContext):
        self.ctx = ctx

    async def keyword_search(self, query: str, limit: int = 10) -> MemorySearchResult:
        """RAG 不支持纯全文检索，回退到 hybrid_search。"""
        return await self.hybrid_search(query, limit)

    async def get_kb_config(self, db: AsyncSession, limit: int) -> dict:
        if self.ctx.user_rag_memory_id is None:
            raise RuntimeError("Knowledge base ID not specified")
        knowledge_config = await knowledge_repository.get_knowledge_by_id_async(
            db,
            knowledge_id=uuid.UUID(self.ctx.user_rag_memory_id)
        )
        if knowledge_config is None:
            raise RuntimeError("Knowledge base not exist")
        reranker_id = knowledge_config.reranker_id

        return {
            "knowledge_bases": [
                {
                    "kb_id": self.ctx.user_rag_memory_id,
                    "similarity_threshold": 0.7,
                    "vector_similarity_weight": 0.5,
                    "top_k": limit,
                    "retrieve_type": "participle"
                }
            ],
            "merge_strategy": "weight",
            "reranker_id": reranker_id,
            "reranker_top_k": limit
        }

    async def hybrid_search(self, query: str, limit: int) -> MemorySearchResult:
        try:
            async with get_async_db_context() as db:
                kb_config = await self.get_kb_config(db, limit)
        except RuntimeError as e:
            logger.error(f"[MemorySearch] get_kb_config error: {self.ctx.user_rag_memory_id} - {e}")
            return MemorySearchResult(memories=[])
        retrieve_chunks_result = knowledge_retrieval(query, kb_config, [self.ctx.end_user_id])
        res = []
        try:
            for chunk in retrieve_chunks_result:
                memory = Memory(
                    content=chunk.page_content,
                    query=query,
                    score=chunk.metadata.get("score", 0.0),
                    source=Neo4jNodeType.RAG,
                    id=chunk.metadata.get("document_id"),
                    data=chunk.metadata,
                )
                memory.retrieval_trace = build_score_trace(
                    node_id=memory.id,
                    node_type=Neo4jNodeType.RAG.value,
                    final_score=memory.score,
                    rank_basis="provider_score",
                    backend="rag",
                    matched_queries=[query],
                )
                res.append(memory)
            res.sort(key=lambda x: x.score, reverse=True)
            res = res[:limit]
            return MemorySearchResult(
                memories=res,
                execution_trace=RetrievalExecutionTrace(
                    backend="rag",
                    keyword_status="skipped",
                    semantic_status="completed",
                    rerank_status="skipped",
                    semantic_hit_count=len(res),
                    raw_hit_count=len(res),
                    merged_count=len(res),
                ),
            )
        except RuntimeError as e:
            logger.error(f"[MemorySearch] rag search error: {e}")
            return MemorySearchResult(memories=[])

    async def relation_search(self, query: str) -> MemorySearchResult:
        logger.info("RAG does not support relation search")
        return MemorySearchResult(memories=[])


class HistorySearchService:
    def __init__(self, ctx: MemoryContext):
        self.ctx = ctx

    async def run(self) -> MemorySearchResult:
        async with get_async_db_context() as db:
            conv_result = await db.execute(
                select(Conversation).where(
                    Conversation.user_id == self.ctx.end_user_id,
                    Conversation.id != self.ctx.conversation_id,
                    Conversation.app_id != "00000000-0000-0000-0000-000000000001",
                ).order_by(
                    Conversation.updated_at.desc()
                ).limit(1)
            )
            conversation: Conversation | None = conv_result.scalars().first()

            if conversation is None:
                return MemorySearchResult(memories=[])

            cursor = conversation.write_cursor
            msg_result = await db.execute(
                select(MemoryMessage).where(
                    MemoryMessage.conversation_id == conversation.id,
                    MemoryMessage.message_seq > cursor,
                ).order_by(MemoryMessage.message_seq)
            )
            messages: list[MemoryMessage] = list(msg_result.scalars().all())

            if not messages:
                return MemorySearchResult(memories=[])

            messages_lst = []
            for message in messages:
                message_dict = {
                    "role": message.role,
                    "content": message.content,
                    "files": message.files,
                }
                messages_lst.append(message_dict)
            memory = Memory(
                content='\n'.join([
                    f'{_["role"]}:{_['content']}'
                    for _ in messages_lst
                ]),
                source=Neo4jNodeType.HISTORY,
                query="",
                id=str(conversation.id),
                data={"messages": messages_lst},
            )
        return MemorySearchResult(memories=[memory])


class MetaSearchService:
    def __init__(self, ctx: MemoryContext):
        self.ctx = ctx

    async def run(self) -> MemorySearchResult:
        if self.ctx.storage_type == StorageType.RAG:
            return MemorySearchResult(memories=[])
        else:
            end_user_id = self.ctx.end_user_id
            user_meta = await get_user_metadata(end_user_id)
            metadata = MetadataBuilder(user_meta)
            memory = Memory(
                score=1,
                source=Neo4jNodeType.EXTRACTEDENTITY,
                query='',
                id=end_user_id,
                content=metadata.content,
                data=metadata.data,
            )
            return MemorySearchResult(memories=[memory])
