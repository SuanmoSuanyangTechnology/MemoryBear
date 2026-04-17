"""
Perceptual Memory Retrieval Node & Service

Provides PerceptualSearchService for searching perceptual memories (vision, audio,
text, conversation) from Neo4j using keyword fulltext + embedding semantic search
with BM25+embedding fusion reranking.

Also provides the perceptual_retrieve_node for use as a LangGraph node.
"""
import asyncio
import math
from typing import List, Dict, Any, Optional

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.llm_tools import ReadState
from app.core.memory.utils.data.text_utils import escape_lucene_query
from app.repositories.neo4j.graph_search import (
    search_perceptual,
    search_perceptual_by_embedding,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = get_agent_logger(__name__)


class PerceptualSearchService:
    """
    感知记忆检索服务。

    封装关键词全文检索 + 向量语义检索 + BM25/embedding 融合排序的完整流程。
    调用方只需提供 query / keywords、end_user_id、memory_config，即可获得
    格式化并排序后的感知记忆列表和拼接文本。

    Usage:
        service = PerceptualSearchService(end_user_id=..., memory_config=...)
        results = await service.search(query="...", keywords=[...], limit=10)
        # results = {"memories": [...], "content": "...", "keyword_raw": N, "embedding_raw": M}
    """

    DEFAULT_ALPHA = 0.6
    DEFAULT_CONTENT_SCORE_THRESHOLD = 0.5

    def __init__(
            self,
            end_user_id: str,
            memory_config: Any,
            alpha: float = DEFAULT_ALPHA,
            content_score_threshold: float = DEFAULT_CONTENT_SCORE_THRESHOLD,
    ):
        self.end_user_id = end_user_id
        self.memory_config = memory_config
        self.alpha = alpha
        self.content_score_threshold = content_score_threshold

    async def search(
            self,
            query: str,
            keywords: Optional[List[str]] = None,
            limit: int = 10,
    ) -> Dict[str, Any]:
        """
        执行感知记忆检索（关键词 + 向量并行），融合排序后返回结果。

        对 embedding 命中但 keyword 未命中的结果，补查全文索引获取 BM25 分数，
        确保所有结果都同时具备 BM25 和 embedding 两个维度的评分。

        Args:
            query: 原始用户查询（用于向量检索和 BM25 补查）
            keywords: 关键词列表（用于全文检索），为 None 时使用 [query]
            limit: 最大返回数量

        Returns:
            {
                "memories": [格式化后的记忆 dict, ...],
                "content": "拼接的纯文本摘要",
                "keyword_raw": int,
                "embedding_raw": int,
            }
        """
        if keywords is None:
            keywords = [query] if query else []

        connector = Neo4jConnector()
        try:
            kw_task = self._keyword_search(connector, keywords, limit)
            emb_task = self._embedding_search(connector, query, limit)

            kw_results, emb_results = await asyncio.gather(
                kw_task, emb_task, return_exceptions=True
            )
            if isinstance(kw_results, Exception):
                logger.warning(f"[PerceptualSearch] keyword search error: {kw_results}")
                kw_results = []
            if isinstance(emb_results, Exception):
                logger.warning(f"[PerceptualSearch] embedding search error: {emb_results}")
                emb_results = []

            # 补查 BM25：找出 embedding 命中但 keyword 未命中的 id，
            # 用原始 query 对这些节点补查全文索引拿 BM25 score
            kw_ids = {r.get("id") for r in kw_results if r.get("id")}
            emb_only_ids = {r.get("id") for r in emb_results if r.get("id") and r.get("id") not in kw_ids}

            if emb_only_ids and query:
                backfill = await self._bm25_backfill(connector, query, emb_only_ids, limit)
                # 把补查到的 BM25 score 注入到 embedding 结果中
                backfill_map = {r["id"]: r.get("score", 0) for r in backfill}
                for r in emb_results:
                    rid = r.get("id", "")
                    if rid in backfill_map:
                        r["bm25_backfill_score"] = backfill_map[rid]
                logger.info(
                    f"[PerceptualSearch] BM25 backfill: {len(emb_only_ids)} embedding-only ids, "
                    f"{len(backfill_map)} got BM25 scores"
                )

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
            return {
                "memories": memories,
                "content": "\n\n".join(content_parts),
                "keyword_raw": len(kw_results),
                "embedding_raw": len(emb_results),
            }
        finally:
            await connector.close()

    async def _bm25_backfill(
            self,
            connector: Neo4jConnector,
            query: str,
            target_ids: set,
            limit: int,
    ) -> List[dict]:
        """
        对指定 id 集合补查全文索引 BM25 score。

        用原始 query 查全文索引，只保留 id 在 target_ids 中的结果。
        """
        escaped = escape_lucene_query(query)
        if not escaped.strip():
            return []
        try:
            r = await search_perceptual(
                connector=connector, query=escaped,
                end_user_id=self.end_user_id,
                limit=limit * 5,  # 多查一些以提高命中率
            )
            all_hits = r.get("perceptuals", [])
            return [h for h in all_hits if h.get("id") in target_ids]
        except Exception as e:
            logger.warning(f"[PerceptualSearch] BM25 backfill failed: {e}")
            return []

    async def _keyword_search(
            self,
            connector: Neo4jConnector,
            keywords: List[str],
            limit: int,
    ) -> List[dict]:
        """并发对每个关键词做全文检索，去重后按 score 降序返回 top N 原始结果。"""
        seen_ids: set = set()
        all_results: List[dict] = []

        async def _one(kw: str):
            escaped = escape_lucene_query(kw)
            if not escaped.strip():
                return []
            r = await search_perceptual(
                connector=connector, query=escaped,
                end_user_id=self.end_user_id, limit=limit,
            )
            return r.get("perceptuals", [])

        tasks = [_one(kw) for kw in keywords[:10]]
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
            connector: Neo4jConnector,
            query_text: str,
            limit: int,
    ) -> List[dict]:
        """向量语义检索，返回原始结果（不做阈值过滤）。"""
        try:
            from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
            from app.core.models.base import RedBearModelConfig
            from app.db import get_db_context
            from app.services.memory_config_service import MemoryConfigService

            with get_db_context() as db:
                cfg = MemoryConfigService(db).get_embedder_config(
                    str(self.memory_config.embedding_model_id)
                )
            client = OpenAIEmbedderClient(RedBearModelConfig(**cfg))

            r = await search_perceptual_by_embedding(
                connector=connector, embedder_client=client,
                query_text=query_text, end_user_id=self.end_user_id,
                limit=limit,
            )
            return r.get("perceptuals", [])
        except Exception as e:
            logger.warning(f"[PerceptualSearch] embedding search failed: {e}")
            return []

    def _rerank(
            self,
            keyword_results: List[dict],
            embedding_results: List[dict],
            limit: int,
    ) -> List[dict]:
        """BM25 + embedding 融合排序。

        对 embedding 结果中带有 bm25_backfill_score 的条目，
        将其与 keyword 结果合并后统一归一化，确保 BM25 分数在同一尺度上。
        """
        # 把补查的 BM25 score 合并到 keyword_results 中统一归一化
        emb_backfill_items = []
        for item in embedding_results:
            backfill_score = item.get("bm25_backfill_score")
            if backfill_score is not None and item.get("id"):
                emb_backfill_items.append({"id": item["id"], "score": backfill_score})

        # 合并后统一归一化 BM25 scores
        all_bm25_items = keyword_results + emb_backfill_items
        all_bm25_items = self._normalize_scores(all_bm25_items)

        # 建立 id -> normalized BM25 score 的映射
        bm25_norm_map: Dict[str, float] = {}
        for item in all_bm25_items:
            item_id = item.get("id", "")
            if item_id:
                bm25_norm_map[item_id] = float(item.get("normalized_score", 0))

        # 归一化 embedding scores
        embedding_results = self._normalize_scores(embedding_results)

        # 合并
        combined: Dict[str, dict] = {}
        for item in keyword_results:
            item_id = item.get("id", "")
            if not item_id:
                continue
            combined[item_id] = item.copy()
            combined[item_id]["bm25_score"] = bm25_norm_map.get(item_id, 0)
            combined[item_id]["embedding_score"] = 0.0

        for item in embedding_results:
            item_id = item.get("id", "")
            if not item_id:
                continue
            if item_id in combined:
                combined[item_id]["embedding_score"] = item.get("normalized_score", 0)
            else:
                combined[item_id] = item.copy()
                combined[item_id]["bm25_score"] = bm25_norm_map.get(item_id, 0)
                combined[item_id]["embedding_score"] = item.get("normalized_score", 0)

        for item in combined.values():
            bm25 = float(item.get("bm25_score", 0) or 0)
            emb = float(item.get("embedding_score", 0) or 0)
            item["content_score"] = self.alpha * bm25 + (1 - self.alpha) * emb

        results = list(combined.values())
        before = len(results)
        results = [r for r in results if r["content_score"] >= self.content_score_threshold]
        results.sort(key=lambda x: x["content_score"], reverse=True)
        results = results[:limit]

        logger.info(
            f"[PerceptualSearch] rerank: merged={before}, after_threshold={len(results)} "
            f"(alpha={self.alpha}, threshold={self.content_score_threshold})"
        )
        return results

    @staticmethod
    def _normalize_scores(items: List[dict], field: str = "score") -> List[dict]:
        """Z-score + sigmoid 归一化。"""
        if not items:
            return items
        scores = [float(it.get(field, 0) or 0) for it in items]
        if len(scores) <= 1:
            for it in items:
                it[f"normalized_{field}"] = 1.0
            return items
        mean = sum(scores) / len(scores)
        var = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = math.sqrt(var)
        if std == 0:
            for it in items:
                it[f"normalized_{field}"] = 1.0
        else:
            for it, s in zip(items, scores):
                z = (s - mean) / std
                it[f"normalized_{field}"] = 1 / (1 + math.exp(-z))
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
        parts = []
        if formatted["summary"]:
            parts.append(formatted["summary"])
        if formatted["topic"]:
            parts.append(f"[主题: {formatted['topic']}]")
        if formatted["keywords"]:
            kw_list = formatted["keywords"]
            if isinstance(kw_list, list):
                parts.append(f"[关键词: {', '.join(kw_list)}]")
        if formatted["file_name"]:
            parts.append(f"[文件: {formatted['file_name']}]")
        return " ".join(parts)


def _extract_keywords_from_problems(problem_extension: dict) -> List[str]:
    """Extract search keywords from problem extension results."""
    keywords = []
    context = problem_extension.get("context", {})
    if isinstance(context, dict):
        for original_q, extended_qs in context.items():
            keywords.append(original_q)
            if isinstance(extended_qs, list):
                keywords.extend(extended_qs)
    return keywords


async def perceptual_retrieve_node(state: ReadState) -> ReadState:
    """
    LangGraph node: perceptual memory retrieval.

    Uses PerceptualSearchService to run keyword + embedding search with
    BM25 fusion reranking, then writes results to state['perceptual_data'].
    """
    end_user_id = state.get("end_user_id", "")
    problem_extension = state.get("problem_extension", {})
    original_query = state.get("data", "")
    memory_config = state.get("memory_config", None)

    logger.info(f"Perceptual_Retrieve: start, end_user_id={end_user_id}")

    keywords = _extract_keywords_from_problems(problem_extension)
    if not keywords:
        keywords = [original_query] if original_query else []

    logger.info(f"Perceptual_Retrieve: {len(keywords)} keywords extracted")

    service = PerceptualSearchService(
        end_user_id=end_user_id,
        memory_config=memory_config,
    )
    search_result = await service.search(
        query=original_query,
        keywords=keywords,
        limit=10,
    )

    result = {
        "memories": search_result["memories"],
        "content": search_result["content"],
        "_intermediate": {
            "type": "perceptual_retrieve",
            "title": "感知记忆检索",
            "data": search_result["memories"],
            "query": original_query,
            "result_count": len(search_result["memories"]),
        },
    }
    return {"perceptual_data": result}
