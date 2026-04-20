# -*- coding: utf-8 -*-
"""搜索服务模块

本模块提供统一的搜索服务接口，支持关键词搜索、语义搜索和混合搜索。
"""

from app.core.memory.storage_services.search.hybrid_search import HybridSearchStrategy
from app.core.memory.storage_services.search.keyword_search import KeywordSearchStrategy
from app.core.memory.storage_services.search.search_strategy import (
    SearchResult,
    SearchStrategy,
)
from app.core.memory.storage_services.search.semantic_search import (
    SemanticSearchStrategy,
)

__all__ = [
    "SearchStrategy",
    "SearchResult",
    "KeywordSearchStrategy",
    "SemanticSearchStrategy",
    "HybridSearchStrategy",
]


# ============================================================================
# 向后兼容的函数式API (DEPRECATED - 未被使用)
# ============================================================================
# 所有调用方均直接使用 app.core.memory.src.search.run_hybrid_search
# 保留注释以备参考

# async def run_hybrid_search(
#     query_text: str,
#     search_type: str = "hybrid",
#     end_user_id: str | None = None,
#     apply_id: str | None = None,
#     user_id: str | None = None,
#     limit: int = 50,
#     include: list[str] | None = None,
#     alpha: float = 0.6,
#     use_forgetting_curve: bool = False,
#     memory_config: "MemoryConfig" = None,
#     **kwargs
# ) -> dict:
#     """运行混合搜索（向后兼容的函数式API）"""
#     from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
#     from app.core.models.base import RedBearModelConfig
#     from app.db import get_db_context
#     from app.repositories.neo4j.neo4j_connector import Neo4jConnector
#     from app.services.memory_config_service import MemoryConfigService
#
#     if not memory_config:
#         raise ValueError("memory_config is required for search")
#
#     connector = Neo4jConnector()
#     with get_db_context() as db:
#         config_service = MemoryConfigService(db)
#         embedder_config_dict = config_service.get_embedder_config(str(memory_config.embedding_model_id))
#     embedder_config = RedBearModelConfig(**embedder_config_dict)
#     embedder_client = OpenAIEmbedderClient(embedder_config)
#
#     try:
#         if search_type == "keyword":
#             strategy = KeywordSearchStrategy(connector=connector)
#         elif search_type == "semantic":
#             strategy = SemanticSearchStrategy(
#                 connector=connector,
#                 embedder_client=embedder_client
#             )
#         else:
#             strategy = HybridSearchStrategy(
#                 connector=connector,
#                 embedder_client=embedder_client,
#                 alpha=alpha,
#                 use_forgetting_curve=use_forgetting_curve
#             )
#
#         result = await strategy.search(
#             query_text=query_text,
#             end_user_id=end_user_id,
#             limit=limit,
#             include=include,
#             alpha=alpha,
#             use_forgetting_curve=use_forgetting_curve,
#             **kwargs
#         )
#
#         result_dict = result.to_dict()
#
#         output_path = kwargs.get('output_path', 'search_results.json')
#         if output_path:
#             import json
#             import os
#             from datetime import datetime
#
#             try:
#                 out_dir = os.path.dirname(output_path)
#                 if out_dir:
#                     os.makedirs(out_dir, exist_ok=True)
#                 with open(output_path, "w", encoding="utf-8") as f:
#                     json.dump(result_dict, f, ensure_ascii=False, indent=2, default=str)
#                 print(f"Search results saved to {output_path}")
#             except Exception as e:
#                 print(f"Error saving search results: {e}")
#         return result_dict
#
#     finally:
#         await connector.close()
#
# __all__.append("run_hybrid_search")
