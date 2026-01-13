"""
Memory Base Service

提供记忆服务的基础功能和共享辅助方法。
"""

from datetime import datetime
from typing import Optional

from app.core.logging_config import get_logger
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = get_logger(__name__)


class MemoryBaseService:
    """记忆服务基类，提供共享的辅助方法"""
    
    def __init__(self):
        self.neo4j_connector = Neo4jConnector()
    
    @staticmethod
    def parse_timestamp(timestamp_value) -> Optional[int]:
        """
        将时间戳转换为毫秒级时间戳
        
        支持多种输入格式：
        - Neo4j DateTime 对象
        - ISO格式的时间戳字符串
        - Python datetime 对象
        
        Args:
            timestamp_value: 时间戳值（可以是多种类型）
            
        Returns:
            毫秒级时间戳，如果解析失败则返回None
        """
        if not timestamp_value:
            return None
        
        try:
            # 处理 Neo4j DateTime 对象
            if hasattr(timestamp_value, 'to_native'):
                dt_object = timestamp_value.to_native()
                return int(dt_object.timestamp() * 1000)
            
            # 处理 Python datetime 对象
            if isinstance(timestamp_value, datetime):
                return int(timestamp_value.timestamp() * 1000)
            
            # 处理字符串格式
            if isinstance(timestamp_value, str):
                dt_object = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
                return int(dt_object.timestamp() * 1000)
            
            # 其他情况尝试转换为字符串再解析
            dt_object = datetime.fromisoformat(str(timestamp_value).replace("Z", "+00:00"))
            return int(dt_object.timestamp() * 1000)
            
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(f"无法解析时间戳: {timestamp_value}, error={str(e)}")
            return None
    
    async def extract_episodic_emotion(
        self,
        summary_id: str,
        end_user_id: str
    ) -> Optional[str]:
        """
        提取情景记忆的主要情绪
        
        查询MemorySummary节点关联的Statement节点，
        返回emotion_intensity最大的emotion_type。
        
        Args:
            summary_id: Summary节点的ID
            end_user_id: 终端用户ID (group_id)
            
        Returns:
            最大emotion_intensity对应的emotion_type，如果没有则返回None
        """
        try:
            query = """
            MATCH (s:MemorySummary)
            WHERE elementId(s) = $summary_id AND s.group_id = $group_id
            MATCH (s)-[:DERIVED_FROM_STATEMENT]->(stmt:Statement)
            WHERE stmt.emotion_type IS NOT NULL 
              AND stmt.emotion_intensity IS NOT NULL
            RETURN stmt.emotion_type AS emotion_type, 
                   stmt.emotion_intensity AS emotion_intensity
            ORDER BY emotion_intensity DESC
            LIMIT 1
            """
            
            result = await self.neo4j_connector.execute_query(
                query,
                summary_id=summary_id,
                group_id=end_user_id
            )
            
            if result and len(result) > 0:
                emotion_type = result[0].get("emotion_type")
                logger.info(f"成功提取 summary_id={summary_id} 的情绪: {emotion_type}")
                return emotion_type
            else:
                logger.info(f"summary_id={summary_id} 没有情绪信息")
                return None
            
        except Exception as e:
            logger.error(f"提取情景记忆情绪时出错: {str(e)}", exc_info=True)
            return None
