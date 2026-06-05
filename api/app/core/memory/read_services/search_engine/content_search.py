import asyncio
import json
import logging
import math
import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

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
from app.core.memory.read_services.search_engine.tools import make_entity_search_tool, make_relation_search_tool
from app.core.memory.utils.llm.llm_utils import StructResponse
from app.core.models import RedBearEmbeddings, RedBearLLM
from app.core.rag.nlp.search import knowledge_retrieval
from app.models import Conversation, MemoryMessage
from app.repositories import knowledge_repository
from app.repositories.neo4j.graph_search import get_nodes_by_ids, get_relations_between_entity_pairs, search_graph, \
    search_graph_by_embedding
from app.repositories.neo4j.graph_search import search_user_metadata
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)

DEFAULT_ALPHA = 0.6
DEFAULT_FULLTEXT_SCORE_THRESHOLD = 1.5
DEFAULT_COSINE_SCORE_THRESHOLD = 0.5
DEFAULT_CONTENT_SCORE_THRESHOLD = 0.5

RELATIONSHIP_LOOP_LIMIT = 6


class Neo4jSearchService:
    def __init__(
            self,
            ctx: MemoryContext,
            embedder: RedBearEmbeddings,
            llm: RedBearLLM,
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

        self.embedder: RedBearEmbeddings = embedder
        self.llm: RedBearLLM = llm
        self.connector: Neo4jConnector | None = None

        self.includes = includes
        if includes is None:
            self.includes = [
                Neo4jNodeType.STATEMENT,
                # Neo4jNodeType.CHUNK,
                Neo4jNodeType.EXTRACTEDENTITY,
                Neo4jNodeType.MEMORYSUMMARY,
                Neo4jNodeType.PERCEPTUAL,
                # Neo4jNodeType.COMMUNITY
            ]

        self.relation_search_tool = make_relation_search_tool(self.ctx)
        self.entity_search_tool = make_entity_search_tool(self.ctx)

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

    def _normalize_kw_scores(self, items: list[dict]) -> list[dict]:
        if not items:
            return items
        scores = [float(it.get("score", 0) or 0) for it in items]
        for it, s in zip(items, scores):
            it[f"normalized_kw_score"] = 1 / (1 + math.exp(-(s - self.fulltext_score_threshold) / 2)) if s else 0
        return items

    async def hybrid_search(
            self,
            query: str,
            limit: int = 10,
    ) -> MemorySearchResult:
        async with Neo4jConnector() as connector:
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
        return MemorySearchResult(memories=memories[:limit])

    async def _run_relation_agent(self, query: str) -> RelationSearchResult:
        system_prompt = prompt_manager.render(
            name="relation_search",
            predicates=[p.to_dict() for p in TripletPredicate],
            loop_limit=RELATIONSHIP_LOOP_LIMIT - 1
        )

        tools = [self.relation_search_tool, self.entity_search_tool]
        tool_map = {t.name: t for t in tools}
        llm_with_tools = self.llm.bind_tools(tools)

        messages: list[SystemMessage | HumanMessage | AIMessage | ToolMessage] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=(
                f"<user-query>{query}</user-query>"
            ))
        ]

        for _ in range(RELATIONSHIP_LOOP_LIMIT):
            response: AIMessage = await llm_with_tools.ainvoke(messages)
            messages.append(response)

            if not response.tool_calls:
                break

            async def run_tool(tc):
                tool = tool_map[tc["name"]]
                try:
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

        final_message = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            messages[-1],
        )
        try:
            return final_message | StructResponse(mode="pydantic", model=RelationSearchResult)
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

    async def _fetch_relation_data(
            self,
            pairs: list[EntityPair]
    ) -> tuple[list[dict], list[dict]]:
        async with Neo4jConnector() as connector:
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

        logger.info(f"[RelationSearch] resolved {len(relations)} relations from {len(result.pairs)} pairs")
        return MemorySearchResult(memories=[], relations=relations)

    async def memory_l0(self) -> Memory:
        async with Neo4jConnector() as connector:
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


class RAGSearchService:
    def __init__(self, ctx: MemoryContext, db: Session):
        self.ctx = ctx
        self.db = db

    def get_kb_config(self, limit: int) -> dict:
        if self.ctx.user_rag_memory_id is None:
            raise RuntimeError("Knowledge base ID not specified")
        knowledge_config = knowledge_repository.get_knowledge_by_id(
            self.db,
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
            kb_config = self.get_kb_config(limit)
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
    def __init__(self, ctx: MemoryContext, db: Session):
        self.ctx = ctx
        self.db = db

    async def run(self) -> MemorySearchResult:
        conversation: Conversation | None = self.db.scalar(
            select(Conversation).where(
                Conversation.user_id == self.ctx.end_user_id
            ).order_by(
                Conversation.updated_at.desc()
            ).offset(1).limit(1)
        )

        if conversation is None:
            return MemorySearchResult(memories=[])

        cursor = conversation.write_cursor
        messages: list[MemoryMessage] | None = list(self.db.scalars(
            select(MemoryMessage).where(
                MemoryMessage.conversation_id == conversation.id,
                MemoryMessage.message_seq > cursor
            ).order_by(MemoryMessage.message_seq)
        ))
        if messages is None:
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
            data={"messages": messages_lst}
        )
        return MemorySearchResult(memories=[memory])


class MetaSearchService:
    def __init__(self, ctx: MemoryContext, db: Session):
        self.ctx = ctx
        self.db = db

    async def run(self) -> MemorySearchResult:
        if self.ctx.storage_type == StorageType.RAG:
            return MemorySearchResult(memories=[])
        else:
            async with Neo4jConnector() as connector:
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
