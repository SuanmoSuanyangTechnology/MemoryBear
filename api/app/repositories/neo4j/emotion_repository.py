# -*- coding: utf-8 -*-
"""情绪数据仓储模块

本模块提供情绪数据的查询功能，用于情绪分析和统计。

Classes:
    EmotionRepository: 情绪数据仓储，提供情绪标签、词云、健康指数等查询方法
"""

from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import json

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class EmotionRepository:
    """情绪数据仓储
    
    提供情绪数据的查询和统计功能，包括：
    - 情绪标签统计
    - 情绪词云数据
    - 时间范围内的情绪数据查询
    
    Attributes:
        connector: Neo4j连接器实例
    """
    
    def __init__(self, connector: Neo4jConnector):
        """初始化情绪数据仓储
        
        Args:
            connector: Neo4j连接器实例
        """
        self.connector = connector
        logger.info("情绪数据仓储初始化完成")
    
    async def get_emotion_tags(
        self,
        group_id: str,
        emotion_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """获取情绪标签统计
        
        查询指定用户的情绪类型分布，包括计数、百分比和平均强度。
        
        Args:
            group_id: 用户组ID（宿主ID）
            emotion_type: 可选的情绪类型过滤（joy/sadness/anger/fear/surprise/neutral）
            start_date: 可选的开始日期（ISO格式字符串）
            end_date: 可选的结束日期（ISO格式字符串）
            limit: 返回结果的最大数量
            
        Returns:
            List[Dict]: 情绪标签列表，每个包含：
                - emotion_type: 情绪类型
                - count: 该类型的数量
                - percentage: 占比百分比
                - avg_intensity: 平均强度
        """
        # 构建查询条件
        where_clauses = ["s.group_id = $group_id", "s.emotion_type IS NOT NULL"]
        params = {"group_id": group_id, "limit": limit}
        
        if emotion_type:
            where_clauses.append("s.emotion_type = $emotion_type")
            params["emotion_type"] = emotion_type
        
        if start_date:
            where_clauses.append("s.created_at >= $start_date")
            params["start_date"] = start_date
        
        if end_date:
            where_clauses.append("s.created_at <= $end_date")
            params["end_date"] = end_date
        
        where_str = " AND ".join(where_clauses)
        
        # 优化的 Cypher 查询：使用索引，减少中间结果
        query = f"""
        MATCH (s:Statement)
        WHERE {where_str}
        WITH s.emotion_type as emotion_type, 
             count(*) as count,
             avg(s.emotion_intensity) as avg_intensity
        WITH collect({{emotion_type: emotion_type, count: count, avg_intensity: avg_intensity}}) as results,
             sum(count) as total_count
        UNWIND results as result
        RETURN result.emotion_type as emotion_type,
               result.count as count,
               toFloat(result.count) / total_count * 100 as percentage,
               result.avg_intensity as avg_intensity
        ORDER BY count DESC
        LIMIT $limit
        """
        
        try:
            results = await self.connector.execute_query(query, **params)
            formatted_results = [
                {
                    "emotion_type": record["emotion_type"],
                    "count": record["count"],
                    "percentage": round(record["percentage"], 2),
                    "avg_intensity": round(record["avg_intensity"], 3) if record["avg_intensity"] else 0.0
                }
                for record in results
            ]
            
            return formatted_results
        except Exception as e:
            logger.error(f"查询情绪标签失败: {str(e)}", exc_info=True)
            return []
    
    async def get_emotion_wordcloud(
        self,
        group_id: str,
        emotion_type: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """获取情绪词云数据
        
        查询情绪关键词及其频率，用于生成词云可视化。
        
        Args:
            group_id: 用户组ID（宿主ID）
            emotion_type: 可选的情绪类型过滤
            limit: 返回关键词的最大数量
            
        Returns:
            List[Dict]: 关键词列表，每个包含：
                - keyword: 关键词
                - frequency: 出现频率
                - emotion_type: 关联的情绪类型
                - avg_intensity: 平均强度
        """
        # 构建查询条件
        where_clauses = ["s.group_id = $group_id", "s.emotion_keywords IS NOT NULL"]
        params = {"group_id": group_id, "limit": limit}
        
        if emotion_type:
            where_clauses.append("s.emotion_type = $emotion_type")
            params["emotion_type"] = emotion_type
        
        where_str = " AND ".join(where_clauses)
        
        # 优化的 Cypher 查询：使用索引，减少不必要的计算
        query = f"""
        MATCH (s:Statement)
        WHERE {where_str}
        UNWIND s.emotion_keywords as keyword
        WITH keyword, 
             s.emotion_type as emotion_type,
             count(*) as frequency,
             avg(s.emotion_intensity) as avg_intensity
        WHERE keyword IS NOT NULL AND keyword <> ''
        RETURN keyword,
               frequency,
               emotion_type,
               avg_intensity
        ORDER BY frequency DESC
        LIMIT $limit
        """
        
        try:
            results = await self.connector.execute_query(query, **params)
            formatted_results = [
                {
                    "keyword": record["keyword"],
                    "frequency": record["frequency"],
                    "emotion_type": record["emotion_type"],
                    "avg_intensity": round(record["avg_intensity"], 3) if record["avg_intensity"] else 0.0
                }
                for record in results
            ]
            
            return formatted_results
        except Exception as e:
            logger.error(f"查询情绪词云失败: {str(e)}", exc_info=True)
            return []
    
    async def get_emotions_in_range(
        self,
        group_id: str,
        time_range: str = "30d"
    ) -> List[Dict[str, Any]]:
        """获取时间范围内的情绪数据
        
        查询指定时间范围内的所有情绪数据，用于健康指数计算。
        
        Args:
            group_id: 用户组ID（宿主ID）
            time_range: 时间范围（7d/30d/90d）
            
        Returns:
            List[Dict]: 情绪数据列表，每个包含：
                - emotion_type: 情绪类型
                - emotion_intensity: 情绪强度
                - created_at: 创建时间
                - statement_id: 陈述句ID
        """
        # 解析时间范围
        days_map = {"7d": 7, "30d": 30, "90d": 90}
        days = days_map.get(time_range, 30)
        
        # 计算起始日期（使用字符串比较，避免时区问题）
        start_date = (datetime.now() - timedelta(days=days)).isoformat()
        
        # 优化的 Cypher 查询：使用字符串比较避免时区问题
        query = """
        MATCH (s:Statement)
        WHERE s.group_id = $group_id
          AND s.emotion_type IS NOT NULL
          AND s.created_at >= $start_date
        RETURN s.id as statement_id,
               s.emotion_type as emotion_type,
               s.emotion_intensity as emotion_intensity,
               s.created_at as created_at
        ORDER BY s.created_at ASC
        """
        
        try:
            results = await self.connector.execute_query(
                query,
                group_id=group_id,
                start_date=start_date
            )
            formatted_results = [
                {
                    "statement_id": record["statement_id"],
                    "emotion_type": record["emotion_type"],
                    "emotion_intensity": record["emotion_intensity"],
                    "created_at": record["created_at"].isoformat() if hasattr(record["created_at"], "isoformat") else str(record["created_at"])
                }
                for record in results
            ]
            
            return formatted_results
        except Exception as e:
            logger.error(f"查询时间范围情绪数据失败: {str(e)}", exc_info=True)
            return []
