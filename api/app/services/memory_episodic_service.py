"""
Episodic Memory Service

处理情景记忆相关的业务逻辑，包括情景记忆总览、详情查询等。
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz
from app.core.logging_config import get_logger
from app.services.memory_base_service import MemoryBaseService

logger = get_logger(__name__)


class MemoryEpisodicService(MemoryBaseService):
    """情景记忆服务类"""
    
    def __init__(self):
        super().__init__()
        logger.info("MemoryEpisodicService initialized")
    
    async def _get_title_and_type(
        self,
        summary_id: str,
        end_user_id: str
    ) -> Tuple[str, str]:
        """
        读取情景记忆的标题(title)和类型(type)
        
        仅负责读取已存在的title和type，不进行生成
        title从name属性读取，type从memory_type属性读取
        
        Args:
            summary_id: Summary节点的ID
            end_user_id: 终端用户ID (group_id)
            
        Returns:
            (标题, 类型)元组，如果不存在则返回默认值
        """
        try:
            # 查询Summary节点的name(作为title)和memory_type(作为type)
            query = """
            MATCH (s:MemorySummary)
            WHERE elementId(s) = $summary_id AND s.group_id = $group_id
            RETURN s.name AS title, s.memory_type AS type
            """
            
            result = await self.neo4j_connector.execute_query(
                query,
                summary_id=summary_id,
                group_id=end_user_id
            )
            
            if not result or len(result) == 0:
                logger.warning(f"未找到 summary_id={summary_id} 的节点")
                return ("未知标题", "其他")
            
            record = result[0]
            title = record.get("title") or "未命名"
            episodic_type = record.get("type") or "其他"
            
            return (title, episodic_type)
            
        except Exception as e:
            logger.error(f"读取标题和类型时出错: {str(e)}", exc_info=True)
            return ("错误", "其他")
    
    async def _extract_involved_objects(
        self,
        summary_id: str,
        end_user_id: str
    ) -> List[str]:
        """
        提取情景记忆涉及的前3个最重要实体
        
        Args:
            summary_id: Summary节点的ID
            end_user_id: 终端用户ID (group_id)
            
        Returns:
            前3个实体的name属性列表
        """
        try:
            # 查询Summary节点指向的Statement节点,再查询Statement指向的ExtractedEntity节点
            # 按activation_value降序排序,返回前3个
            query = """
            MATCH (s:MemorySummary)
            WHERE elementId(s) = $summary_id AND s.group_id = $group_id
            MATCH (s)-[:DERIVED_FROM_STATEMENT]->(stmt:Statement)
            MATCH (stmt)-[:REFERENCES_ENTITY]->(entity:ExtractedEntity)
            WHERE entity.activation_value IS NOT NULL
            RETURN DISTINCT entity.name AS name, entity.activation_value AS activation
            ORDER BY activation DESC
            LIMIT 3
            """
            
            result = await self.neo4j_connector.execute_query(
                query,
                summary_id=summary_id,
                group_id=end_user_id
            )
            
            # 提取实体名称
            involved_objects = [record["name"] for record in result if record.get("name")]
            
            logger.info(f"成功提取 summary_id={summary_id} 的涉及对象: {involved_objects}")
            
            return involved_objects
            
        except Exception as e:
            logger.error(f"提取涉及对象时出错: {str(e)}", exc_info=True)
            return []
    
    async def _extract_content_records(
        self,
        summary_id: str,
        end_user_id: str
    ) -> List[str]:
        """
        提取情景记忆的内容记录
        
        Args:
            summary_id: Summary节点的ID
            end_user_id: 终端用户ID (group_id)
            
        Returns:
            所有Statement节点的statement属性内容列表
        """
        try:
            # 查询Summary节点指向的所有Statement节点
            query = """
            MATCH (s:MemorySummary)
            WHERE elementId(s) = $summary_id AND s.group_id = $group_id
            MATCH (s)-[:DERIVED_FROM_STATEMENT]->(stmt:Statement)
            WHERE stmt.statement IS NOT NULL AND stmt.statement <> ''
            RETURN stmt.statement AS statement
            """
            
            result = await self.neo4j_connector.execute_query(
                query,
                summary_id=summary_id,
                group_id=end_user_id
            )
            
            # 提取statement内容
            content_records = [record["statement"] for record in result if record.get("statement")]
            
            logger.info(f"成功提取 summary_id={summary_id} 的内容记录,共 {len(content_records)} 条")
            
            return content_records
            
        except Exception as e:
            logger.error(f"提取内容记录时出错: {str(e)}", exc_info=True)
            return []
    
    def _calculate_time_filter(self, time_range: str) -> Optional[str]:
        """
        根据时间范围计算过滤的起始时间
        
        Args:
            time_range: 时间范围 (all/today/this_week/this_month)
            
        Returns:
            ISO格式的时间字符串，如果是"all"则返回None
        """
        if time_range == "all":
            return None
        
        # 获取当前时间（UTC）
        now = datetime.now(pytz.UTC)
        
        if time_range == "today":
            # 今天的开始时间（00:00:00）
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif time_range == "this_week":
            # 本周的开始时间（周一00:00:00）
            days_since_monday = now.weekday()
            start_time = (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif time_range == "this_month":
            # 本月的开始时间（1号00:00:00）
            start_time = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            return None
        
        # 返回ISO格式字符串
        return start_time.isoformat()
    
    async def get_episodic_memory_overview(
        self,
        end_user_id: str,
        time_range: str = "all",
        episodic_type: str = "all",
        title_keyword: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取情景记忆总览信息
        
        Args:
            end_user_id: 终端用户ID
            time_range: 时间范围筛选
            episodic_type: 情景类型筛选
            title_keyword: 标题关键词（可选，用于模糊搜索）
        """
        try:
            logger.info(
                f"开始查询 end_user_id={end_user_id} 的情景记忆总览, "
                f"time_range={time_range}, episodic_type={episodic_type}, title_keyword={title_keyword}"
            )
            
            # 1. 先查询所有情景记忆的总数（不受筛选条件限制）
            total_all_query = """
            MATCH (s:MemorySummary)
            WHERE s.group_id = $group_id
            RETURN count(s) AS total_all
            """
            total_all_result = await self.neo4j_connector.execute_query(
                total_all_query, 
                group_id=end_user_id
            )
            total_all = total_all_result[0]["total_all"] if total_all_result else 0
            
            # 2. 计算时间范围的起始时间戳
            time_filter = self._calculate_time_filter(time_range)
            
            # 3. 构建Cypher查询
            query = """
            MATCH (s:MemorySummary)
            WHERE s.group_id = $group_id
            """
            
            # 添加时间范围过滤
            if time_filter:
                query += " AND s.created_at >= $time_filter"
            
            # 添加标题关键词过滤（如果提供了title_keyword）
            if title_keyword:
                query += " AND toLower(s.name) CONTAINS toLower($title_keyword)"
            
            query += """
            RETURN elementId(s) AS id, 
                   s.created_at AS created_at,
                   s.memory_type AS type,
                   s.name AS title
            ORDER BY s.created_at DESC
            """
            
            params = {"group_id": end_user_id}
            if time_filter:
                params["time_filter"] = time_filter
            if title_keyword:
                params["title_keyword"] = title_keyword
            
            result = await self.neo4j_connector.execute_query(query, **params)
            
            # 4. 如果没有数据，返回空列表
            if not result:
                logger.info(f"end_user_id={end_user_id} 没有情景记忆数据")
                return {
                    "total": 0,
                    "total_all": total_all,
                    "episodic_memories": []
                }
            
            # 5. 对每个节点读取标题和类型，并应用类型筛选
            episodic_memories = []
            for record in result:
                summary_id = record["id"]
                created_at_str = record.get("created_at")
                memory_type = record.get("type", "其他")
                title = record.get("title") or "未命名"  # 直接从查询结果获取标题
                
                # 应用情景类型筛选
                if episodic_type != "all":
                    # 检查类型是否匹配
                    # 注意：Neo4j 中存储的 memory_type 现在应该是英文格式（如 "conversation", "project_work"）
                    # 但为了兼容旧数据，我们也支持中文格式的匹配
                    type_mapping = {
                        "conversation": "对话",
                        "project_work": "项目/工作",
                        "learning": "学习",
                        "decision": "决策",
                        "important_event": "重要事件"
                    }
                    
                    # 获取对应的中文类型（用于兼容旧数据）
                    chinese_type = type_mapping.get(episodic_type)
                    
                    # 检查类型是否匹配（支持新的英文格式和旧的中文格式）
                    if memory_type != episodic_type and memory_type != chinese_type:
                        continue
                
                # 使用基类方法转换时间戳
                created_at_timestamp = self.parse_timestamp(created_at_str)
                
                episodic_memories.append({
                    "id": summary_id,
                    "title": title,
                    "type": memory_type,
                    "created_at": created_at_timestamp
                })
            
            logger.info(
                f"成功获取 end_user_id={end_user_id} 的情景记忆总览,"
                f"筛选后 {len(episodic_memories)} 条，总共 {total_all} 条"
            )
            
            return {
                "total": len(episodic_memories),
                "total_all": total_all,
                "episodic_memories": episodic_memories
            }
            
        except Exception as e:
            logger.error(f"获取情景记忆总览时出错: {str(e)}", exc_info=True)
            raise
    
    async def get_episodic_memory_details(
        self,
        end_user_id: str,
        summary_id: str
    ) -> Dict[str, Any]:
        """
        获取单个情景记忆的详细信息
        
        """
        try:
            logger.info(f"开始查询 end_user_id={end_user_id}, summary_id={summary_id} 的情景记忆详情")
            
            # 1. 查询指定的MemorySummary节点
            query = """
            MATCH (s:MemorySummary)
            WHERE elementId(s) = $summary_id AND s.group_id = $group_id
            RETURN elementId(s) AS id, s.created_at AS created_at
            """
            
            result = await self.neo4j_connector.execute_query(
                query,
                summary_id=summary_id,
                group_id=end_user_id
            )
            
            # 2. 如果节点不存在，返回错误
            if not result or len(result) == 0:
                logger.warning(f"未找到 summary_id={summary_id} 的情景记忆")
                raise ValueError(f"情景记忆不存在: summary_id={summary_id}")
            
            # 3. 获取基本信息
            record = result[0]
            created_at_str = record.get("created_at")
            
            # 使用基类方法转换时间戳
            created_at_timestamp = self.parse_timestamp(created_at_str)
            
            # 4. 调用_get_title_and_type读取标题和类型
            title, episodic_type = await self._get_title_and_type(
                summary_id=summary_id,
                end_user_id=end_user_id
            )
            
            # 5. 调用_extract_involved_objects提取涉及对象
            involved_objects = await self._extract_involved_objects(
                summary_id=summary_id,
                end_user_id=end_user_id
            )
            
            # 6. 调用_extract_content_records提取内容记录
            content_records = await self._extract_content_records(
                summary_id=summary_id,
                end_user_id=end_user_id
            )
            
            # 7. 使用基类方法提取情绪
            emotion = await self.extract_episodic_emotion(
                summary_id=summary_id,
                end_user_id=end_user_id
            )
            
            # 8. 返回完整的详情信息
            details = {
                "id": summary_id,
                "created_at": created_at_timestamp,
                "involved_objects": involved_objects,
                "episodic_type": episodic_type,
                "content_records": content_records,
                "emotion": emotion
            }
            
            logger.info(f"成功获取 summary_id={summary_id} 的情景记忆详情")
            
            return details
            
        except ValueError:
            # 重新抛出ValueError，让Controller层处理
            raise
        except Exception as e:
            logger.error(f"获取情景记忆详情时出错: {str(e)}", exc_info=True)
            raise


# 创建全局服务实例（供控制器层使用）
memory_episodic_service = MemoryEpisodicService()
