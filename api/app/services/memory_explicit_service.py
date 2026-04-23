"""
显性记忆服务

处理显性记忆相关的业务逻辑，包括情景记忆和语义记忆的查询。
"""

from typing import Any, Dict, Optional

from app.core.logging_config import get_logger
from app.services.memory_base_service import MemoryBaseService

logger = get_logger(__name__)


class MemoryExplicitService(MemoryBaseService):
    """显性记忆服务类"""
    
    def __init__(self):
        super().__init__()
        logger.info("MemoryExplicitService initialized")
    
    async def get_explicit_memory_overview(
        self,
        end_user_id: str
    ) -> Dict[str, Any]:
        """
        获取显性记忆总览信息
        
        返回两部分：
        1. 情景记忆（episodic_memories）- 来自MemorySummary节点
        2. 语义记忆（semantic_memories）- 来自ExtractedEntity节点（is_explicit_memory=true）
        
        Args:
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
                        "core_definition": str
                    }
                ]
            }
        """
        try:
            logger.info(f"开始查询 end_user_id={end_user_id} 的显性记忆总览（情景记忆+语义记忆）")
            
            # ========== 1. 查询情景记忆（MemorySummary节点） ==========
            episodic_query = """
            MATCH (s:MemorySummary)
            WHERE s.end_user_id = $end_user_id
            RETURN elementId(s) AS id, 
                   s.name AS title,
                   s.content AS content,
                   s.created_at AS created_at
            ORDER BY s.created_at DESC
            """
            
            episodic_result = await self.neo4j_connector.execute_query(
                episodic_query, 
                end_user_id=end_user_id
            )
            
            # 处理情景记忆数据
            episodic_memories = []
            if episodic_result:
                for record in episodic_result:
                    summary_id = record["id"]
                    title = record.get("title") or "未命名"
                    content = record.get("content") or ""
                    created_at_str = record.get("created_at")
                    
                    # 使用基类方法转换时间戳
                    created_at_timestamp = self.parse_timestamp(created_at_str)
                    
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
            WHERE e.end_user_id = $end_user_id 
              AND e.is_explicit_memory = true
            RETURN elementId(e) AS id, 
                   e.name AS name,
                   e.entity_type AS entity_type,
                   e.description AS core_definition
            ORDER BY e.name ASC
            """

            semantic_result = await self.neo4j_connector.execute_query(
                semantic_query, 
                end_user_id=end_user_id
            )
            
            # 处理语义记忆数据
            semantic_memories = []
            if semantic_result:
                for record in semantic_result:
                    entity_id = record["id"]
                    name = record.get("name") or "未命名"
                    entity_type = record.get("entity_type") or "未分类"
                    core_definition = record.get("core_definition") or ""
                    
                    # 注意：总览接口不返回 detailed_notes 和 created_at 字段
                    semantic_memories.append({
                        "id": entity_id,
                        "name": name,
                        "entity_type": entity_type,
                        "core_definition": core_definition
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


    async def get_episodic_memory_list(
        self,
        end_user_id: str,
        page: int,
        pagesize: int,
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
        episodic_type: str = "all",
    ) -> Dict[str, Any]:
        """
        获取情景记忆分页列表

        Args:
            end_user_id: 终端用户ID
            page: 页码
            pagesize: 每页数量
            start_date: 开始时间戳（毫秒），可选
            end_date: 结束时间戳（毫秒），可选
            episodic_type: 情景类型筛选

        Returns:
            {
                "total": int,          # 该用户情景记忆总数（不受筛选影响）
                "items": [...],        # 当前页数据
                "page": {
                    "page": int,
                    "pagesize": int,
                    "total": int,      # 筛选后总数
                    "hasnext": bool
                }
            }
        """
        try:
            logger.info(
                f"情景记忆分页查询: end_user_id={end_user_id}, "
                f"start_date={start_date}, end_date={end_date}, "
                f"episodic_type={episodic_type}, page={page}, pagesize={pagesize}"
            )

            # 1. 查询情景记忆总数（不受筛选条件限制）
            total_all_query = """
            MATCH (s:MemorySummary)
            WHERE s.end_user_id = $end_user_id
            RETURN count(s) AS total
            """
            total_all_result = await self.neo4j_connector.execute_query(
                total_all_query, end_user_id=end_user_id
            )
            total_all = total_all_result[0]["total"] if total_all_result else 0

            # 2. 构建筛选条件
            where_clauses = ["s.end_user_id = $end_user_id"]
            params = {"end_user_id": end_user_id}

            # 时间戳筛选（毫秒时间戳转为 UTC ISO 字符串，使用 Neo4j datetime() 精确比较）
            if start_date is not None and end_date is not None:
                from datetime import datetime, timezone
                start_dt = datetime.fromtimestamp(start_date / 1000, tz=timezone.utc)
                end_dt = datetime.fromtimestamp(end_date / 1000, tz=timezone.utc)
                # 开始时间取当天 UTC 00:00:00，结束时间取当天 UTC 23:59:59.999999
                start_iso = start_dt.strftime("%Y-%m-%dT") + "00:00:00.000000"
                end_iso = end_dt.strftime("%Y-%m-%dT") + "23:59:59.999999"
            
                where_clauses.append("datetime(s.created_at) >= datetime($start_iso) AND datetime(s.created_at) <= datetime($end_iso)")
                params["start_iso"] = start_iso
                params["end_iso"] = end_iso

            # 类型筛选下推到 Cypher（兼容中英文）
            if episodic_type != "all":
                type_mapping = {
                    "conversation": "对话",
                    "project_work": "项目/工作",
                    "learning": "学习",
                    "decision": "决策",
                    "important_event": "重要事件"
                }
                chinese_type = type_mapping.get(episodic_type)
                if chinese_type:
                    where_clauses.append(
                        "(s.memory_type = $episodic_type OR s.memory_type = $chinese_type)"
                    )
                    params["episodic_type"] = episodic_type
                    params["chinese_type"] = chinese_type
                else:
                    where_clauses.append("s.memory_type = $episodic_type")
                    params["episodic_type"] = episodic_type

            where_str = " AND ".join(where_clauses)

            # 3. 查询筛选后的总数
            count_query = f"""
            MATCH (s:MemorySummary)
            WHERE {where_str}
            RETURN count(s) AS total
            """
            count_result = await self.neo4j_connector.execute_query(count_query, **params)
            filtered_total = count_result[0]["total"] if count_result else 0

            # 4. 查询分页数据
            skip = (page - 1) * pagesize
            data_query = f"""
            MATCH (s:MemorySummary)
            WHERE {where_str}
            RETURN elementId(s) AS id,
                s.name AS title,
                s.memory_type AS memory_type,
                s.content AS content,
                s.created_at AS created_at
            ORDER BY s.created_at DESC
            SKIP $skip LIMIT $limit
            """
            params["skip"] = skip
            params["limit"] = pagesize

            result = await self.neo4j_connector.execute_query(data_query, **params)

            # 5. 处理结果
            items = []
            if result:
                for record in result:
                    raw_created_at = record.get("created_at")
                    created_at_timestamp = self.parse_timestamp(raw_created_at)
                    items.append({
                        "id": record["id"],
                        "title": record.get("title") or "未命名",
                        "memory_type": record.get("memory_type") or "其他",
                        "content": record.get("content") or "",
                        "created_at": created_at_timestamp
                    })

            # 6. 构建返回结果
            return {
                "total": total_all,
                "items": items,
                "page": {
                    "page": page,
                    "pagesize": pagesize,
                    "total": filtered_total,
                    "hasnext": (page * pagesize) < filtered_total
                }
            }

        except Exception as e:
            logger.error(f"情景记忆分页查询出错: {str(e)}", exc_info=True)
            raise

    async def get_semantic_memory_list(
        self,
        end_user_id: str
    ) -> list:
        """
        获取语义记忆全量列表

        Args:
            end_user_id: 终端用户ID

        Returns:
            [
                {
                    "id": str,
                    "name": str,
                    "entity_type": str,
                    "core_definition": str
                }
            ]
        """
        try:
            logger.info(f"语义记忆列表查询: end_user_id={end_user_id}")

            semantic_query = """
            MATCH (e:ExtractedEntity)
            WHERE e.end_user_id = $end_user_id
            AND e.is_explicit_memory = true
            RETURN elementId(e) AS id,
                e.name AS name,
                e.entity_type AS entity_type,
                e.description AS core_definition
            ORDER BY e.name ASC
            """

            result = await self.neo4j_connector.execute_query(
                semantic_query, end_user_id=end_user_id
            )

            items = []
            if result:
                for record in result:
                    items.append({
                        "id": record["id"],
                        "name": record.get("name") or "未命名",
                        "entity_type": record.get("entity_type") or "未分类",
                        "core_definition": record.get("core_definition") or ""
                    })

            logger.info(f"语义记忆列表查询成功: end_user_id={end_user_id}, total={len(items)}")

            return items

        except Exception as e:
            logger.error(f"语义记忆列表查询出错: {str(e)}", exc_info=True)
            raise

    async def get_explicit_memory_details(
        self,
        end_user_id: str,
        memory_id: str
    ) -> Dict[str, Any]:
        """
        获取显性记忆详情
        
        根据 memory_id 查询情景记忆或语义记忆的详细信息。
        先尝试查询情景记忆，如果找不到再查询语义记忆。
        
        Args:
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
            WHERE elementId(s) = $memory_id AND s.end_user_id = $end_user_id
            RETURN s.name AS title,
                   s.content AS content,
                   s.created_at AS created_at
            """
            
            episodic_result = await self.neo4j_connector.execute_query(
                episodic_query,
                memory_id=memory_id,
                end_user_id=end_user_id
            )
            
            if episodic_result and len(episodic_result) > 0:
                record = episodic_result[0]
                title = record.get("title") or "未命名"
                content = record.get("content") or ""
                created_at_str = record.get("created_at")
                
                # 使用基类方法转换时间戳
                created_at_timestamp = self.parse_timestamp(created_at_str)
                
                # 使用基类方法获取情绪信息
                emotion = await self.extract_episodic_emotion(
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
              AND e.end_user_id = $end_user_id 
              AND e.is_explicit_memory = true
            RETURN e.name AS name,
                   e.description AS core_definition,
                   e.example AS detailed_notes,
                   e.created_at AS created_at
            """
            
            semantic_result = await self.neo4j_connector.execute_query(
                semantic_query,
                memory_id=memory_id,
                end_user_id=end_user_id
            )
            
            if semantic_result and len(semantic_result) > 0:
                record = semantic_result[0]
                name = record.get("name") or "未命名"
                core_definition = record.get("core_definition") or ""
                detailed_notes = record.get("detailed_notes") or ""
                created_at_str = record.get("created_at")
                
                # 使用基类方法转换时间戳
                created_at_timestamp = self.parse_timestamp(created_at_str)
                
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
