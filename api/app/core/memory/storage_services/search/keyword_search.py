# -*- coding: utf-8 -*-
"""关键词搜索策略

实现基于关键词的全文搜索功能。
使用Neo4j的全文索引进行高效的文本匹配。
"""

from typing import List, Dict, Any, Optional
from app.core.logging_config import get_memory_logger
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.storage_services.search.search_strategy import SearchStrategy, SearchResult
from app.repositories.neo4j.graph_search import search_graph

logger = get_memory_logger(__name__)


class KeywordSearchStrategy(SearchStrategy):
    """关键词搜索策略

    使用Neo4j全文索引进行关键词匹配搜索。
    支持跨陈述句、实体、分块和摘要的搜索。
    """

    def __init__(self, connector: Optional[Neo4jConnector] = None):
        """初始化关键词搜索策略

        Args:
            connector: Neo4j连接器，如果为None则创建新连接
        """
        self.connector = connector
        self._owns_connector = connector is None

    async def __aenter__(self):
        """异步上下文管理器入口"""
        if self._owns_connector:
            self.connector = Neo4jConnector()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if self._owns_connector and self.connector:
            await self.connector.close()

    async def search(
        self,
        query_text: str,
        end_user_id: Optional[str] = None,
        limit: int = 50,
        include: Optional[List[str]] = None,
        **kwargs
    ) -> SearchResult:
        """执行关键词搜索

        Args:
            query_text: 查询文本
            end_user_id: 可选的组ID过滤
            limit: 每个类别的最大结果数
            include: 要包含的搜索类别列表
            **kwargs: 其他搜索参数

        Returns:
            SearchResult: 搜索结果对象
        """
        logger.info(f"执行关键词搜索: query='{query_text}', end_user_id={end_user_id}, limit={limit}")

        # 获取有效的搜索类别
        include_list = self._get_include_list(include)

        # 确保连接器已初始化
        if not self.connector:
            self.connector = Neo4jConnector()

        try:
            # 调用底层的关键词搜索函数
            results_dict = await search_graph(
                connector=self.connector,
                q=query_text,
                end_user_id=end_user_id,
                limit=limit,
                include=include_list
            )

            # 创建元数据
            metadata = self._create_metadata(
                query_text=query_text,
                search_type="keyword",
                end_user_id=end_user_id,
                limit=limit,
                include=include_list
            )

            # 添加结果统计
            metadata["result_counts"] = {
                category: len(results_dict.get(category, []))
                for category in include_list
            }
            metadata["total_results"] = sum(metadata["result_counts"].values())

            # 构建SearchResult对象
            search_result = SearchResult(
                statements=results_dict.get("statements", []),
                chunks=results_dict.get("chunks", []),
                entities=results_dict.get("entities", []),
                summaries=results_dict.get("summaries", []),
                metadata=metadata
            )

            logger.info(f"关键词搜索完成: 共找到 {search_result.total_results()} 条结果")
            return search_result

        except Exception as e:
            logger.error(f"关键词搜索失败: {e}", exc_info=True)
            # 返回空结果但包含错误信息
            return SearchResult(
                metadata=self._create_metadata(
                    query_text=query_text,
                    search_type="keyword",
                    end_user_id=end_user_id,
                    limit=limit,
                    error=str(e)
                )
            )
