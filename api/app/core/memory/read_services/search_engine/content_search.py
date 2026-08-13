import asyncio
import json
import logging
import math
import uuid

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig, var_child_runnable_config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memory.enums import Neo4jNodeType, TripletPredicate, StorageType
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
from app.core.models import RedBearEmbeddings, RedBearLLM, RedBearRerank
from app.core.models.llm import StructResponse
from app.core.rag.nlp.search import knowledge_retrieval
from app.db import get_async_db_context
from app.models import Conversation, MemoryMessage
from app.repositories import knowledge_repository
from app.repositories.neo4j.cypher_queries import FETCH_USER_SOURCES_FOR_ENTITIES
from app.repositories.neo4j.graph_search import get_nodes_by_ids, get_relations_between_entity_pairs, search_graph, \
    search_graph_by_embedding
from app.repositories.neo4j.graph_search import search_user_metadata
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
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
            includes: list[Neo4jNodeType] | None = None,
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
        self.connector: Neo4jConnector | None = None

        self.includes = includes
        if includes is None:
            self.includes = [
                Neo4jNodeType.STATEMENT,
                # Neo4jNodeType.DIALOGUE,
                Neo4jNodeType.CHUNK,
                Neo4jNodeType.EXTRACTEDENTITY,
                Neo4jNodeType.MEMORYSUMMARY,
                Neo4jNodeType.PERCEPTUAL,
                Neo4jNodeType.DIALOGUE,
                # Neo4jNodeType.COMMUNITY
            ]

        self.relation_search_tool = make_relation_search_tool(self.ctx)
        self.entity_search_tool = make_entity_search_tool(self.ctx)
        self.user_source_lookup_tool = make_user_source_lookup_tool(self.ctx)
        self._user_source_looked_up_ids = set()

    async def _keyword_search(
            self,
            query: str,
            limit: int
    ):
        return await search_graph(
            connector=self.connector,
            query=query,
            end_user_id=self.ctx.end_user_id,
            limit=limit,
            include=self.includes
        )

    async def _embedding_search(self, query, limit):
        return await search_graph_by_embedding(
            connector=self.connector,
            embedder_client=self.embedder,
            query_text=query,
            end_user_id=self.ctx.end_user_id,
            limit=limit,
            include=self.includes
        )

    def _rerank(
            self,
            keyword_results: list[dict],
            embedding_results: list[dict],
            limit: int,
    ) -> list[dict]:
        keyword_results = self._normalize_kw_scores(keyword_results)

        kw_norm_map = {}
        for item in keyword_results:
            item_id = item["id"]
            kw_norm_map[item_id] = float(item.get("normalized_kw_score", 0))

        emb_norm_map = {}
        for item in embedding_results:
            item_id = item["id"]
            emb_norm_map[item_id] = float(item.get("score", 0))

        combined = {}
        for item in keyword_results:
            item_id = item["id"]
            combined[item_id] = item.copy()
            combined[item_id]["kw_score"] = kw_norm_map.get(item_id, 0)
            combined[item_id]["embedding_score"] = emb_norm_map.get(item_id, 0)

        for item in embedding_results:
            item_id = item["id"]
            if item_id in combined:
                combined[item_id]["embedding_score"] = emb_norm_map.get(item_id, 0)
            else:
                combined[item_id] = item.copy()
                combined[item_id]["kw_score"] = kw_norm_map.get(item_id, 0)
                combined[item_id]["embedding_score"] = emb_norm_map.get(item_id, 0)

        for item in combined.values():
            item_id = item["id"]
            kw = float(combined[item_id].get("kw_score", 0) or 0)
            emb = float(combined[item_id].get("embedding_score", 0) or 0)
            base = self.alpha * emb + (1 - self.alpha) * kw
            combined[item_id]["content_score"] = base + min(1 - base, 0.1 * kw * emb)
        results = sorted(combined.values(), key=lambda x: x["content_score"], reverse=True)
        # results = [
        #     res for res in results
        #     if res["content_score"] > self.content_score_threshold
        # ]
        results = results[:limit]

        logger.debug(
            f"[MemorySearch] rerank: merged={len(combined)}, after_threshold={len(results)} "
            f"(alpha={self.alpha})"
        )
        return results

    async def _hybrid_search_with_model_rerank(
            self,
            kw_results: dict,
            emb_results: dict,
            query: str,
            limit: int,
    ) -> list[Memory]:
        seen: dict[str, dict] = {}
        for node_type in self.includes:
            for record in kw_results.get(node_type, []):
                rid = record.get("id", "")
                if rid and rid not in seen:
                    record["_node_type"] = node_type
                    record["_source"] = "keyword"
                    seen[rid] = record
            for record in emb_results.get(node_type, []):
                rid = record.get("id", "")
                if rid and rid not in seen:
                    record["_node_type"] = node_type
                    record["_source"] = "embedding"
                    seen[rid] = record

        if not seen:
            return []

        memories: list[Memory] = []
        for record in seen.values():
            node_type = record.pop("_node_type")
            memory = data_builder_factory(node_type, record)
            memories.append(Memory(
                score=memory.score,
                content=memory.content,
                data=memory.data,
                source=node_type,
                query=query,
                id=memory.id,
            ))

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
                reranked = await asyncio.to_thread(
                    self.reranker.compress_documents,
                    documents,
                    query,
                    top_n=min(limit, len(documents))
                )
            index_to_score = {
                doc.metadata["index"]: doc.metadata.get("relevance_score", 0.0)
                for doc in reranked
            }
            for i, mem in enumerate(memories):
                if i in index_to_score:
                    mem.score = index_to_score[i]

            memories.sort(key=lambda x: x.score, reverse=True)
            memories = memories[:limit]

            logger.info(
                f"[Neo4jSearch] Model rerank applied: {len(documents)} → {len(memories)} memories"
            )
        except Exception as e:
            logger.warning(
                f"[Neo4jSearch] Model rerank failed, falling back to content_score: {e}",
                exc_info=True,
            )
            memories.sort(key=lambda x: x.score, reverse=True)
            memories = memories[:limit]

        return memories

    def _normalize_kw_scores(self, items: list[dict]) -> list[dict]:
        if not items:
            return items
        scores = [float(it.get("score", 0) or 0) for it in items]
        for it, s in zip(items, scores):
            it[f"normalized_kw_score"] = 1 / (1 + math.exp(-(s - self.fulltext_score_threshold) / 2)) if s else 0
        return items

    async def keyword_search(
            self,
            query: str,
            limit: int = 10,
    ) -> MemorySearchResult:
        """仅全文检索，不做 embedding / rerank / 关系检索。"""
        async with Neo4jConnector(shared_driver=True) as connector:
            self.connector = connector
            kw_results = await self._keyword_search(query, limit)

        all_records = []
        for node_type in self.includes:
            for record in kw_results.get(node_type, []):
                record["_node_type"] = node_type
                all_records.append(record)

        if not all_records:
            return MemorySearchResult(memories=[])

        all_records = self._normalize_kw_scores(all_records)

        for r in all_records:
            cs = float(r.get("normalized_kw_score", 0))
            r["content_score"] = cs
            r["score"] = cs

        all_records.sort(key=lambda x: x["score"], reverse=True)

        memories = []
        for record in all_records[:limit]:
            node_type = record.pop("_node_type")
            memory = data_builder_factory(node_type, record)
            memories.append(Memory(
                score=memory.score,
                content=memory.content,
                data=memory.data,
                source=node_type,
                query=query,
                id=memory.id
            ))
        return MemorySearchResult(memories=memories)

    async def hybrid_search(
            self,
            query: str,
            limit: int = 10,
    ) -> MemorySearchResult:
        async with Neo4jConnector(shared_driver=True) as connector:
            self.connector = connector
            kw_task = self._keyword_search(query, limit)
            emb_task = self._embedding_search(query, limit)
            kw_results, emb_results = await asyncio.gather(kw_task, emb_task, return_exceptions=True)

        if isinstance(kw_results, Exception):
            logger.warning(f"[MemorySearch] keyword search error: {kw_results}")
            kw_results = {}
        if isinstance(emb_results, Exception):
            logger.warning(f"[MemorySearch] embedding search error: {emb_results}")
            emb_results = {}

        if self.reranker is not None:
            memories = await self._hybrid_search_with_model_rerank(
                kw_results, emb_results, query, limit
            )
        else:
            memories = []
            for node_type in self.includes:
                reranked = self._rerank(
                    kw_results.get(node_type, []),
                    emb_results.get(node_type, []),
                    limit
                )
                for record in reranked:
                    memory = data_builder_factory(node_type, record)
                    memories.append(Memory(
                        score=memory.score,
                        content=memory.content,
                        data=memory.data,
                        source=node_type,
                        query=query,
                        id=memory.id
                    ))
            memories.sort(key=lambda x: x.score, reverse=True)
            memories = memories[:limit]

        return MemorySearchResult(memories=memories)

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
                response: AIMessage = await llm_with_tools.ainvoke(messages)
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
        except Exception:
            logger.debug(
                "[RelationSearch] LLM final message parsing failed, "
                "falling back to tool-call extraction", exc_info=True
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
        async with Neo4jConnector(shared_driver=True) as connector:
            user_meta = await search_user_metadata(connector, self.ctx.end_user_id)
            user_entity_id = user_meta.get("id", "")

            batch_pairs = [
                {"source_id": user_entity_id if p.source_id == "__user__" else p.source_id,
                 "target_id": p.target_id}
                for p in pairs
            ]
            relation_records = await get_relations_between_entity_pairs(
                connector,
                self.ctx.end_user_id,
                batch_pairs,
            )

            all_entity_ids = {user_entity_id}
            for rec in relation_records:
                all_entity_ids.add(rec.get("source_id", ""))
                all_entity_ids.add(rec.get("target_id", ""))
            for pair in pairs:
                sid = user_entity_id if pair.source_id == "__user__" else pair.source_id
                all_entity_ids.add(sid)
                all_entity_ids.add(pair.target_id)
            all_entity_ids.discard("")

            entity_records = await get_nodes_by_ids(
                connector,
                Neo4jNodeType.EXTRACTEDENTITY,
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
        async with Neo4jConnector(shared_driver=True) as connector:
            end_user_id = self.ctx.end_user_id
            user_meta = await search_user_metadata(connector, end_user_id)
            metadata = MetadataBuilder(user_meta)
            memory = Memory(
                score=1,
                source=Neo4jNodeType.EXTRACTEDENTITY,
                query='',
                id=end_user_id,
                content=metadata.content,
                data=metadata.data,
            )

        return memory

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

        async with Neo4jConnector(shared_driver=True) as connector:
            records = await connector.execute_query(
                FETCH_USER_SOURCES_FOR_ENTITIES,
                entity_ids=list(entity_ids),
                end_user_id=self.ctx.end_user_id,
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
                    file_path, file_name, file_type, perceptual_type, query, llm
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
        response = await llm.ainvoke([HumanMessage(content=[
            {"type": "text", "text": prompt},
            *formatted,
        ])])

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
                res.append(Memory(
                    content=chunk.page_content,
                    query=query,
                    score=chunk.metadata.get("score", 0.0),
                    source=Neo4jNodeType.RAG,
                    id=chunk.metadata.get("document_id"),
                    data=chunk.metadata,
                ))
            res.sort(key=lambda x: x.score, reverse=True)
            res = res[:limit]
            return MemorySearchResult(memories=res)
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
            async with Neo4jConnector(shared_driver=True) as connector:
                end_user_id = self.ctx.end_user_id
                user_meta = await search_user_metadata(connector, end_user_id)
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
