"""
Memory Base Service

提供记忆服务的基础功能和共享辅助方法。
"""

from datetime import datetime
from typing import Optional

from app.core.logging_config import get_logger
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services.emotion_analytics_service import EmotionAnalyticsService

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
    
    async def get_episodic_memory_count(
        self,
        end_user_id: Optional[str] = None
    ) -> int:
        """
        获取情景记忆数量
        
        查询 MemorySummary 节点的数量。
        
        Args:
            end_user_id: 可选的终端用户ID，用于过滤特定用户的节点
            
        Returns:
            情景记忆的数量
        """
        try:
            if end_user_id:
                query = """
                MATCH (n:MemorySummary)
                WHERE n.group_id = $group_id
                RETURN count(n) as count
                """
                result = await self.neo4j_connector.execute_query(query, group_id=end_user_id)
            else:
                query = """
                MATCH (n:MemorySummary)
                RETURN count(n) as count
                """
                result = await self.neo4j_connector.execute_query(query)
            
            count = result[0]["count"] if result and len(result) > 0 else 0
            logger.debug(f"情景记忆数量: {count} (end_user_id={end_user_id})")
            return count
            
        except Exception as e:
            logger.error(f"获取情景记忆数量时出错: {str(e)}", exc_info=True)
            return 0
    
    async def get_explicit_memory_count(
        self,
        end_user_id: Optional[str] = None
    ) -> int:
        """
        获取显性记忆数量
        
        显性记忆 = 情景记忆（MemorySummary）+ 语义记忆（ExtractedEntity with is_explicit_memory=true）
        
        Args:
            end_user_id: 可选的终端用户ID，用于过滤特定用户的节点
            
        Returns:
            显性记忆的数量
        """
        try:
            # 1. 获取情景记忆数量
            episodic_count = await self.get_episodic_memory_count(end_user_id)
            
            # 2. 获取语义记忆数量（ExtractedEntity 且 is_explicit_memory = true）
            if end_user_id:
                semantic_query = """
                MATCH (e:ExtractedEntity)
                WHERE e.group_id = $group_id AND e.is_explicit_memory = true
                RETURN count(e) as count
                """
                semantic_result = await self.neo4j_connector.execute_query(
                    semantic_query, 
                    group_id=end_user_id
                )
            else:
                semantic_query = """
                MATCH (e:ExtractedEntity)
                WHERE e.is_explicit_memory = true
                RETURN count(e) as count
                """
                semantic_result = await self.neo4j_connector.execute_query(semantic_query)
            
            semantic_count = semantic_result[0]["count"] if semantic_result and len(semantic_result) > 0 else 0
            
            # 3. 计算总数
            explicit_count = episodic_count + semantic_count
            logger.debug(
                f"显性记忆数量: {explicit_count} "
                f"(情景={episodic_count}, 语义={semantic_count}, end_user_id={end_user_id})"
            )
            return explicit_count
            
        except Exception as e:
            logger.error(f"获取显性记忆数量时出错: {str(e)}", exc_info=True)
            return 0
    
    async def get_emotional_memory_count(
        self,
        end_user_id: Optional[str] = None,
        statement_count_fallback: int = 0
    ) -> int:
        """
        获取情绪记忆数量
        
        通过 EmotionAnalyticsService 获取情绪标签统计总数。
        如果获取失败或没有指定 end_user_id，使用 statement_count_fallback 作为后备。
        
        Args:
            end_user_id: 可选的终端用户ID
            statement_count_fallback: 后备方案的数量（通常是 statement 节点数量）
            
        Returns:
            情绪记忆的数量
        """
        try:
            if end_user_id:
                emotion_service = EmotionAnalyticsService()
                
                emotion_data = await emotion_service.get_emotion_tags(
                    end_user_id=end_user_id,
                    emotion_type=None,
                    start_date=None,
                    end_date=None,
                    limit=10
                )
                emotion_count = emotion_data.get("total_count", 0)
                logger.debug(f"情绪记忆数量: {emotion_count} (end_user_id={end_user_id})")
                return emotion_count
            else:
                # 如果没有指定 end_user_id，使用后备方案
                logger.debug(f"情绪记忆数量: {statement_count_fallback} (使用后备方案)")
                return statement_count_fallback
                
        except Exception as e:
            logger.warning(f"获取情绪记忆数量失败，使用后备方案: {str(e)}")
            return statement_count_fallback
    
    async def get_forget_memory_count(
        self,
        end_user_id: Optional[str] = None,
        forgetting_threshold: float = 0.3
    ) -> int:
        """
        获取遗忘记忆数量
        
        统计激活值低于遗忘阈值的节点数量（low_activation_nodes）。
        查询范围包括：Statement、ExtractedEntity、MemorySummary、Chunk 节点。
        
        Args:
            end_user_id: 可选的终端用户ID，用于过滤特定用户的节点
            forgetting_threshold: 遗忘阈值，默认 0.3
            
        Returns:
            遗忘记忆的数量（激活值低于阈值的节点数）
        """
        try:
            # 构建查询语句
            query = """
            MATCH (n)
            WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary OR n:Chunk)
            """
            
            if end_user_id:
                query += " AND n.group_id = $group_id"
            
            query += """
            RETURN sum(CASE WHEN n.activation_value IS NOT NULL AND n.activation_value < $threshold THEN 1 ELSE 0 END) as low_activation_nodes
            """
            
            # 设置查询参数
            params = {'threshold': forgetting_threshold}
            if end_user_id:
                params['group_id'] = end_user_id
            
            # 执行查询
            result = await self.neo4j_connector.execute_query(query, **params)
            
            # 提取结果
            forget_count = result[0]['low_activation_nodes'] if result and len(result) > 0 else 0
            forget_count = forget_count or 0  # 处理 None 值
            
            logger.debug(
                f"遗忘记忆数量: {forget_count} "
                f"(threshold={forgetting_threshold}, end_user_id={end_user_id})"
            )
            return forget_count
            
        except Exception as e:
            logger.error(f"获取遗忘记忆数量时出错: {str(e)}", exc_info=True)
            return 0
