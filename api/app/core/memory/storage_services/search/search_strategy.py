# -*- coding: utf-8 -*-
"""搜索策略基类

定义搜索策略的抽象接口和统一的搜索结果数据结构。
遵循策略模式（Strategy Pattern）和开放-关闭原则（OCP）。
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class SearchResult(BaseModel):
    """统一的搜索结果数据结构

    Attributes:
        statements: 陈述句搜索结果列表
        chunks: 分块搜索结果列表
        entities: 实体搜索结果列表
        summaries: 摘要搜索结果列表
        metadata: 搜索元数据（如查询时间、结果数量等）
    """
    statements: List[Dict[str, Any]] = Field(default_factory=list, description="陈述句搜索结果")
    chunks: List[Dict[str, Any]] = Field(default_factory=list, description="分块搜索结果")
    entities: List[Dict[str, Any]] = Field(default_factory=list, description="实体搜索结果")
    summaries: List[Dict[str, Any]] = Field(default_factory=list, description="摘要搜索结果")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="搜索元数据")

    def total_results(self) -> int:
        """返回所有类别的结果总数"""
        return (
            len(self.statements) +
            len(self.chunks) +
            len(self.entities) +
            len(self.summaries)
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "statements": self.statements,
            "chunks": self.chunks,
            "entities": self.entities,
            "summaries": self.summaries,
            "metadata": self.metadata
        }


class SearchStrategy(ABC):
    """搜索策略抽象基类

    定义所有搜索策略必须实现的接口。
    遵循依赖反转原则（DIP）：高层模块依赖抽象而非具体实现。
    """

    @abstractmethod
    async def search(
        self,
        query_text: str,
        group_id: Optional[str] = None,
        limit: int = 50,
        include: Optional[List[str]] = None,
        **kwargs
    ) -> SearchResult:
        """执行搜索

        Args:
            query_text: 查询文本
            group_id: 可选的组ID过滤
            limit: 每个类别的最大结果数
            include: 要包含的搜索类别列表（statements, chunks, entities, summaries）
            **kwargs: 其他搜索参数

        Returns:
            SearchResult: 统一的搜索结果对象
        """
        pass

    def _create_metadata(
        self,
        query_text: str,
        search_type: str,
        group_id: Optional[str] = None,
        limit: int = 50,
        **kwargs
    ) -> Dict[str, Any]:
        """创建搜索元数据

        Args:
            query_text: 查询文本
            search_type: 搜索类型
            group_id: 组ID
            limit: 结果限制
            **kwargs: 其他元数据

        Returns:
            Dict[str, Any]: 元数据字典
        """
        metadata = {
            "query": query_text,
            "search_type": search_type,
            "group_id": group_id,
            "limit": limit,
            "timestamp": datetime.now().isoformat()
        }
        metadata.update(kwargs)
        return metadata

    def _get_include_list(self, include: Optional[List[str]] = None) -> List[str]:
        """获取要包含的搜索类别列表

        Args:
            include: 用户指定的类别列表

        Returns:
            List[str]: 有效的类别列表
        """
        default_include = ["statements", "chunks", "entities", "summaries"]
        if include is None:
            return default_include

        # 验证并过滤有效的类别
        valid_categories = set(default_include)
        return [cat for cat in include if cat in valid_categories]
