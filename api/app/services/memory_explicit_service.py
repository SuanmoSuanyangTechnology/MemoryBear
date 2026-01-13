"""
显性记忆服务

处理显性记忆相关的业务逻辑，包括情景记忆和语义记忆的查询。
"""

from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = get_logger(__name__)


class MemoryExplicitService:
    """显性记忆服务类"""
    
    def __init__(self):
        logger.info("MemoryExplicitService initialized")
        self.neo4j_connector = Neo4jConnector()
    
    async def get_explicit_memory_overview(
        self,
        db: Session,
        end_user_id: str
    ) -> Dict[str, Any]:
        """
        获取显性记忆总览信息
        
        返回两部分：
        1. 情景记忆（episodic_memories）- 来自MemorySummary节点
        2. 语义记忆（semantic_memories）- 来自ExtractedEntity节点（is_explicit_memory=true）
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID
        
        Returns:
            {
                "total": int,
                "episodic_memories": [
                    {
                        "id": str,
                        "title": str,
                        "content": str,
                        "created_at": int
                    }
                ],
                "semantic_memories": [
                    {
                        "id": str,
                        "name": str,
                        "entity_type": str,
                        "core_definition": str,
                        "created_at": int
                    }
                ]
            }
        """
        try:
            logger.info(f"开始查询 end_user_id={end_user_id} 的显性记忆总览（情景记忆+语义记忆）")
            
            # ========== 1. 查询情景记忆（MemorySummary节点） ==========
            episodic_query = """
            MATCH (s:MemorySummary)
            WHERE s.group_id = $group_id
            RETURN elementId(s) AS id, 
                   s.name AS title,
                   s.content AS content,
                   s.created_at AS created_at
            ORDER BY s.created_at DESC
            """
            
            episodic_result = await self.neo4j_connector.execute_query(
                episodic_query, 
                group_id=end_user_id
            )
            
            # 处理情景记忆数据
            episodic_memories = []
            if episodic_result:
                for record in episodic_result:
                    summary_id = record["id"]
                    title = record.get("title") or "未命名"
                    content = record.get("content") or ""
                    created_at_str = record.get("created_at")
                    
                    # 转换时间戳
                    created_at_timestamp = None
                    if created_at_str:
                        try:
                            from datetime import datetime
                            dt_object = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                            created_at_timestamp = int(dt_object.timestamp() * 1000)
                        except (ValueError, TypeError, AttributeError) as e:
                            logger.warning(f"无法解析时间戳: {created_at_str}, error={str(e)}")
                    
                    # 注意：总览接口不返回 emotion 字段
                    episodic_memories.append({
                        "id": summary_id,
                        "title": title,
                        "content": content,
                        "created_at": created_at_timestamp
                    })
            
            # ========== 2. 查询语义记忆（ExtractedEntity节点） ==========
            semantic_query = """
            MATCH (e:ExtractedEntity)
            WHERE e.group_id = $group_id 
              AND e.is_explicit_memory = true
            RETURN elementId(e) AS id, 
                   e.name AS name,
                   e.entity_type AS entity_type,
                   e.description AS core_definition,
                   e.example AS detailed_notes,
                   e.created_at AS created_at
            ORDER BY e.created_at DESC
            """
            
            semantic_result = await self.neo4j_connector.execute_query(
                semantic_query, 
                group_id=end_user_id
            )
            
            # 处理语义记忆数据
            semantic_memories = []
            if semantic_result:
                for record in semantic_result:
                    entity_id = record["id"]
                    name = record.get("name") or "未命名"
                    entity_type = record.get("entity_type") or "未分类"
                    core_definition = record.get("core_definition") or ""
                    created_at_str = record.get("created_at")
                    
                    # 转换时间戳
                    created_at_timestamp = None
                    if created_at_str:
                        try:
                            from datetime import datetime
                            dt_object = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                            created_at_timestamp = int(dt_object.timestamp() * 1000)
                        except (ValueError, TypeError, AttributeError) as e:
                            logger.warning(f"无法解析时间戳: {created_at_str}, error={str(e)}")
                    
                    # 注意：总览接口不返回 detailed_notes 字段
                    semantic_memories.append({
                        "id": entity_id,
                        "name": name,
                        "entity_type": entity_type,
                        "core_definition": core_definition,
                        "created_at": created_at_timestamp
                    })
            
            # ========== 3. 返回结果 ==========
            total_count = len(episodic_memories) + len(semantic_memories)
            
            logger.info(
                f"成功获取 end_user_id={end_user_id} 的显性记忆总览，"
                f"情景记忆={len(episodic_memories)} 条，语义记忆={len(semantic_memories)} 条，"
                f"总计 {total_count} 条"
            )
            
            return {
                "total": total_count,
                "episodic_memories": episodic_memories,
                "semantic_memories": semantic_memories
            }
            
        except Exception as e:
            logger.error(f"获取显性记忆总览时出错: {str(e)}", exc_info=True)
            raise

    async def get_explicit_memory_details(
        self,
        db: Session,
        end_user_id: str,
        memory_id: str
    ) -> Dict[str, Any]:
        """
        获取显性记忆详情
        
        根据 memory_id 查询情景记忆或语义记忆的详细信息。
        先尝试查询情景记忆，如果找不到再查询语义记忆。
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID
            memory_id: 记忆ID（可以是情景记忆或语义记忆的ID）
        
        Returns:
            情景记忆返回：
            {
                "memory_type": "episodic",
                "title": str,
                "content": str,
                "emotion": Dict,
                "created_at": int
            }
            
            语义记忆返回：
            {
                "memory_type": "semantic",
                "name": str,
                "core_definition": str,
                "detailed_notes": str,
                "created_at": int
            }
        
        Raises:
            ValueError: 当记忆不存在时
        """
        try:
            logger.info(f"开始查询显性记忆详情: end_user_id={end_user_id}, memory_id={memory_id}")
            
            # ========== 1. 先尝试查询情景记忆 ==========
            episodic_query = """
            MATCH (s:MemorySummary)
            WHERE elementId(s) = $memory_id AND s.group_id = $group_id
            RETURN s.name AS title,
                   s.content AS content,
                   s.created_at AS created_at
            """
            
            episodic_result = await self.neo4j_connector.execute_query(
                episodic_query,
                memory_id=memory_id,
                group_id=end_user_id
            )
            
            if episodic_result and len(episodic_result) > 0:
                record = episodic_result[0]
                title = record.get("title") or "未命名"
                content = record.get("content") or ""
                created_at_str = record.get("created_at")
                
                # 转换时间戳
                created_at_timestamp = None
                if created_at_str:
                    try:
                        from datetime import datetime
                        dt_object = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        created_at_timestamp = int(dt_object.timestamp() * 1000)
                    except (ValueError, TypeError, AttributeError) as e:
                        logger.warning(f"无法解析时间戳: {created_at_str}, error={str(e)}")
                
                # 获取情绪信息
                emotion = await self._extract_episodic_emotion(
                    summary_id=memory_id,
                    end_user_id=end_user_id
                )
                
                logger.info(f"成功获取情景记忆详情: memory_id={memory_id}")
                return {
                    "memory_type": "episodic",
                    "title": title,
                    "content": content,
                    "emotion": emotion,
                    "created_at": created_at_timestamp
                }
            
            # ========== 2. 如果不是情景记忆，尝试查询语义记忆 ==========
            semantic_query = """
            MATCH (e:ExtractedEntity)
            WHERE elementId(e) = $memory_id 
              AND e.group_id = $group_id 
              AND e.is_explicit_memory = true
            RETURN e.name AS name,
                   e.description AS core_definition,
                   e.example AS detailed_notes,
                   e.created_at AS created_at
            """
            
            semantic_result = await self.neo4j_connector.execute_query(
                semantic_query,
                memory_id=memory_id,
                group_id=end_user_id
            )
            
            if semantic_result and len(semantic_result) > 0:
                record = semantic_result[0]
                name = record.get("name") or "未命名"
                core_definition = record.get("core_definition") or ""
                detailed_notes = record.get("detailed_notes") or ""
                created_at_str = record.get("created_at")
                
                # 转换时间戳
                created_at_timestamp = None
                if created_at_str:
                    try:
                        from datetime import datetime
                        dt_object = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        created_at_timestamp = int(dt_object.timestamp() * 1000)
                    except (ValueError, TypeError, AttributeError) as e:
                        logger.warning(f"无法解析时间戳: {created_at_str}, error={str(e)}")
                
                logger.info(f"成功获取语义记忆详情: memory_id={memory_id}")
                return {
                    "memory_type": "semantic",
                    "name": name,
                    "core_definition": core_definition,
                    "detailed_notes": detailed_notes,
                    "created_at": created_at_timestamp
                }
            
            # ========== 3. 两种记忆都找不到 ==========
            logger.warning(f"记忆不存在: memory_id={memory_id}, end_user_id={end_user_id}")
            raise ValueError(f"记忆不存在: memory_id={memory_id}")
            
        except ValueError:
            # 重新抛出 ValueError（记忆不存在）
            raise
        except Exception as e:
            logger.error(f"获取显性记忆详情时出错: {str(e)}", exc_info=True)
            raise

    async def _extract_episodic_emotion(
        self,
        summary_id: str,
        end_user_id: str
    ) -> Optional[str]:
        """
        提取情景记忆的主要情绪
        
        Args:
            summary_id: Summary节点的ID
            end_user_id: 终端用户ID (group_id)
            
        Returns:
            最大emotion_intensity对应的emotion_type,如果没有则返回None
        """
        try:
            # 查询Summary节点指向的所有Statement节点
            # 筛选具有emotion_type属性的节点
            # 按emotion_intensity降序排序,返回第一个
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
            
            # 提取emotion_type
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
