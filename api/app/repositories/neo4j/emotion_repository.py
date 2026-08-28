# -*- coding: utf-8 -*-
"""情绪数据仓储模块

本模块提供情绪数据的查询功能，用于情绪分析和统计。

Classes:
    EmotionRepository: 情绪数据仓储，提供情绪标签、词云、健康指数等查询方法（Statement 节点，LLM 六情绪体系）
    DialogueEmotionRepository: Dialogue 节点 BERT 情绪仓储，提供原始对话明细查询（十情绪体系，情绪统计模块专用）
"""

from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta, timezone
import json

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.utils.datetime_utils import to_iso_z, utcnow_naive
from app.core.logging_config import get_business_logger

logger = get_business_logger()


def _to_naive_utc(value: Any) -> datetime:
    """Neo4j created_at 值 → naive UTC datetime

    兼容三种返回形态：neo4j DateTime（to_native）、原生 datetime、ISO 字符串。
    aware 时间统一转 UTC 后去 tzinfo（与 PG naive UTC 存储口径一致）。
    """
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


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
        end_user_id: str,
        emotion_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """获取情绪标签统计
        
        查询指定用户的情绪类型分布，包括计数、百分比和平均强度。
        
        Args:
            end_user_id: 用户组ID（宿主ID）
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
        where_clauses = ["s.end_user_id = $end_user_id", "s.emotion_type IS NOT NULL"]
        params = {"end_user_id": end_user_id, "limit": limit}
        
        if emotion_type:
            where_clauses.append("s.emotion_type = $emotion_type")
            params["emotion_type"] = emotion_type
        
        if start_date:
            where_clauses.append("s.created_at IS NOT NULL AND datetime(s.created_at) >= datetime($start_date)")
            params["start_date"] = start_date
        
        if end_date:
            where_clauses.append("s.created_at IS NOT NULL AND datetime(s.created_at) <= datetime($end_date)")
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
        end_user_id: str,
        emotion_type: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """获取情绪词云数据
        
        查询情绪关键词及其频率，用于生成词云可视化。
        
        Args:
            end_user_id: 用户组ID（宿主ID）
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
        where_clauses = ["s.end_user_id = $end_user_id", "s.emotion_keywords IS NOT NULL"]
        params = {"end_user_id": end_user_id, "limit": limit}
        
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
        end_user_id: str,
        time_range: str = "30d"
    ) -> List[Dict[str, Any]]:
        """获取时间范围内的情绪数据
        
        查询指定时间范围内的所有情绪数据，用于健康指数计算。
        
        Args:
            end_user_id: 用户组ID（宿主ID）
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
        start_date = to_iso_z(utcnow_naive() - timedelta(days=days))
        
        # 使用 datetime() 函数进行时间比较，与其他查询保持一致
        query = """
        MATCH (s:Statement)
        WHERE s.end_user_id = $end_user_id
          AND s.emotion_type IS NOT NULL
          AND s.created_at IS NOT NULL
          AND datetime(s.created_at) >= datetime($start_date)
        RETURN s.id as statement_id,
               s.emotion_type as emotion_type,
               s.emotion_intensity as emotion_intensity,
               s.created_at as created_at
        ORDER BY datetime(s.created_at) ASC
        """
        
        try:
            results = await self.connector.execute_query(
                query,
                end_user_id=end_user_id,
                start_date=start_date
            )
            formatted_results = [
                {
                    "statement_id": record["statement_id"],
                    "emotion_type": record["emotion_type"],
                    "emotion_intensity": record["emotion_intensity"],
                    "created_at": to_iso_z(record["created_at"]) if hasattr(record["created_at"], "isoformat") else str(record["created_at"])
                }
                for record in results
            ]
            
            return formatted_results
        except Exception as e:
            logger.error(f"查询时间范围情绪数据失败: {str(e)}", exc_info=True)
            return []


class DialogueEmotionRepository:
    """Dialogue 节点 BERT 情绪仓储

    情绪统计模块的数据源。与 EmotionRepository（Statement 节点、LLM 六情绪体系）
    完全独立：本仓储返回 Dialogue 节点的**原始对话明细**（id/created_at/emotion，
    BERT 十分类），由 Celery 任务 / 全量脚本 / 查询时实时补数写入
    PG dialogue_emotion_raw 表（一对话一行）。

    不做切日聚合：created_at 为 naive UTC 口径返回，切日/聚合由查询接口
    按请求时区实时完成（一次同步支持任意时区）。
    """

    def __init__(self, connector: Neo4jConnector):
        """初始化

        Args:
            connector: Neo4j连接器实例
        """
        self.connector = connector

    async def get_raw_dialogues(
        self,
        end_user_id: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> List[Dict[str, Any]]:
        """查询单个用户在 [start_dt, end_dt)（naive UTC）内的原始对话明细

        Args:
            end_user_id: 终端用户ID
            start_dt: 起始时间（naive UTC，含）
            end_dt: 结束时间（naive UTC，不含）

        Returns:
            List[Dict]: 每项包含：
                - id: Dialogue.id（幂等 Upsert 主键）
                - created_at: datetime 对话原始时刻（naive UTC）
                - emotion: 情绪枚举（BERT 十分类英文 code）
        """
        query = """
        MATCH (d:Dialogue)
        WHERE d.end_user_id = $end_user_id
          AND d.emotion IS NOT NULL
          AND d.created_at IS NOT NULL
          AND d.created_at >= $start_dt AND d.created_at < $end_dt
        RETURN d.id AS id, d.created_at AS created_at, d.emotion AS emotion
        """
        results = await self.connector.execute_query(
            query,
            end_user_id=end_user_id,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        return [
            {
                "id": record["id"],
                "created_at": _to_naive_utc(record["created_at"]),
                "emotion": record["emotion"],
            }
            for record in results
        ]
