# -*- coding: utf-8 -*-
"""语义搜索策略

实现基于向量嵌入的语义搜索功能。
使用余弦相似度进行语义匹配。
"""

from typing import Any, Dict, List, Optional

from app.core.logging_config import get_memory_logger
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.memory.storage_services.search.search_strategy import (
    SearchResult,
    SearchStrategy,
)
from app.core.memory.utils.config import definitions as config_defs
from app.core.models.base import RedBearModelConfig
from app.db import get_db_context
from app.repositories.neo4j.graph_search import search_graph_by_embedding
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services.memory_config_service import MemoryConfigService

logger = get_memory_logger(__name__)


class SemanticSearchStrategy(SearchStrategy):
    """语义搜索策略

    使用向量嵌入和余弦相似度进行语义搜索。
    支持跨陈述句、分块、实体和摘要的语义匹配。
    """

    def __init__(
        self,
        connector: Optional[Neo4jConnector] = None,
        embedder_client: Optional[OpenAIEmbedderClient] = None
    ):
        """初始化语义搜索策略

        Args:
            connector: Neo4j连接器，如果为None则创建新连接
            embedder_client: 嵌入模型客户端，如果为None则根据配置创建
        """
        self.connector = connector
        self.embedder_client = embedder_client
        self._owns_connector = connector is None
        self._owns_embedder = embedder_client is None

    async def __aenter__(self):
        """异步上下文管理器入口"""
        if self._owns_connector:
            self.connector = Neo4jConnector()
        if self._owns_embedder:
            self.embedder_client = self._create_embedder_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if self._owns_connector and self.connector:
            await self.connector.close()

    def _create_embedder_client(self) -> OpenAIEmbedderClient:
        """创建嵌入模型客户端

        Returns:
            OpenAIEmbedderClient: 嵌入模型客户端实例
        """
        try:
            # 从数据库读取嵌入器配置
            with get_db_context() as db:
                config_service = MemoryConfigService(db)
                embedder_config_dict = config_service.get_embedder_config(config_defs.SELECTED_EMBEDDING_ID)
            rb_config = RedBearModelConfig(
                model_name=embedder_config_dict["model_name"],
                provider=embedder_config_dict["provider"],
                api_key=embedder_config_dict["api_key"],
                base_url=embedder_config_dict["base_url"],
                type="llm"
            )
            return OpenAIEmbedderClient(model_config=rb_config)
        except Exception as e:
            logger.error(f"创建嵌入模型客户端失败: {e}", exc_info=True)
            raise

    async def search(
        self,
        query_text: str,
        group_id: Optional[str] = None,
        limit: int = 50,
        include: Optional[List[str]] = None,
        **kwargs
    ) -> SearchResult:
        """执行语义搜索

        Args:
            query_text: 查询文本
            group_id: 可选的组ID过滤
            limit: 每个类别的最大结果数
            include: 要包含的搜索类别列表
            **kwargs: 其他搜索参数

        Returns:
            SearchResult: 搜索结果对象
        """
        logger.info(f"执行语义搜索: query='{query_text}', group_id={group_id}, limit={limit}")

        # 获取有效的搜索类别
        include_list = self._get_include_list(include)

        # 确保连接器和嵌入器已初始化
        if not self.connector:
            self.connector = Neo4jConnector()
        if not self.embedder_client:
            self.embedder_client = self._create_embedder_client()

        try:
            # 调用底层的语义搜索函数
            results_dict = await search_graph_by_embedding(
                connector=self.connector,
                embedder_client=self.embedder_client,
                query_text=query_text,
                group_id=group_id,
                limit=limit,
                include=include_list
            )

            # 创建元数据
            metadata = self._create_metadata(
                query_text=query_text,
                search_type="semantic",
                group_id=group_id,
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

            logger.info(f"语义搜索完成: 共找到 {search_result.total_results()} 条结果")
            return search_result

        except Exception as e:
            logger.error(f"语义搜索失败: {e}", exc_info=True)
            # 返回空结果但包含错误信息
            return SearchResult(
                metadata=self._create_metadata(
                    query_text=query_text,
                    search_type="semantic",
                    group_id=group_id,
                    limit=limit,
                    error=str(e)
                )
            )
