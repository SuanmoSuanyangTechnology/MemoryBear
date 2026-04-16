import asyncio
import logging
import math

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.memory_service import MemoryContext
from app.core.memory.models.service_models import Memory, MemorySearchResult
from app.core.memory.read_services.result_builder import data_builder_factory
from app.core.models import RedBearEmbeddings
from app.repositories.neo4j.graph_search import search_graph, search_graph_by_embedding
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)

DEFAULT_ALPHA = 0.6
DEFAULT_FULLTEXT_SCORE_THRESHOLD = 1.5
DEFAULT_COSINE_SCORE_THRESHOLD = 0.5
DEFAULT_CONTENT_SCORE_THRESHOLD = 0.5


class Neo4jSearchService:
    def __init__(
            self,
            ctx: MemoryContext,
            embedder: RedBearEmbeddings,
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
        self.connector: Neo4jConnector | None = None

        self.includes = includes
        if includes is None:
            self.includes = [
                Neo4jNodeType.STATEMENT,
                Neo4jNodeType.CHUNK,
                Neo4jNodeType.EXTRACTEDENTITY,
                Neo4jNodeType.MEMORYSUMMARY,
                Neo4jNodeType.PERCEPTUAL,
                Neo4jNodeType.COMMUNITY
            ]

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
        embedding_results = embedding_results

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

        logger.info(
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

    async def search(
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
                    query=query
                ))
        memories.sort(key=lambda x: x.score, reverse=True)
        return MemorySearchResult(memories=memories[:limit])


class RAGSearchService:
    def __init__(self, ctx: MemoryContext):
        pass

    async def search(self) -> MemorySearchResult:
        pass
