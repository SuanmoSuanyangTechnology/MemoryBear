# # -*- coding: utf-8 -*-
# """混合搜索策略

# 结合关键词搜索和语义搜索的混合检索方法。
# 支持结果重排序和遗忘曲线加权。
# """

# from typing import List, Dict, Any, Optional
# import math
# from datetime import datetime
# from app.core.logging_config import get_memory_logger
# from app.repositories.neo4j.neo4j_connector import Neo4jConnector
# from app.core.memory.storage_services.search.search_strategy import SearchStrategy, SearchResult
# from app.core.memory.storage_services.search.keyword_search import KeywordSearchStrategy
# from app.core.memory.storage_services.search.semantic_search import SemanticSearchStrategy
# from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
# from app.core.memory.models.variate_config import ForgettingEngineConfig
# from app.core.memory.storage_services.forgetting_engine.forgetting_engine import ForgettingEngine

# logger = get_memory_logger(__name__)


# class HybridSearchStrategy(SearchStrategy):
#     """混合搜索策略

#     结合关键词搜索和语义搜索的优势：
#     - 关键词搜索：精确匹配，适合已知术语
#     - 语义搜索：语义理解，适合概念查询
#     - 混合重排序：综合两种搜索的结果
#     - 遗忘曲线：根据时间衰减调整相关性
#     """

#     def __init__(
#         self,
#         connector: Optional[Neo4jConnector] = None,
#         embedder_client: Optional[OpenAIEmbedderClient] = None,
#         alpha: float = 0.6,
#         use_forgetting_curve: bool = False,
#         forgetting_config: Optional[ForgettingEngineConfig] = None
#     ):
#         """初始化混合搜索策略

#         Args:
#             connector: Neo4j连接器
#             embedder_client: 嵌入模型客户端
#             alpha: BM25分数权重（0.0-1.0），1-alpha为嵌入分数权重
#             use_forgetting_curve: 是否使用遗忘曲线
#             forgetting_config: 遗忘引擎配置
#         """
#         self.connector = connector
#         self.embedder_client = embedder_client
#         self.alpha = alpha
#         self.use_forgetting_curve = use_forgetting_curve
#         self.forgetting_config = forgetting_config or ForgettingEngineConfig()
#         self._owns_connector = connector is None

#         # 创建子策略
#         self.keyword_strategy = KeywordSearchStrategy(connector=connector)
#         self.semantic_strategy = SemanticSearchStrategy(
#             connector=connector,
#             embedder_client=embedder_client
#         )

#     async def __aenter__(self):
#         """异步上下文管理器入口"""
#         if self._owns_connector:
#             self.connector = Neo4jConnector()
#             self.keyword_strategy.connector = self.connector
#             self.semantic_strategy.connector = self.connector
#         return self

#     async def __aexit__(self, exc_type, exc_val, exc_tb):
#         """异步上下文管理器出口"""
#         if self._owns_connector and self.connector:
#             await self.connector.close()

#     async def search(
#         self,
#         query_text: str,
#         group_id: Optional[str] = None,
#         limit: int = 50,
#         include: Optional[List[str]] = None,
#         **kwargs
#     ) -> SearchResult:
#         """执行混合搜索

#         Args:
#             query_text: 查询文本
#             group_id: 可选的组ID过滤
#             limit: 每个类别的最大结果数
#             include: 要包含的搜索类别列表
#             **kwargs: 其他搜索参数（如alpha, use_forgetting_curve）

#         Returns:
#             SearchResult: 搜索结果对象
#         """
#         logger.info(f"执行混合搜索: query='{query_text}', group_id={group_id}, limit={limit}")

#         # 从kwargs中获取参数
#         alpha = kwargs.get("alpha", self.alpha)
#         use_forgetting = kwargs.get("use_forgetting_curve", self.use_forgetting_curve)

#         # 获取有效的搜索类别
#         include_list = self._get_include_list(include)

#         try:
#             # 并行执行关键词搜索和语义搜索
#             keyword_result = await self.keyword_strategy.search(
#                 query_text=query_text,
#                 group_id=group_id,
#                 limit=limit,
#                 include=include_list
#             )

#             semantic_result = await self.semantic_strategy.search(
#                 query_text=query_text,
#                 group_id=group_id,
#                 limit=limit,
#                 include=include_list
#             )

#             # 重排序结果
#             if use_forgetting:
#                 reranked_results = self._rerank_with_forgetting_curve(
#                     keyword_result=keyword_result,
#                     semantic_result=semantic_result,
#                     alpha=alpha,
#                     limit=limit
#                 )
#             else:
#                 reranked_results = self._rerank_hybrid_results(
#                     keyword_result=keyword_result,
#                     semantic_result=semantic_result,
#                     alpha=alpha,
#                     limit=limit
#                 )

#             # 创建元数据
#             metadata = self._create_metadata(
#                 query_text=query_text,
#                 search_type="hybrid",
#                 group_id=group_id,
#                 limit=limit,
#                 include=include_list,
#                 alpha=alpha,
#                 use_forgetting_curve=use_forgetting
#             )

#             # 添加结果统计
#             metadata["keyword_results"] = keyword_result.metadata.get("result_counts", {})
#             metadata["semantic_results"] = semantic_result.metadata.get("result_counts", {})
#             metadata["total_keyword_results"] = keyword_result.total_results()
#             metadata["total_semantic_results"] = semantic_result.total_results()
#             metadata["total_reranked_results"] = reranked_results.total_results()

#             reranked_results.metadata = metadata

#             logger.info(f"混合搜索完成: 共找到 {reranked_results.total_results()} 条结果")
#             return reranked_results

#         except Exception as e:
#             logger.error(f"混合搜索失败: {e}", exc_info=True)
#             # 返回空结果但包含错误信息
#             return SearchResult(
#                 metadata=self._create_metadata(
#                     query_text=query_text,
#                     search_type="hybrid",
#                     group_id=group_id,
#                     limit=limit,
#                     error=str(e)
#                 )
#             )

#     def _normalize_scores(
#         self,
#         results: List[Dict[str, Any]],
#         score_field: str = "score"
#     ) -> List[Dict[str, Any]]:
#         """使用z-score标准化和sigmoid转换归一化分数

#         Args:
#             results: 结果列表
#             score_field: 分数字段名

#         Returns:
#             List[Dict[str, Any]]: 归一化后的结果列表
#         """
#         if not results:
#             return results

#         # 提取分数
#         scores = []
#         for item in results:
#             if score_field in item:
#                 score = item.get(score_field)
#                 if score is not None and isinstance(score, (int, float)):
#                     scores.append(float(score))
#                 else:
#                     scores.append(0.0)

#         if not scores or len(scores) == 1:
#             # 单个分数或无分数，设置为1.0
#             for item in results:
#                 if score_field in item:
#                     item[f"normalized_{score_field}"] = 1.0
#             return results

#         # 计算均值和标准差
#         mean_score = sum(scores) / len(scores)
#         variance = sum((score - mean_score) ** 2 for score in scores) / len(scores)
#         std_dev = math.sqrt(variance)

#         if std_dev == 0:
#             # 所有分数相同，设置为1.0
#             for item in results:
#                 if score_field in item:
#                     item[f"normalized_{score_field}"] = 1.0
#         else:
#             # z-score标准化 + sigmoid转换
#             for item in results:
#                 if score_field in item:
#                     score = item[score_field]
#                     if score is None or not isinstance(score, (int, float)):
#                         score = 0.0
#                     z_score = (score - mean_score) / std_dev
#                     normalized = 1 / (1 + math.exp(-z_score))
#                     item[f"normalized_{score_field}"] = normalized

#         return results

#     def _rerank_hybrid_results(
#         self,
#         keyword_result: SearchResult,
#         semantic_result: SearchResult,
#         alpha: float,
#         limit: int
#     ) -> SearchResult:
#         """重排序混合搜索结果

#         Args:
#             keyword_result: 关键词搜索结果
#             semantic_result: 语义搜索结果
#             alpha: BM25分数权重
#             limit: 结果限制

#         Returns:
#             SearchResult: 重排序后的结果
#         """
#         reranked_data = {}

#         for category in ["statements", "chunks", "entities", "summaries"]:
#             keyword_items = getattr(keyword_result, category, [])
#             semantic_items = getattr(semantic_result, category, [])

#             # 归一化分数
#             keyword_items = self._normalize_scores(keyword_items, "score")
#             semantic_items = self._normalize_scores(semantic_items, "score")

#             # 合并结果
#             combined_items = {}

#             # 添加关键词结果
#             for item in keyword_items:
#                 item_id = item.get("id") or item.get("uuid")
#                 if item_id:
#                     combined_items[item_id] = item.copy()
#                     combined_items[item_id]["bm25_score"] = item.get("normalized_score", 0)
#                     combined_items[item_id]["embedding_score"] = 0

#             # 添加或更新语义结果
#             for item in semantic_items:
#                 item_id = item.get("id") or item.get("uuid")
#                 if item_id:
#                     if item_id in combined_items:
#                         combined_items[item_id]["embedding_score"] = item.get("normalized_score", 0)
#                     else:
#                         combined_items[item_id] = item.copy()
#                         combined_items[item_id]["bm25_score"] = 0
#                         combined_items[item_id]["embedding_score"] = item.get("normalized_score", 0)

#             # 计算组合分数
#             for item_id, item in combined_items.items():
#                 bm25_score = item.get("bm25_score", 0)
#                 embedding_score = item.get("embedding_score", 0)
#                 combined_score = alpha * bm25_score + (1 - alpha) * embedding_score
#                 item["combined_score"] = combined_score

#             # 排序并限制结果
#             sorted_items = sorted(
#                 combined_items.values(),
#                 key=lambda x: x.get("combined_score", 0),
#                 reverse=True
#             )[:limit]

#             reranked_data[category] = sorted_items

#         return SearchResult(
#             statements=reranked_data.get("statements", []),
#             chunks=reranked_data.get("chunks", []),
#             entities=reranked_data.get("entities", []),
#             summaries=reranked_data.get("summaries", [])
#         )

#     def _parse_datetime(self, value: Any) -> Optional[datetime]:
#         """解析日期时间字符串"""
#         if value is None:
#             return None
#         if isinstance(value, datetime):
#             return value
#         if isinstance(value, str):
#             s = value.strip()
#             if not s:
#                 return None
#             try:
#                 return datetime.fromisoformat(s)
#             except Exception:
#                 return None
#         return None

#     def _rerank_with_forgetting_curve(
#         self,
#         keyword_result: SearchResult,
#         semantic_result: SearchResult,
#         alpha: float,
#         limit: int
#     ) -> SearchResult:
#         """使用遗忘曲线重排序混合搜索结果

#         Args:
#             keyword_result: 关键词搜索结果
#             semantic_result: 语义搜索结果
#             alpha: BM25分数权重
#             limit: 结果限制

#         Returns:
#             SearchResult: 重排序后的结果
#         """
#         engine = ForgettingEngine(self.forgetting_config)
#         now_dt = datetime.now()

#         reranked_data = {}

#         for category in ["statements", "chunks", "entities", "summaries"]:
#             keyword_items = getattr(keyword_result, category, [])
#             semantic_items = getattr(semantic_result, category, [])

#             # 归一化分数
#             keyword_items = self._normalize_scores(keyword_items, "score")
#             semantic_items = self._normalize_scores(semantic_items, "score")

#             # 合并结果
#             combined_items = {}

#             for src_items, is_embedding in [(keyword_items, False), (semantic_items, True)]:
#                 for item in src_items:
#                     item_id = item.get("id") or item.get("uuid")
#                     if not item_id:
#                         continue

#                     if item_id not in combined_items:
#                         combined_items[item_id] = item.copy()
#                         combined_items[item_id]["bm25_score"] = 0
#                         combined_items[item_id]["embedding_score"] = 0

#                     if is_embedding:
#                         combined_items[item_id]["embedding_score"] = item.get("normalized_score", 0)
#                     else:
#                         combined_items[item_id]["bm25_score"] = item.get("normalized_score", 0)

#             # 计算分数并应用遗忘权重
#             for item_id, item in combined_items.items():
#                 bm25_score = float(item.get("bm25_score", 0) or 0)
#                 embedding_score = float(item.get("embedding_score", 0) or 0)
#                 combined_score = alpha * bm25_score + (1 - alpha) * embedding_score

#                 # 计算时间衰减
#                 dt = self._parse_datetime(item.get("created_at"))
#                 if dt is None:
#                     time_elapsed_days = 0.0
#                 else:
#                     time_elapsed_days = max(0.0, (now_dt - dt).total_seconds() / 86400.0)

#                 memory_strength = 1.0  # 默认强度
#                 forgetting_weight = engine.calculate_weight(
#                     time_elapsed=time_elapsed_days,
#                     memory_strength=memory_strength
#                 )

#                 final_score = combined_score * forgetting_weight
#                 item["combined_score"] = final_score
#                 item["forgetting_weight"] = forgetting_weight
#                 item["time_elapsed_days"] = time_elapsed_days

#             # 排序并限制结果
#             sorted_items = sorted(
#                 combined_items.values(),
#                 key=lambda x: x.get("combined_score", 0),
#                 reverse=True
#             )[:limit]

#             reranked_data[category] = sorted_items

#         return SearchResult(
#             statements=reranked_data.get("statements", []),
#             chunks=reranked_data.get("chunks", []),
#             entities=reranked_data.get("entities", []),
#             summaries=reranked_data.get("summaries", [])
#         )
