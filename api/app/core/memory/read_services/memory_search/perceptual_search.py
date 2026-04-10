import asyncio
import logging
from typing import Any

from pydantic import BaseModel

from app.core.memory.llm_tools import OpenAIEmbedderClient
from app.core.memory.memory_service import MemoryContext
from app.core.memory.utils.data import escape_lucene_query
from app.repositories.neo4j.graph_search import search_perceptual, search_perceptual_by_embedding
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


class PerceptualResult(BaseModel):
    memories: list[dict[str, Any]] = []
    content: str = ""
    keyword_raw: int = 0
    embedding_raw: int = 0


class PerceptualRetrieverService:
    DEFAULT_ALPHA = 0.6
    DEFAULT_FULLTEXT_SCORE_THRESHOLD = 0.5
    DEFAULT_COSINE_SCORE_THRESHOLD = 0.7

    def __init__(
            self,
            ctx: MemoryContext,
            embedder: OpenAIEmbedderClient,
            alpha: float = DEFAULT_ALPHA,
            fulltext_score_threshold: float = DEFAULT_FULLTEXT_SCORE_THRESHOLD,
            cosine_score_threshold: float = DEFAULT_COSINE_SCORE_THRESHOLD
    ):
        self.ctx = ctx
        self.alpha = alpha
        self.fulltext_score_threshold = fulltext_score_threshold
        self.cosine_score_threshold = cosine_score_threshold

        self.embedder: OpenAIEmbedderClient = embedder
        self.connector = Neo4jConnector()

    async def search(
            self,
            query: str,
            keywords: list[str] | None = None,
            limit: int = 10
    ) -> PerceptualResult:
        if keywords is None:
            keywords = [query] if query else []

        try:
            kw_task = self._keyword_search(keywords, limit)
            emb_task = self._embedding_search(query, limit)
            kw_results, emb_results = await asyncio.gather(kw_task, emb_task, return_exceptions=True)
            if isinstance(kw_results, Exception):
                logger.warning(f"[PerceptualSearch] keyword search error: {kw_results}")
                kw_results = []
            if isinstance(emb_results, Exception):
                logger.warning(f"[PerceptualSearch] embedding search error: {emb_results}")
                emb_results = []

            reranked = self._rerank(kw_results, emb_results, limit)

            memories = []
            content_parts = []
            for record in reranked:
                fmt = self._format_result(record)
                fmt["score"] = round(record.get("content_score", 0), 4)
                memories.append(fmt)
                content_parts.append(self._build_content_text(fmt))

            logger.info(
                f"[PerceptualSearch] {len(memories)} results after rerank "
                f"(keyword_raw={len(kw_results)}, embedding_raw={len(emb_results)})"
            )
            return PerceptualResult(
                memories=memories,
                content="\n\n".join(content_parts),
                keyword_raw=len(kw_results),
                embedding_raw=len(emb_results),
            )
        except Exception as e:
            logger.error(f"[PerceptualSearch] search failed: {e}", exc_info=True)
            return PerceptualResult()
        finally:
            await self.connector.close()

    async def _keyword_search(
            self,
            keywords: list[str],
            limit: int
    ) -> list[dict]:
        seen_ids: set = set()
        all_results: list[dict] = []

        async def _one(kw: str):
            escaped = escape_lucene_query(kw)
            if not escaped.strip():
                return []
            r = await search_perceptual(
                connector=self.connector, q=escaped,
                end_user_id=self.ctx.end_user_id, limit=limit
            )
            perceptuals = r.get("perceptuals", [])
            return [perceptual for perceptual in perceptuals if perceptual["score"] > self.fulltext_score_threshold]

        tasks = [_one(kw) for kw in keywords]
        batch = await asyncio.gather(*tasks, return_exceptions=True)

        for result in batch:
            if isinstance(result, Exception):
                logger.warning(f"[PerceptualSearch] keyword sub-query error: {result}")
                continue
            for rec in result:
                rid = rec.get("id", "")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    all_results.append(rec)
        all_results.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
        return all_results[:limit]

    async def _embedding_search(
            self,
            query: str,
            limit: int
    ) -> list[dict]:
        r = await search_perceptual_by_embedding(
            connector=self.connector,
            embedder_client=self.embedder,
            query_text=query,
            end_user_id=self.ctx.end_user_id,
            limit=limit
        )
        perceptuals = r.get("perceptuals", [])
        return [perceptual for perceptual in perceptuals if perceptual["score"] > self.cosine_score_threshold]

    def _rerank(
            self,
            keyword_results: list[dict],
            embedding_results: list[dict],
            limit: int,
    ) -> list[dict]:
        keyword_results = self._normalize_scores(keyword_results)
        embedding_results = self._normalize_scores(embedding_results)

        kw_norm_map = {}
        for item in keyword_results:
            item_id = item["id"]
            kw_norm_map[item_id] = float(item.get("normalized_score", 0))

        emb_norm_map = {}
        for item in embedding_results:
            item_id = item["id"]
            emb_norm_map[item_id] = float(item.get("normalized_score", 0))

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
            kw = float(item.get("kw_score", 0) or 0)
            emb = float(item.get("embedding_score", 0) or 0)
            item["content_score"] = self.alpha * emb + (1 - self.alpha) * kw

        results = list(combined.values())
        results.sort(key=lambda x: x["content_score"], reverse=True)
        results = results[:limit]

        logger.info(
            f"[PerceptualSearch] rerank: merged={len(combined)}, after_threshold={len(results)} "
            f"(alpha={self.alpha})"
        )
        return results

    @staticmethod
    def _normalize_scores(items: list[dict], field: str = "score") -> list[dict]:
        """Min-max 归一化，将分数线性映射到 [0, 1]。"""
        if not items:
            return items
        scores = [float(it.get(field, 0) or 0) for it in items]
        min_s = min(scores)
        max_s = max(scores)
        diff = max_s - min_s
        for it, s in zip(items, scores):
            it[f"normalized_{field}"] = (s - min_s) / diff if diff > 0 else 1.0
        return items

    @staticmethod
    def _format_result(record: dict) -> dict:
        return {
            "id": record.get("id", ""),
            "perceptual_type": record.get("perceptual_type", ""),
            "file_name": record.get("file_name", ""),
            "file_path": record.get("file_path", ""),
            "summary": record.get("summary", ""),
            "topic": record.get("topic", ""),
            "domain": record.get("domain", ""),
            "keywords": record.get("keywords", []),
            "created_at": str(record.get("created_at", "")),
            "file_type": record.get("file_type", ""),
            "score": record.get("score", 0),
        }

    @staticmethod
    def _build_content_text(formatted: dict) -> str:
        content_text = (f"<history-file-info>"
                        f"<file-name>{formatted["file_name"]}</file-name>"
                        f"<file-path>{formatted["file_path"]}</file-path>"
                        f"<file-type>{formatted["file_type"]}</file-type>"
                        f"<file-topic>{formatted["topic"]}</file-topic>"
                        f"<file-domain>{formatted["keywords"]}</file-domain>"
                        f"<file-summary>{formatted["summary"]}</file-summary>"
                        f"</history-file-info>")
        return content_text
