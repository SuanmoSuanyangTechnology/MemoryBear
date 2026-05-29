"""
User Memory Service

处理用户记忆相关的业务逻辑，包括记忆洞察、用户摘要、节点统计和图数据等。
"""

import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.core.memory.constants.graph_data_constants import (
    DEPTH_HARD_MAX,
    NODE_PROPERTY_WHITELIST,
    SUPPORTED_NODE_TYPES,
    _DEFAULT_FIELDS,
)
from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import _USER_PLACEHOLDER_NAMES
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.neo4j.cypher_queries import Graph_Node_query
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services._graph_data_helpers import (
    _apply_total_cap_shrink,
    _query_nodes_by_type_limits,
    _query_rel_count_batch,
    _query_total_count_by_type,
    _resolve_per_type_limits,
)
from app.schemas.memory_episodic_schema import EmotionSubject, EmotionType, type_mapping
from app.services.memory_base_service import MemoryBaseService, MIN_MEMORY_SUMMARY_COUNT
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_perceptual_service import MemoryPerceptualService
from app.services.memory_short_service import ShortService

logger = get_logger(__name__)

# Neo4j connector instance for analytics functions
_neo4j_connector = Neo4jConnector()

# Default LLM ID for fallback
DEFAULT_LLM_ID = os.getenv("SELECTED_LLM_ID", "openai/qwen-plus")


# ============================================================================
# Internal Helper Classes
# ============================================================================

class TagClassification(BaseModel):
    """Represents the classification of a tag into a specific domain."""
    domain: str = Field(
        ...,
        description="The domain the tag belongs to, chosen from the predefined list.",
        examples=["教育", "学习", "工作", "旅行", "家庭", "运动", "社交", "娱乐", "健康", "其他"],
    )


def _get_llm_client_for_user(user_id: str):
    """
    Get LLM client for a specific user based on their config.
    
    Args:
        user_id: User ID to get config for
        
    Returns:
        LLM client instance
    """
    with get_db_context() as db:
        try:
            from app.services.memory_agent_service import get_end_user_connected_config
            connected_config = get_end_user_connected_config(user_id, db)
            config_id = connected_config.get("memory_config_id")
            workspace_id = connected_config.get("workspace_id")
            
            if config_id or workspace_id:
                config_service = MemoryConfigService(db)
                memory_config = config_service.load_memory_config(
                    config_id=config_id,
                    workspace_id=workspace_id
                )
                factory = MemoryClientFactory(db)
                return factory.get_llm_client(memory_config.llm_model_id)
            else:
                factory = MemoryClientFactory(db)
                return factory.get_llm_client(DEFAULT_LLM_ID)
        except Exception as e:
            logger.warning(f"Failed to get user connected config, using default LLM: {e}")
            factory = MemoryClientFactory(db)
            return factory.get_llm_client(DEFAULT_LLM_ID)


class MemoryInsightHelper:
    """
    Internal helper class for memory insight analysis.
    Provides basic data retrieval and analysis functionality.
    """
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.neo4j_connector = Neo4jConnector()
        self.llm_client = _get_llm_client_for_user(user_id)
    
    async def close(self):
        """Close database connection."""
        await self.neo4j_connector.close()
    
    async def get_domain_distribution(self) -> dict[str, float]:
        """Calculate the distribution of memory domains based on hot tags."""
        from app.core.memory.analytics.hot_memory_tags import get_hot_memory_tags
        
        hot_tags = await get_hot_memory_tags(self.user_id)
        if not hot_tags:
            return {}
        
        domain_counts = Counter()
        for tag, _ in hot_tags:
            prompt = f"""请将以下标签归类到最合适的领域中。

可选领域及其关键词：
- 教育：学校、课程、考试、培训、教学、学科、教师、学生、班级、作业、成绩、毕业、入学、校园、大学、中学、小学、教材、学位等
- 学习：自学、阅读、书籍、技能提升、知识积累、笔记、复习、练习、研究、历史知识、科学知识、文化知识、学术讨论、知识问答等
- 工作：职业、项目、会议、同事、业务、公司、办公、任务、客户、合同、职场、工作计划等
- 旅行：旅游、景点、出行、度假、酒店、机票、导游、风景、旅行计划等
- 家庭：亲人、父母、子女、配偶、家事、家庭活动、亲情、家庭聚会等
- 运动：健身、体育、锻炼、跑步、游泳、球类、瑜伽、运动计划等
- 社交：朋友、聚会、社交活动、派对、聊天、交友、社交网络等
- 娱乐：游戏、电影、音乐、休闲、综艺、动漫、小说、娱乐活动等
- 健康：医疗、养生、心理健康、体检、药物、疾病、保健、健康管理等
- 其他：确实无法归入以上任何类别的内容

标签: {tag}

分析步骤：
1. 仔细理解标签的核心含义和使用场景
2. 对比各个领域的关键词，找到最匹配的领域
3. 特别注意：
   - 历史、科学、文化等知识性内容应归类为"学习"
   - 学校、课程、考试等正式教育场景应归类为"教育"
   - 只有在标签完全不属于上述9个具体领域时，才选择"其他"
4. 如果标签与某个领域有任何相关性，就选择该领域，不要选"其他"

请直接返回最合适的领域名称。"""
            messages = [
                {"role": "system", "content": "你是一个专业的标签分类助手。你必须仔细分析标签的实际含义和使用场景，优先选择9个具体领域之一。'其他'类别只用于完全无法归类的极少数情况。特别注意：历史、科学、文化等知识性对话应归类为'学习'领域；学校、课程、考试等正式教育场景应归类为'教育'领域。"},
                {"role": "user", "content": prompt}
            ]
            classification = await self.llm_client.response_structured(
                messages=messages,
                response_model=TagClassification,
            )
            if classification and hasattr(classification, 'domain') and classification.domain:
                domain_counts[classification.domain] += 1
        
        total_tags = sum(domain_counts.values())
        if total_tags == 0:
            return {}
        
        domain_distribution = {
            domain: count / total_tags for domain, count in domain_counts.items()
        }
        return dict(sorted(domain_distribution.items(), key=lambda item: item[1], reverse=True))
    
    async def get_active_periods(self) -> list[int]:
        """
        Identify the top 2 most active months for the user.
        Only returns months if there is valid and diverse time data.
        """
        query = """
        MATCH (d:Dialogue)
        WHERE d.end_user_id = $end_user_id AND d.created_at IS NOT NULL AND d.created_at <> ''
        RETURN d.created_at AS creation_time
        """
        records = await self.neo4j_connector.execute_query(query, end_user_id=self.user_id)
        
        if not records:
            return []
        
        month_counts = Counter()
        valid_dates_count = 0
        for record in records:
            creation_time = record.get("creation_time")
            if not creation_time:
                continue
            try:
                # 处理 Neo4j DateTime 对象或字符串
                if hasattr(creation_time, 'to_native'):
                    dt_object = creation_time.to_native()
                elif isinstance(creation_time, str):
                    dt_object = datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
                elif isinstance(creation_time, datetime):
                    dt_object = creation_time
                else:
                    dt_object = datetime.fromisoformat(str(creation_time).replace("Z", "+00:00"))
                
                month_counts[dt_object.month] += 1
                valid_dates_count += 1
            except (ValueError, TypeError, AttributeError):
                continue
        
        if not month_counts or valid_dates_count == 0:
            return []
        
        # Check if time distribution is too concentrated (likely batch imported data)
        unique_months = len(month_counts)
        if unique_months <= 2:
            most_common_count = month_counts.most_common(1)[0][1]
            if most_common_count / valid_dates_count > 0.8:
                return []
        
        if unique_months >= 3:
            most_common_months = month_counts.most_common(2)
            return [month for month, _ in most_common_months]
        
        if unique_months == 2:
            counts = list(month_counts.values())
            ratio = min(counts) / max(counts)
            if ratio > 0.3:
                most_common_months = month_counts.most_common(2)
                return [month for month, _ in most_common_months]
        
        return []
    
    async def get_social_connections(self) -> dict | None:
        """Find the user with whom the most memories are shared."""
        query = """
        MATCH (c1:Chunk {end_user_id: $end_user_id})
        OPTIONAL MATCH (c1)-[:CONTAINS]->(s:Statement)
        OPTIONAL MATCH (s)<-[:CONTAINS]-(c2:Chunk)
        WHERE c1.end_user_id <> c2.end_user_id AND s IS NOT NULL AND c2 IS NOT NULL
        WITH c2.end_user_id AS other_user_id, COUNT(DISTINCT s) AS common_statements
        WHERE common_statements > 0
        RETURN other_user_id, common_statements
        ORDER BY common_statements DESC
        LIMIT 1
        """
        records = await self.neo4j_connector.execute_query(query, end_user_id=self.user_id)
        if not records or not records[0].get("other_user_id"):
            return None
        
        most_connected_user = records[0]["other_user_id"]
        common_memories_count = records[0]["common_statements"]
        
        time_range_query = """
        MATCH (c:Chunk)
        WHERE c.end_user_id IN [$user_id, $other_user_id]
        RETURN min(c.created_at) AS start_time, max(c.created_at) AS end_time
        """
        time_records = await self.neo4j_connector.execute_query(
            time_range_query, 
            user_id=self.user_id, 
            other_user_id=most_connected_user
        )
        start_year, end_year = "N/A", "N/A"
        if time_records and time_records[0]["start_time"]:
            start_time = time_records[0]["start_time"]
            end_time = time_records[0]["end_time"]
            
            # 处理 Neo4j DateTime 对象或字符串
            try:
                if hasattr(start_time, 'to_native'):
                    start_year = start_time.to_native().year
                elif isinstance(start_time, str):
                    start_year = datetime.fromisoformat(start_time.replace("Z", "+00:00")).year
                elif isinstance(start_time, datetime):
                    start_year = start_time.year
                else:
                    start_year = datetime.fromisoformat(str(start_time).replace("Z", "+00:00")).year
            except Exception:
                start_year = "N/A"
            
            try:
                if hasattr(end_time, 'to_native'):
                    end_year = end_time.to_native().year
                elif isinstance(end_time, str):
                    end_year = datetime.fromisoformat(end_time.replace("Z", "+00:00")).year
                elif isinstance(end_time, datetime):
                    end_year = end_time.year
                else:
                    end_year = datetime.fromisoformat(str(end_time).replace("Z", "+00:00")).year
            except Exception:
                end_year = "N/A"
        
        return {
            "user_id": most_connected_user,
            "common_memories_count": common_memories_count,
            "time_range": f"{start_year}-{end_year}",
        }


class UserSummaryHelper:
    """
    Internal helper class for user summary generation.
    Provides data retrieval functionality for user summary analysis.
    """
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.connector = Neo4jConnector()
        self.llm = _get_llm_client_for_user(user_id)
    
    async def close(self):
        """Close database connection."""
        await self.connector.close()
    
    async def get_recent_statements(self, limit: int = 80) -> List[Dict[str, Any]]:
        """Fetch recent statements authored by the user/group for context."""
        query = (
            "MATCH (s:Statement) "
            "WHERE s.end_user_id = $end_user_id AND s.statement IS NOT NULL "
            "RETURN s.statement AS statement, s.created_at AS created_at "
            "ORDER BY created_at DESC LIMIT $limit"
        )
        rows = await self.connector.execute_query(query, end_user_id=self.user_id, limit=limit)
        records = []
        for r in rows:
            try:
                records.append({
                    "statement": r.get("statement", ""),
                    "created_at": r.get("created_at")
                })
            except Exception:
                continue
        return records
    
    async def get_top_entities(self, limit: int = 30) -> List[Tuple[str, int]]:
        """Get meaningful entities and their frequencies using hot tag logic."""
        from app.core.memory.analytics.hot_memory_tags import get_hot_memory_tags
        return await get_hot_memory_tags(self.user_id, limit=limit)


# ============================================================================
# Service Class
# ============================================================================


class UserMemoryService:
    """用户记忆服务类"""
    
    def __init__(self):
        logger.info("UserMemoryService initialized")
        self.neo4j_connector = Neo4jConnector()
    
    @staticmethod
    def _datetime_to_timestamp(dt: Optional[Any]) -> Optional[int]:
        """将 DateTime 对象转换为时间戳（毫秒）"""
        if dt is None:
            return None
        if hasattr(dt, 'timestamp'):
            return int(dt.timestamp() * 1000)
        return None
    
    @staticmethod
    def convert_profile_to_dict_with_timestamp(profile_data: Any) -> dict:
        """
        将 Pydantic 模型转换为字典，自动转换所有 DateTime 字段为时间戳（毫秒）
        
        Args:
            profile_data: Pydantic 模型对象
            
        Returns:
            包含时间戳的字典
        """
        data = profile_data.model_dump()
        # 自动转换所有 datetime 类型的字段
        for key, value in data.items():
            if hasattr(profile_data, key):
                original_value = getattr(profile_data, key)
                if hasattr(original_value, 'timestamp'):
                    data[key] = UserMemoryService._datetime_to_timestamp(original_value)
        return data
 # ======================== 用户别名及信息 ========================    
    def get_end_user_info(
        self,
        db: Session,
        end_user_id: str
    ) -> Dict[str, Any]:
        """
        查询单个终端用户信息记录
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            
        Returns:
            {
                "success": bool,
                "data": dict,
                "error": Optional[str]
            }
        """
        try:
            from app.repositories.end_user_info_repository import EndUserInfoRepository
            from app.core.api_key_utils import datetime_to_timestamp
            
            # 转换为UUID并查询
            user_uuid = uuid.UUID(end_user_id)
            end_user_info_record = EndUserInfoRepository(db).get_by_end_user_id(user_uuid)
            
            if not end_user_info_record:
                logger.warning(f"终端用户信息记录不存在: end_user_id={end_user_id}")
                return {
                    "success": False,
                    "data": None,
                    "error": "终端用户信息记录不存在"
                }
            
            # 构建响应数据（转换时间为毫秒时间戳）
            # meta_data 只暴露四个核心字段
            _META_FIELDS = ("goals", "traits", "interests", "core_facts")
            raw_meta = end_user_info_record.meta_data or {}
            filtered_meta = {k: raw_meta[k] for k in _META_FIELDS if k in raw_meta}

            response_data = {
                "end_user_info_id": str(end_user_info_record.id),
                "end_user_id": str(end_user_info_record.end_user_id),
                "other_name": end_user_info_record.other_name,
                "aliases": end_user_info_record.aliases,
                "meta_data": filtered_meta,
                "created_at": datetime_to_timestamp(end_user_info_record.created_at),
                "updated_at": datetime_to_timestamp(end_user_info_record.updated_at)
            }
            
            logger.info(f"成功查询终端用户信息记录: end_user_id={end_user_id}")
            
            return {
                "success": True,
                "data": response_data,
                "error": None
            }
            
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "success": False,
                "data": None,
                "error": "无效的终端用户ID格式"
            }
        except Exception as e:
            logger.error(f"查询终端用户信息记录失败: end_user_id={end_user_id}, error={str(e)}")
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }
    
    def update_end_user_info(
        self,
        db: Session,
        end_user_id: str,
        update_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        更新终端用户信息记录
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            update_data: 更新数据字典
            
        Returns:
            {
                "success": bool,
                "data": dict,
                "error": Optional[str]
            }
        """
        try:
            from app.repositories.end_user_info_repository import EndUserInfoRepository
            from app.repositories.end_user_repository import EndUserRepository
            from app.core.api_key_utils import datetime_to_timestamp
            
            # 转换为UUID并查询
            user_uuid = uuid.UUID(end_user_id)
            end_user_info_record = EndUserInfoRepository(db).get_by_end_user_id(user_uuid)
            
            if not end_user_info_record:
                logger.warning(f"终端用户信息记录不存在: end_user_id={end_user_id}")
                return {
                    "success": False,
                    "data": None,
                    "error": "终端用户信息记录不存在"
                }
            
            # 定义允许更新的字段白名单
            allowed_fields = {'other_name', 'aliases', 'meta_data'}
            
            # 用户占位名称黑名单，不允许作为 other_name 或出现在 aliases 中
            _user_placeholder_names = _USER_PLACEHOLDER_NAMES
            
            # 过滤 other_name：不允许设置为占位名称
            if 'other_name' in update_data and update_data['other_name'] and update_data['other_name'].strip() in _user_placeholder_names:
                logger.warning(f"拒绝将占位名称 '{update_data['other_name']}' 设置为 other_name")
                del update_data['other_name']
            
            # 过滤 aliases：移除占位名称和非字符串值
            if 'aliases' in update_data and update_data['aliases']:
                update_data['aliases'] = [
                    a for a in update_data['aliases']
                    if isinstance(a, str) and a.strip() and a.strip() not in _user_placeholder_names
                ]
            
            # 检查是否更新了 aliases 字段
            aliases_updated = 'aliases' in update_data and update_data['aliases'] != end_user_info_record.aliases
            
            # 检查是否更新了 other_name 字段
            other_name_updated = 'other_name' in update_data and update_data['other_name'] != end_user_info_record.other_name
            
            # 更新字段（仅允许白名单中的字段）
            for field, value in update_data.items():
                if field in allowed_fields:
                    setattr(end_user_info_record, field, value)
            
            # 更新时间戳
            end_user_info_record.updated_at = datetime.now()
            
            # 如果 other_name 被更新，同步更新 end_user 表
            if other_name_updated:
                end_user_record = EndUserRepository(db).get_by_id(user_uuid)
                if end_user_record:
                    end_user_record.other_name = update_data['other_name']
                    end_user_record.updated_at = datetime.now()
                    logger.info(f"同步更新 end_user 表的 other_name: end_user_id={end_user_id}, other_name={update_data['other_name']}")
                else:
                    logger.warning(f"未找到对应的 end_user 记录: end_user_id={end_user_id}")
            
            # 提交更改
            db.commit()
            db.refresh(end_user_info_record)
            
            # 如果 aliases 被更新，同步到 Neo4j
            if aliases_updated:
                try:
                    import asyncio
                    asyncio.run(self._sync_aliases_to_neo4j(end_user_id, update_data['aliases']))
                    logger.info(f"已触发 aliases 同步到 Neo4j: end_user_id={end_user_id}, aliases={update_data['aliases']}")
                except Exception as sync_error:
                    logger.error(f"触发同步 aliases 到 Neo4j 失败: {sync_error}", exc_info=True)
                    # 不影响主流程，只记录错误
            
            # 构建响应数据（转换时间为毫秒时间戳）
            response_data = {
                "end_user_info_id": str(end_user_info_record.id),
                "end_user_id": str(end_user_info_record.end_user_id),
                "other_name": end_user_info_record.other_name,
                "aliases": end_user_info_record.aliases,
                "meta_data": end_user_info_record.meta_data,
                "created_at": datetime_to_timestamp(end_user_info_record.created_at),
                "updated_at": datetime_to_timestamp(end_user_info_record.updated_at)
            }
            
            logger.info(f"成功更新终端用户信息记录: end_user_id={end_user_id}, updated_fields={list(update_data.keys())}")
            
            return {
                "success": True,
                "data": response_data,
                "error": None
            }
            
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "success": False,
                "data": None,
                "error": "无效的终端用户ID格式"
            }
        except Exception as e:
            db.rollback()
            logger.error(f"更新终端用户信息记录失败: end_user_id={end_user_id}, error={str(e)}")
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }
    
    async def _sync_aliases_to_neo4j(self, end_user_id: str, aliases: List[str]) -> None:
        """
        将 aliases 同步到 Neo4j 中的用户实体
        
        Args:
            end_user_id: 终端用户ID
            aliases: 别名列表
        """
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        
        # Cypher 查询：更新用户实体的 aliases
        cypher_query = """
        MATCH (e:ExtractedEntity)
        WHERE e.end_user_id = $end_user_id 
          AND e.name IN ['用户', '我', 'User', 'I']
        SET e.aliases = $aliases
        RETURN e.id AS entity_id, e.name AS entity_name, e.aliases AS updated_aliases
        """
        
        connector = Neo4jConnector()
        try:
            result = await connector.execute_query(
                cypher_query,
                end_user_id=end_user_id,
                aliases=aliases
            )
            
            if result:
                logger.info(f"成功同步 aliases 到 Neo4j: end_user_id={end_user_id}, 更新了 {len(result)} 个实体节点")
            else:
                logger.warning(f"未找到需要更新的用户实体节点: end_user_id={end_user_id}")
                
        except Exception as e:
            logger.error(f"同步 aliases 到 Neo4j 失败: {e}", exc_info=True)
            raise
    
    async def get_cached_memory_insight(
        self, 
        db: Session, 
        end_user_id: str
    ) -> Dict[str, Any]:
        """
        从数据库获取缓存的记忆洞察（四个维度）
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
        
        Returns:
            {
                "memory_insight": str,           # 总体概述
                "behavior_pattern": str,         # 行为模式
                "key_findings": List[str],       # 关键发现（数组）
                "growth_trajectory": str,        # 成长轨迹
                "updated_at": int,               # 时间戳（毫秒）
                "is_cached": bool
            }
        """
        try:
            # 转换为UUID并查询用户
            user_uuid = uuid.UUID(end_user_id)
            repo = EndUserRepository(db)
            end_user = repo.get_by_id(user_uuid)
            
            if not end_user:
                logger.warning(f"未找到 end_user_id 为 {end_user_id} 的用户")
                return {
                    "memory_insight": None,
                    "behavior_pattern": None,
                    "key_findings": None,
                    "growth_trajectory": None,
                    "updated_at": None,
                    "is_cached": False,
                    "message": "用户不存在"
                }
            
            # 检查是否有缓存数据（至少有一个字段不为空）
            has_cache = any([
                end_user.memory_insight,
                end_user.behavior_pattern,
                end_user.key_findings,
                end_user.growth_trajectory
            ])
            
            if has_cache:
                # 反序列化 key_findings（从 JSON 字符串转为数组）
                key_findings_value = end_user.key_findings
                if key_findings_value:
                    try:
                        import json
                        key_findings_array = json.loads(key_findings_value)
                    except (json.JSONDecodeError, TypeError):
                        # 如果解析失败，尝试按 • 分割（兼容旧数据）
                        key_findings_array = [item.strip() for item in key_findings_value.split('•') if item.strip()]
                else:
                    key_findings_array = []
                
                logger.info(f"成功获取 end_user_id {end_user_id} 的缓存记忆洞察（四维度）")
                memory_insight=end_user.memory_insight
                behavior_pattern=end_user.behavior_pattern
                growth_trajectory=end_user.growth_trajectory
                return {
                    "memory_insight":memory_insight,  # 总体概述存储在 memory_insight
                    "behavior_pattern":behavior_pattern,
                    "key_findings": key_findings_array,  # 返回数组
                    "growth_trajectory": growth_trajectory,
                    "updated_at": self._datetime_to_timestamp(end_user.memory_insight_updated_at),
                    "is_cached": True
                }
            else:
                logger.info(f"end_user_id {end_user_id} 的记忆洞察缓存为空")
                return {
                    "memory_insight": None,
                    "behavior_pattern": None,
                    "key_findings": None,
                    "growth_trajectory": None,
                    "updated_at": None,
                    "is_cached": False,
                    "message": "数据尚未生成，请稍后重试或联系管理员"
                }
                
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "memory_insight": None,
                "behavior_pattern": None,
                "key_findings": None,
                "growth_trajectory": None,
                "updated_at": None,
                "is_cached": False,
                "message": "无效的用户ID格式"
            }
        except Exception as e:
            logger.error(f"获取缓存记忆洞察时出错: {str(e)}")
            raise
    
    async def get_cached_user_summary(
        self, 
        db: Session, 
        end_user_id: str,
        model_id:str,
        language_type:str="zh"
    ) -> Dict[str, Any]:
        """
        从数据库获取缓存的用户摘要（四个部分）
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            model_id: 模型ID（用于翻译）
            language_type: 语言类型 ("zh" 中文, "en" 英文)
            
        Returns:
            {
                "user_summary": str,
                "personality": str,
                "core_values": str,
                "one_sentence": str,
                "updated_at": datetime,
                "is_cached": bool
            }
        """
        try:
            # 转换为UUID并查询用户
            user_uuid = uuid.UUID(end_user_id)
            repo = EndUserRepository(db)
            end_user = repo.get_by_id(user_uuid)
            if not end_user:
                logger.warning(f"未找到 end_user_id 为 {end_user_id} 的用户")
                return {
                    "user_summary": None,
                    "personality": None,
                    "core_values": None,
                    "one_sentence": None,
                    "updated_at": None,
                    "is_cached": False,
                    "message": "用户不存在"
                }
            
            # 检查是否有缓存数据（至少有一个字段不为空）
            user_summary=end_user.user_summary
            personality_traits=end_user.personality_traits
            core_values=end_user.core_values
            one_sentence_summary=end_user.one_sentence_summary
            
            # 直接返回数据库中的数据，不进行二次翻译
            # 语言由生成时的 X-Language-Type 决定
            
            has_cache = any([
                user_summary,
                personality_traits,
                core_values,
                one_sentence_summary
            ])
            
            if has_cache:
                logger.info(f"成功获取 end_user_id {end_user_id} 的缓存用户摘要")
                return {
                    "user_summary": user_summary,
                    "personality": personality_traits,
                    "core_values":core_values,
                    "one_sentence": one_sentence_summary,
                    "updated_at": self._datetime_to_timestamp(end_user.user_summary_updated_at),
                    "is_cached": True
                }
            else:
                logger.info(f"end_user_id {end_user_id} 的用户摘要缓存为空")
                return {
                    "user_summary": None,
                    "personality": None,
                    "core_values": None,
                    "one_sentence": None,
                    "updated_at": None,
                    "is_cached": False,
                    "message": "数据尚未生成，请稍后重试或联系管理员"
                }
                
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "user_summary": None,
                "personality": None,
                "core_values": None,
                "one_sentence": None,
                "updated_at": None,
                "is_cached": False,
                "message": "无效的用户ID格式"
            }
        except Exception as e:
            logger.error(f"获取缓存用户摘要时出错: {str(e)}")
            raise

# for user    
    async def generate_and_cache_insight(
        self, 
        db: Session, 
        end_user_id: str,
        workspace_id: Optional[uuid.UUID] = None,
        language: str = "zh"
    ) -> Dict[str, Any]:
        """
        生成并缓存记忆洞察
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            workspace_id: 工作空间ID (可选)
            language: 语言类型 ("zh" 中文, "en" 英文)，默认中文
            
        Returns:
            {
                "success": bool,
                "memory_insight": str,
                "behavior_pattern": str,
                "key_findings": List[str],  # 数组格式
                "growth_trajectory": str,
                "error": Optional[str]
            }
        """
        try:
            logger.info(f"开始为 end_user_id {end_user_id} 生成记忆洞察, language={language}")
            
            # 转换为UUID并查询用户
            user_uuid = uuid.UUID(end_user_id)
            repo = EndUserRepository(db)
            end_user = repo.get_by_id(user_uuid)
            
            if not end_user:
                logger.error(f"end_user_id {end_user_id} 不存在")
                return {
                    "success": False,
                    "memory_insight": None,
                    "behavior_pattern": None,
                    "key_findings": None,
                    "growth_trajectory": None,
                    "error": "用户不存在"
                }
            
            # 使用 end_user_id 调用分析函数
            try:
                logger.info(f"使用 end_user_id={end_user_id} 生成记忆洞察")
                result = await analytics_memory_insight_report(end_user_id, language=language)
                
                memory_insight = result.get("memory_insight", "")
                behavior_pattern = result.get("behavior_pattern", "")
                key_findings_array = result.get("key_findings", [])  # 现在是数组
                growth_trajectory = result.get("growth_trajectory", "")
                
                # 将 key_findings 数组序列化为 JSON 字符串以存储到数据库
                import json
                key_findings_json = json.dumps(key_findings_array, ensure_ascii=False) if key_findings_array else ""
                
                if not any([memory_insight, behavior_pattern, key_findings_array, growth_trajectory]):
                    logger.warning(f"end_user_id {end_user_id} 的记忆洞察生成结果为空")
                    return {
                        "success": False,
                        "memory_insight": None,
                        "behavior_pattern": None,
                        "key_findings": None,
                        "growth_trajectory": None,
                        "error": "生成的洞察报告为空,可能Neo4j中没有该用户的数据"
                    }
                
                # 更新数据库缓存（四个维度）
                # 注意：key_findings 存储为 JSON 字符串
                success = repo.update_memory_insight(
                    user_uuid, 
                    memory_insight, 
                    behavior_pattern, 
                    key_findings_json,  # 存储 JSON 字符串
                    growth_trajectory
                )
                
                if success:
                    logger.info(f"成功为 end_user_id {end_user_id} 生成并缓存记忆洞察（四维度）")
                    return {
                        "success": True,
                        "memory_insight": memory_insight,
                        "behavior_pattern": behavior_pattern,
                        "key_findings": key_findings_array,  # 返回数组
                        "growth_trajectory": growth_trajectory,
                        "error": None
                    }
                else:
                    logger.error(f"更新 end_user_id {end_user_id} 的记忆洞察缓存失败")
                    return {
                        "success": False,
                        "memory_insight": memory_insight,
                        "behavior_pattern": behavior_pattern,
                        "key_findings": key_findings_array,  # 返回数组
                        "growth_trajectory": growth_trajectory,
                        "error": "数据库更新失败"
                    }
                    
            except Exception as e:
                logger.error(f"调用分析函数生成记忆洞察时出错: {str(e)}")
                return {
                    "success": False,
                    "memory_insight": None,
                    "behavior_pattern": None,
                    "key_findings": None,
                    "growth_trajectory": None,
                    "error": f"Neo4j或LLM服务不可用: {str(e)}"
                }
                
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "success": False,
                "memory_insight": None,
                "behavior_pattern": None,
                "key_findings": None,
                "growth_trajectory": None,
                "error": "无效的用户ID格式"
            }
        except Exception as e:
            logger.error(f"生成并缓存记忆洞察时出错: {str(e)}")
            return {
                "success": False,
                "memory_insight": None,
                "behavior_pattern": None,
                "key_findings": None,
                "growth_trajectory": None,
                "error": str(e)
            }
    
    async def generate_and_cache_summary(
        self, 
        db: Session, 
        end_user_id: str,
        workspace_id: Optional[uuid.UUID] = None,
        language: str = "zh"
    ) -> Dict[str, Any]:
        """
        生成并缓存用户摘要（四个部分）
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            workspace_id: 工作空间ID (可选)
            language: 语言类型 ("zh" 中文, "en" 英文)，默认中文
            
        Returns:
            {
                "success": bool,
                "user_summary": str,
                "personality": str,
                "core_values": str,
                "one_sentence": str,
                "error": Optional[str]
            }
        """
        try:
            logger.info(f"开始为 end_user_id {end_user_id} 生成用户摘要, language={language}")
            
            # 转换为UUID并查询用户
            user_uuid = uuid.UUID(end_user_id)
            repo = EndUserRepository(db)
            end_user = repo.get_by_id(user_uuid)
            
            if not end_user:
                logger.error(f"end_user_id {end_user_id} 不存在")
                return {
                    "success": False,
                    "user_summary": None,
                    "personality": None,
                    "core_values": None,
                    "one_sentence": None,
                    "error": "用户不存在"
                }
            
            # 使用 end_user_id 调用分析函数
            try:
                logger.info(f"使用 end_user_id={end_user_id} 生成用户摘要")
                result = await analytics_user_summary(end_user_id, language=language)
                
                user_summary = result.get("user_summary", "")
                personality = result.get("personality", "")
                core_values = result.get("core_values", "")
                one_sentence = result.get("one_sentence", "")
                
                if not any([user_summary, personality, core_values, one_sentence]):
                    logger.warning(f"end_user_id {end_user_id} 的用户摘要生成结果为空")
                    return {
                        "success": False,
                        "user_summary": None,
                        "personality": None,
                        "core_values": None,
                        "one_sentence": None,
                        "error": "生成的用户摘要为空,可能Neo4j中没有该用户的数据"
                    }
                
                # 更新数据库缓存
                success = repo.update_user_summary(
                    user_uuid, 
                    user_summary, 
                    personality, 
                    core_values, 
                    one_sentence
                )
                
                if success:
                    logger.info(f"成功为 end_user_id {end_user_id} 生成并缓存用户摘要")
                    return {
                        "success": True,
                        "user_summary": user_summary,
                        "personality": personality,
                        "core_values": core_values,
                        "one_sentence": one_sentence,
                        "error": None
                    }
                else:
                    logger.error(f"更新 end_user_id {end_user_id} 的用户摘要缓存失败")
                    return {
                        "success": False,
                        "user_summary": user_summary,
                        "personality": personality,
                        "core_values": core_values,
                        "one_sentence": one_sentence,
                        "error": "数据库更新失败"
                    }
                    
            except Exception as e:
                logger.error(f"调用分析函数生成用户摘要时出错: {str(e)}")
                return {
                    "success": False,
                    "user_summary": None,
                    "personality": None,
                    "core_values": None,
                    "one_sentence": None,
                    "error": f"Neo4j或LLM服务不可用: {str(e)}"
                }
                
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "success": False,
                "user_summary": None,
                "personality": None,
                "core_values": None,
                "one_sentence": None,
                "error": "无效的用户ID格式"
            }
        except Exception as e:
            logger.error(f"生成并缓存用户摘要时出错: {str(e)}")
            return {
                "success": False,
                "user_summary": None,
                "personality": None,
                "core_values": None,
                "one_sentence": None,
                "error": str(e)
            }

# for workspace    
    async def generate_cache_for_workspace(
        self, 
        db: Session, 
        workspace_id: uuid.UUID,
        language: str = "zh"
    ) -> Dict[str, Any]:
        """
        为整个工作空间生成缓存
        
        Args:
            db: 数据库会话
            workspace_id: 工作空间ID
            language: 语言类型 ("zh" 中文, "en" 英文)，默认中文
            
        Returns:
            {
                "total_users": int,
                "successful": int,
                "failed": int,
                "errors": List[Dict]
            }
        """
        logger.info(f"开始为工作空间 {workspace_id} 批量生成缓存, language={language}")
        
        total_users = 0
        successful = 0
        failed = 0
        errors = []
        
        try:
            # 获取工作空间的所有终端用户
            repo = EndUserRepository(db)
            end_users = repo.get_all_by_workspace(workspace_id)
            total_users = len(end_users)
            
            logger.info(f"工作空间 {workspace_id} 共有 {total_users} 个终端用户")
            
            # 遍历每个用户并生成缓存
            for end_user in end_users:
                end_user_id = str(end_user.id)
                
                try:
                    # 生成记忆洞察
                    insight_result = await self.generate_and_cache_insight(db, end_user_id, language=language)
                    
                    # 生成用户摘要
                    summary_result = await self.generate_and_cache_summary(db, end_user_id, language=language)
                    
                    # 检查是否都成功
                    if insight_result["success"] and summary_result["success"]:
                        successful += 1
                        logger.info(f"成功为终端用户 {end_user_id} 生成缓存")
                    else:
                        failed += 1
                        error_info = {
                            "end_user_id": end_user_id,
                            "insight_error": insight_result.get("error"),
                            "summary_error": summary_result.get("error")
                        }
                        errors.append(error_info)
                        logger.warning(f"终端用户 {end_user_id} 的缓存生成部分失败: {error_info}")
                        
                except Exception as e:
                    # 单个用户失败不影响其他用户
                    failed += 1
                    error_info = {
                        "end_user_id": end_user_id,
                        "error": str(e)
                    }
                    errors.append(error_info)
                    logger.error(f"为终端用户 {end_user_id} 生成缓存时出错: {str(e)}")
            
            # 记录统计信息
            logger.info(
                f"工作空间 {workspace_id} 批量生成完成: "
                f"总数={total_users}, 成功={successful}, 失败={failed}"
            )
            
            return {
                "total_users": total_users,
                "successful": successful,
                "failed": failed,
                "errors": errors
            }
            
        except Exception as e:
            logger.error(f"为工作空间 {workspace_id} 批量生成缓存时出错: {str(e)}")
            return {
                "total_users": total_users,
                "successful": successful,
                "failed": failed,
                "errors": errors + [{"error": f"批量处理失败: {str(e)}"}]
            }


# 独立的分析函数

async def analytics_memory_insight_report(end_user_id: Optional[str] = None, language: str = "zh") -> Dict[str, Any]:
    """
    生成记忆洞察报告（四个维度）
    
    这个函数包含完整的业务逻辑：
    1. 使用 MemoryInsightHelper 工具类获取基础数据（领域分布、活跃时段、社交关联）
    2. 使用 Jinja2 模板渲染提示词
    3. 调用 LLM 生成四个维度的自然语言报告
    4. 解析并返回四个部分
    
    Args:
        end_user_id: 可选的终端用户ID
        language: 语言类型 ("zh" 中文, "en" 英文)，默认中文
        
    Returns:
        包含四个维度报告的字典: {
            "memory_insight": str,           # 总体概述
            "behavior_pattern": str,         # 行为模式
            "key_findings": List[str],       # 关键发现（数组）
            "growth_trajectory": str         # 成长轨迹
        }
    """
    import re

    from app.core.language_utils import validate_language
    from app.core.memory.utils.prompt.prompt_utils import render_memory_insight_prompt
    
    # 验证语言参数
    language = validate_language(language)
    
    insight = MemoryInsightHelper(end_user_id)     
    
    try:
        # 1. 并行获取三个维度的数据
        import asyncio
        domain_dist, active_periods, social_conn = await asyncio.gather(
            insight.get_domain_distribution(),
            insight.get_active_periods(),
            insight.get_social_connections(),
        )
        
        # 2. 构建数据字符串
        domain_distribution_str = None
        if domain_dist:
            top_domains = ", ".join([f"{k}({v:.0%})" for k, v in list(domain_dist.items())[:3]])
            domain_distribution_str = f"用户的记忆主要集中在 {top_domains}"
        
        active_periods_str = None
        if active_periods:
            months_str = " 和 ".join(map(str, active_periods))
            active_periods_str = f"用户在每年的 {months_str} 月最为活跃"
        
        social_connections_str = None
        if social_conn:
            social_connections_str = f"与用户\"{social_conn['user_id']}\"拥有最多共同记忆({social_conn['common_memories_count']}条)，时间范围主要在 {social_conn['time_range']}"
        
        # 3. 如果没有足够数据，返回默认消息
        if not any([domain_distribution_str, active_periods_str, social_connections_str]):
            return {
                "memory_insight": "暂无足够数据生成洞察报告。",
                "behavior_pattern": "",
                "key_findings": "",
                "growth_trajectory": ""
            }
        
        # 4. 使用 Jinja2 模板渲染提示词
        user_prompt = await render_memory_insight_prompt(
            domain_distribution=domain_distribution_str,
            active_periods=active_periods_str,
            social_connections=social_connections_str,
            language=language
        )
        
        messages = [
            {"role": "user", "content": user_prompt}
        ]
        
        # 5. 调用 LLM 生成报告
        response = await insight.llm_client.chat(messages=messages)
        
        # 6. 处理 LLM 响应，确保返回字符串类型
        content = response.content
        if isinstance(content, list):
            if len(content) > 0:
                if isinstance(content[0], dict):
                    text = content[0].get('text', content[0].get('content', str(content[0])))
                    full_response = str(text)
                else:
                    full_response = str(content[0])
            else:
                full_response = ""
        elif isinstance(content, dict):
            full_response = str(content.get('text', content.get('content', str(content))))
        else:
            full_response = str(content) if content is not None else ""
        
        # 7. 解析四个部分
        # 使用正则表达式提取四个部分（支持中英文双语标题）
        memory_insight_match = re.search(r'【(?:总体概述|Overview)】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        behavior_match = re.search(r'【(?:行为模式|Behavior Pattern)】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        findings_match = re.search(r'【(?:关键发现|Key Findings)】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        trajectory_match = re.search(r'【(?:成长轨迹|Growth Trajectory)】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        
        memory_insight = memory_insight_match.group(1).strip() if memory_insight_match else ""
        behavior_pattern = behavior_match.group(1).strip() if behavior_match else ""
        key_findings_text = findings_match.group(1).strip() if findings_match else ""
        growth_trajectory = trajectory_match.group(1).strip() if trajectory_match else ""
        
        # 将 key_findings 从文本转换为数组
        # 按 • 符号分割，并清理每个条目
        key_findings_array = []
        if key_findings_text:
            # 分割并清理每个条目
            items = [item.strip() for item in key_findings_text.split('•') if item.strip()]
            key_findings_array = items
        
        return {
            "memory_insight": memory_insight,
            "behavior_pattern": behavior_pattern,
            "key_findings": key_findings_array,  # 返回数组而不是字符串
            "growth_trajectory": growth_trajectory
        }
        
    finally:
        # 确保关闭连接
        await insight.close()


async def analytics_user_summary(end_user_id: Optional[str] = None, language: str = "zh") -> Dict[str, Any]:
    """
    生成用户摘要（包含四个部分）
    
    这个函数包含完整的业务逻辑：
    1. 使用 UserSummaryHelper 工具类获取基础数据（实体、语句）
    2. 使用 prompt_utils 渲染提示词
    3. 调用 LLM 生成四部分内容：基本介绍、性格特点、核心价值观、一句话总结
    
    Args:
        end_user_id: 可选的终端用户ID
        language: 语言类型 ("zh" 中文, "en" 英文)，默认中文
        
    Returns:
        包含四部分摘要的字典: {
            "user_summary": str,
            "personality": str,
            "core_values": str,
            "one_sentence": str
        }
    """
    import re

    from app.core.language_utils import validate_language
    from app.core.memory.utils.prompt.prompt_utils import render_user_summary_prompt
    from app.repositories.end_user_repository import EndUserRepository
    
    # 验证语言参数
    language = validate_language(language)
    
    # 获取用户的 other_name 字段
    user_display_name = "该用户" if language == "zh" else "the user"
    if end_user_id:
        try:
            # 获取数据库会话并查询用户信息
            with get_db_context() as db:
                repo = EndUserRepository(db)
                end_user = repo.get_by_id(uuid.UUID(end_user_id))
                if end_user and end_user.other_name:
                    user_display_name = end_user.other_name
                    logger.info(f"使用 other_name 作为用户显示名称: {user_display_name}")
                else:
                    logger.info(f"用户 {end_user_id} 的 other_name 为空，使用默认称呼: {user_display_name}")

        except Exception as e:
            logger.warning(f"获取用户 other_name 失败，使用默认称呼: {str(e)}")
    
    # 创建 UserSummaryHelper 实例
    user_summary_tool = UserSummaryHelper(end_user_id or os.getenv("SELECTED_end_user_id", "group_123"))
    
    try:
        # 1) 收集上下文数据
        entities = await user_summary_tool.get_top_entities(limit=40)
        statements = await user_summary_tool.get_recent_statements(limit=100)

        entity_lines = [f"{name} ({freq})" for name, freq in entities][:20]
        statement_samples = [s["statement"].strip() for s in statements if s.get("statement", "").strip()][:20]

        # 2) 使用 prompt_utils 渲染提示词
        user_prompt = await render_user_summary_prompt(
            user_id=user_summary_tool.user_id,
            entities=", ".join(entity_lines) if entity_lines else "(空)" if language == "zh" else "(empty)",
            statements=" | ".join(statement_samples) if statement_samples else "(空)" if language == "zh" else "(empty)",
            language=language,
            user_display_name=user_display_name
        )

        messages = [
            {"role": "user", "content": user_prompt},
        ]

        # 3) 调用 LLM 生成摘要
        response = await user_summary_tool.llm.chat(messages=messages)
        
        # 4) 处理 LLM 响应，确保返回字符串类型
        content = response.content
        if isinstance(content, list):
            if len(content) > 0:
                if isinstance(content[0], dict):
                    text = content[0].get('text', content[0].get('content', str(content[0])))
                    full_response = str(text)
                else:
                    full_response = str(content[0])
            else:
                full_response = ""
        elif isinstance(content, dict):
            full_response = str(content.get('text', content.get('content', str(content))))
        else:
            full_response = str(content) if content is not None else ""
        
        # 5) 解析四个部分
        # 使用正则表达式提取四个部分（支持中英文标题）
        user_summary_match = re.search(r'【(?:基本介绍|Basic Introduction)】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        personality_match = re.search(r'【(?:性格特点|Personality Traits)】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        core_values_match = re.search(r'【(?:核心价值观|Core Values)】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        one_sentence_match = re.search(r'【(?:一句话总结|One-Sentence Summary)】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        
        user_summary = user_summary_match.group(1).strip() if user_summary_match else ""
        personality = personality_match.group(1).strip() if personality_match else ""
        core_values = core_values_match.group(1).strip() if core_values_match else ""
        one_sentence = one_sentence_match.group(1).strip() if one_sentence_match else ""
        
        # 6) 清理可能包含的反思内容（防御性编程）
        # 如果 LLM 仍然输出了反思内容，在这里过滤掉
        def clean_reflection_content(text: str) -> str:
            """移除可能包含的反思内容"""
            if not text:
                return text
            # 移除 "---" 之后的所有内容（通常是反思部分的开始）
            if '---' in text:
                text = text.split('---')[0].strip()
            # 移除 "**Step" 开头的内容
            if '**Step' in text:
                text = text.split('**Step')[0].strip()
            # 移除 "Self-Review" 相关内容
            if 'Self-Review' in text or 'self-review' in text:
                text = re.sub(r'[\-\*]*\s*Self-Review.*$', '', text, flags=re.IGNORECASE | re.DOTALL).strip()
            return text
        
        user_summary = clean_reflection_content(user_summary)
        personality = clean_reflection_content(personality)
        core_values = clean_reflection_content(core_values)
        one_sentence = clean_reflection_content(one_sentence)
        
        return {
            "user_summary": user_summary,
            "personality": personality,
            "core_values": core_values,
            "one_sentence": one_sentence
        }
        
    finally:
        # 确保关闭连接
        await user_summary_tool.close()


async def analytics_node_statistics(
    db: Session,
    end_user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    统计 Neo4j 中四种节点类型的数量和百分比
    
    Args:
        db: 数据库会话
        end_user_id: 可选的终端用户ID (UUID)，用于过滤特定用户的节点
        
    Returns:
        {
            "total": int,  # 总节点数
            "nodes": [
                {
                    "type": str,  # 节点类型
                    "count": int,  # 节点数量
                    "percentage": float  # 百分比
                }
            ]
        }
    """
    # 定义四种节点类型的查询
    node_types = ["Chunk", "MemorySummary", "Statement", "ExtractedEntity"]
    
    # 存储每种节点类型的计数
    node_counts = {}
    
    # 查询每种节点类型的数量
    for node_type in node_types:
        # 构建查询语句
        if end_user_id:
            query = f"""
            MATCH (n:{node_type})
            WHERE n.end_user_id = $end_user_id
            RETURN count(n) as count
            """
            result = await _neo4j_connector.execute_query(query, end_user_id=end_user_id)
        else:
            query = f"""
            MATCH (n:{node_type})
            RETURN count(n) as count
            """
            result = await _neo4j_connector.execute_query(query)
        
        # 提取计数结果
        count = result[0]["count"] if result and len(result) > 0 else 0
        node_counts[node_type] = count
    
    # 计算总数
    total = sum(node_counts.values())
    
    # 构建返回数据，包含百分比
    nodes = []
    for node_type in node_types:
        count = node_counts[node_type]
        percentage = round((count / total * 100), 2) if total > 0 else 0.0
        nodes.append({
            "type": node_type,
            "count": count,
            "percentage": percentage
        })
    
    data = {
        "total": total,
        "nodes": nodes
    }
    
    return data


async def analytics_memory_types(
    db: Session,
    end_user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    统计8种记忆类型的数量和百分比
    
    计算规则：
    1. 感知记忆 (PERCEPTUAL_MEMORY) = 通过 MemoryPerceptualService.get_memory_count 获取的 total_count
    2. 工作记忆 (WORKING_MEMORY) = 会话数量（通过 ConversationRepository.get_conversation_by_user_id 获取）
    3. 短期记忆 (SHORT_TERM_MEMORY) = /short_term 接口返回的问答对数量
    4. 显性记忆 (EXPLICIT_MEMORY) = 情景记忆 + 语义记忆（通过 MemoryBaseService.get_explicit_memory_count 获取）
    5. 隐性记忆 (IMPLICIT_MEMORY) = MemorySummary 节点数量（需 >= MIN_MEMORY_SUMMARY_COUNT 才显示，否则为 0）
    6. 情绪记忆 (EMOTIONAL_MEMORY) = 情绪标签统计总数（通过 MemoryBaseService.get_emotional_memory_count 获取）
    7. 情景记忆 (EPISODIC_MEMORY) = memory_summary（通过 MemoryBaseService.get_episodic_memory_count 获取）
    8. 遗忘记忆 (FORGET_MEMORY) = 激活值低于阈值的节点数（通过 MemoryBaseService.get_forget_memory_count 获取）
    
    Args:
        db: 数据库会话
        end_user_id: 可选的终端用户ID (UUID)，用于过滤特定用户的节点
        
    Returns:
        [
            {
                "type": str,  # 记忆类型枚举值 (如 PERCEPTUAL_MEMORY, WORKING_MEMORY 等)
                "count": int,  # 该类型的数量
                "percentage": float  # 该类型在所有记忆中的占比
            },
            ...
        ]
        
    记忆类型枚举值：
        - PERCEPTUAL_MEMORY: 感知记忆
        - WORKING_MEMORY: 工作记忆
        - SHORT_TERM_MEMORY: 短期记忆
        - EXPLICIT_MEMORY: 显性记忆
        - IMPLICIT_MEMORY: 隐性记忆
        - EMOTIONAL_MEMORY: 情绪记忆
        - EPISODIC_MEMORY: 情景记忆
        - FORGET_MEMORY: 遗忘记忆
    """
    # 初始化基础服务
    base_service = MemoryBaseService()
    
    # 初始化感知记忆服务
    perceptual_service = MemoryPerceptualService(db)
    
    # 获取感知记忆数量
    if end_user_id:
        perceptual_stats = perceptual_service.get_memory_count(uuid.UUID(end_user_id))
        perceptual_count = perceptual_stats.get("total", 0)
    else:
        perceptual_count = 0
    
    # 获取工作记忆数量（基于会话数量）
    work_count = 0
    if end_user_id:
        try:
            conversation_repo = ConversationRepository(db)
            conversations, total = conversation_repo.get_conversation_by_user_id(
                user_id=uuid.UUID(end_user_id),
                is_activate=True
            )
            work_count = total
            logger.debug(f"工作记忆数量（会话数）: {work_count} (end_user_id={end_user_id})")
        except Exception as e:
            logger.warning(f"获取会话数量失败，工作记忆数量设为0: {str(e)}")
            work_count = 0
    
    # 获取隐性记忆数量（基于有关联关系的 MemorySummary 节点数量，需 >= MIN_MEMORY_SUMMARY_COUNT 才计入）
    implicit_count = 0
    if end_user_id:
        try:
            memory_summary_count = await base_service.get_valid_memory_summary_count(end_user_id)
            implicit_count = memory_summary_count if memory_summary_count >= MIN_MEMORY_SUMMARY_COUNT else 0
            logger.debug(f"隐性记忆数量（有效MemorySummary节点数）: {implicit_count} (有效MemorySummary总数={memory_summary_count}, end_user_id={end_user_id})")
        except Exception as e:
            logger.warning(f"获取MemorySummary数量失败，隐性记忆数量设为0: {str(e)}")
            implicit_count = 0
    
    # 原有的基于行为习惯的统计方式（已注释）
    # implicit_count = 0
    # if end_user_id:
    #     try:
    #         implicit_service = ImplicitMemoryService(db, end_user_id)
    #         behavior_habits = await implicit_service.get_behavior_habits(
    #             user_id=end_user_id
    #         )
    #         implicit_count = len(behavior_habits)
    #         logger.debug(f"隐性记忆数量（行为习惯数）: {implicit_count} (end_user_id={end_user_id})")
    #     except Exception as e:
    #         logger.warning(f"获取行为习惯数量失败，隐性记忆数量设为0: {str(e)}")
    #         implicit_count = 0
    
    # 获取短期记忆数量（基于 /short_term 接口返回的问答对数量）
    short_term_count = 0
    if end_user_id:
        try:
            short_term_service = ShortService(end_user_id, db)
            short_term_data = short_term_service.get_short_databasets()
            # 统计 short_term 数组的长度
            if short_term_data:
                short_term_count = len(short_term_data)
            logger.debug(f"短期记忆数量（问答对数）: {short_term_count} (end_user_id={end_user_id})")
        except Exception as e:
            logger.warning(f"获取短期记忆数量失败，短期记忆数量设为0: {str(e)}")
            short_term_count = 0
    
    # 获取用户的遗忘阈值配置
    forgetting_threshold = 0.3  # 默认值
    if end_user_id:
        try:
            from app.core.memory.storage_services.forgetting_engine.config_utils import (
                load_actr_config_from_db,
            )
            from app.services.memory_agent_service import get_end_user_connected_config
            
            # 获取用户关联的 config_id
            connected_config = get_end_user_connected_config(end_user_id, db)
            config_id = connected_config.get('memory_config_id')
            
            if config_id:
                # 从数据库加载配置
                config = load_actr_config_from_db(db, config_id)
                forgetting_threshold = config.get('forgetting_threshold', 0.3)
                logger.debug(f"使用用户配置的遗忘阈值: {forgetting_threshold} (end_user_id={end_user_id}, config_id={config_id})")
            else:
                logger.debug(f"用户未关联配置，使用默认遗忘阈值: {forgetting_threshold} (end_user_id={end_user_id})")
        except Exception as e:
            logger.warning(f"获取用户遗忘阈值配置失败，使用默认值 {forgetting_threshold}: {str(e)}")
    
    # 使用 MemoryBaseService 的共享方法获取特殊记忆类型的数量
    episodic_count = await base_service.get_episodic_memory_count(end_user_id)
    explicit_count = await base_service.get_explicit_memory_count(end_user_id)
    emotion_count = await base_service.get_emotional_memory_count(end_user_id, perceptual_count)
    forget_count = await base_service.get_forget_memory_count(end_user_id, forgetting_threshold)
    
    # 按规则计算8种记忆类型的数量（使用英文枚举作为key）
    memory_counts = {
        "PERCEPTUAL_MEMORY": perceptual_count,                    # 感知记忆
        "WORKING_MEMORY": work_count,                             # 工作记忆（基于会话数量）
        "SHORT_TERM_MEMORY": short_term_count,                    # 短期记忆（基于问答对数量）
        "EXPLICIT_MEMORY": explicit_count,                        # 显性记忆（情景记忆 + 语义记忆）
        "IMPLICIT_MEMORY": implicit_count,                        # 隐性记忆（MemorySummary节点数，需>=MIN_MEMORY_SUMMARY_COUNT）
        "EMOTIONAL_MEMORY": emotion_count,                        # 情绪记忆（使用情绪标签统计）
        "EPISODIC_MEMORY": episodic_count,                        # 情景记忆
        "FORGET_MEMORY": forget_count                             # 遗忘记忆（激活值低于阈值）
    }
    
    # 计算总数
    total = sum(memory_counts.values())
    
    # 构建返回数据，包含 type、count 和 percentage
    memory_types = []
    for memory_type, count in memory_counts.items():
        percentage = round((count / total * 100), 2) if total > 0 else 0.0
        memory_types.append({
            "type": memory_type,
            "count": count,
            "percentage": percentage
        })
    
    return memory_types

async def analytics_graph_data(
    db: Session,
    end_user_id: str,
    node_types: Optional[List[str]] = None,
    limit: int = 100,
    depth: int = 1,
    center_node_id: Optional[str] = None,
    per_type_limits: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """获取 Neo4j 图数据，用于前端可视化（按 Per_Type_Limit 精细化控制）。

    本函数严格遵循 design ``Algorithm 4``：

    1. 校验 ``end_user_id`` 合法性与用户存在性，失败返回结构化空响应；
    2. 按 ``center_node_id`` / ``node_types`` 分派 Center_Mode / Filter_Mode /
       Default_Mode 三条路径，统一使用 ``Per_Type_Limit`` 控制每种类型的返回数量；
    3. 调用 ``_resolve_per_type_limits`` + ``_apply_total_cap_shrink`` 得到最终
       生效的 ``{Node_Type: Per_Type_Limit}`` 映射；
    4. 顺序执行 Q1（节点）→ Q2（批量关联计数）→ Q3（边，try/except 降级）→
       Q4（按 label 全量计数）四次 Cypher，整体调用次数与节点数 N 无关；
    5. 通过 :class:`GraphDataResponse` 装配响应，``statistics.per_type`` 包含
       本次配置内全部 Node_Type 的 ``returned/total/limit/truncated`` 元数据。

    Args:
        db: SQLAlchemy 会话，用于校验 end_user 是否存在。
        end_user_id: 终端用户 UUID 字符串。
        node_types: 可选的 Node_Type 过滤列表。非空 → Filter_Mode；空 →
            Default_Mode（覆盖全部 :data:`SUPPORTED_NODE_TYPES`）。
        limit: 兜底 Per_Type_Limit；同时也作为 Center_Mode 下的单一节点上限。
        depth: 仅在 Center_Mode 下生效，控制邻居跳数。
        center_node_id: 中心节点 elementId；非空时进入 Center_Mode 并忽略
            ``node_types`` / ``per_type_limits`` 中的类型过滤语义。
        per_type_limits: 由 controller 解析得到的 ``{Node_Type: Per_Type_Limit}``
            映射；本函数不再做格式校验。

    Returns:
        ``GraphDataResponse.model_dump()`` 后的字典，顶层键为
        ``nodes / edges / statistics``，必要时附带 ``message``。
    """
    try:
        # 1. 用户校验
        try:
            user_uuid = uuid.UUID(end_user_id)
        except (ValueError, TypeError):
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return _empty_graph_response("无效的用户ID格式")

        end_user = EndUserRepository(db).get_by_id(user_uuid)
        if not end_user:
            logger.warning(f"未找到 end_user_id 为 {end_user_id} 的用户")
            return _empty_graph_response("用户不存在")

        # 2. 模式分派 + Q1 节点查询
        mode, type_limits, node_rows = await _collect_node_query(
            end_user_id=end_user_id,
            node_types=node_types,
            limit=limit,
            depth=depth,
            center_node_id=center_node_id,
            per_type_limits=per_type_limits,
        )

        # 3+4+5. Q2 批量关联计数 → 节点装配
        nodes, node_type_counts, node_ids = await _format_nodes(node_rows)

        # 6. Q3 边查询（try/except 降级，Requirement 8.4）
        edges, edge_type_counts = await _format_edges(node_ids)

        # 7. Q4 全量计数 + statistics.per_type 装配
        per_type_stat = await _build_per_type_stat(
            mode=mode,
            end_user_id=end_user_id,
            type_limits=type_limits,
            node_type_counts=node_type_counts,
        )

        statistics = {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "node_types": node_type_counts,
            "edge_types": edge_type_counts,
            "per_type": per_type_stat,
        }

        # 8. 日志输出 returned/total/limit 三元组（Requirement 8.5）
        per_type_log = ", ".join(
            f"{t}:returned={v['returned']}/total={v['total']}/limit={v['limit']}"
            for t, v in per_type_stat.items()
        )
        logger.info(
            f"图数据查询: end_user_id={end_user_id} mode={mode} "
            f"nodes={len(nodes)} edges={len(edges)} per_type=[{per_type_log}]"
        )

        return {
            "nodes": nodes,
            "edges": edges,
            "statistics": statistics,
        }

    except Exception as e:
        logger.error(f"获取图数据失败: {str(e)}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# analytics_graph_data 的内部装配 helper
# ---------------------------------------------------------------------------


async def _collect_node_query(
    *,
    end_user_id: str,
    node_types: Optional[List[str]],
    limit: int,
    depth: int,
    center_node_id: Optional[str],
    per_type_limits: Optional[Dict[str, int]],
) -> Tuple[str, Dict[str, int], List[Dict[str, Any]]]:
    """模式分派 + Per_Type_Limit 解析 + Q1 节点查询。

    Returns:
        ``(mode, type_limits, node_rows)`` —— ``mode`` ∈ ``{"Center","Filter","Default"}``；
        ``type_limits`` 为本次实际生效的 ``{Node_Type: Per_Type_Limit}`` 映射，Center_Mode
        下为空 dict；``node_rows`` 为 Q1 返回的节点行列表（按字典顺序合并）。
    """
    if center_node_id:
        node_rows = await _query_center_node_neighbors(
            end_user_id=end_user_id,
            center_node_id=center_node_id,
            depth=depth,
            limit=limit,
        )
        return "Center", {}, node_rows

    if node_types:
        target_types = [t for t in node_types if t in SUPPORTED_NODE_TYPES]
        mode = "Filter"
    else:
        target_types = sorted(SUPPORTED_NODE_TYPES)
        mode = "Default"

    type_limits = _resolve_per_type_limits(
        target_types=target_types,
        user_overrides=dict(per_type_limits or {}),
        fallback_default=limit,
    )
    type_limits = _apply_total_cap_shrink(type_limits)

    non_zero_limits = {t: v for t, v in type_limits.items() if v > 0}
    if not non_zero_limits:
        return mode, type_limits, []

    node_rows = await _query_nodes_by_type_limits(
        _neo4j_connector,
        end_user_id=end_user_id,
        type_limits=non_zero_limits,
    )
    return mode, type_limits, node_rows


async def _format_nodes(
    node_rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[str]]:
    """Q2 批量关联计数 → 装配节点列表。

    Returns:
        ``(nodes, node_type_counts, node_ids)`` —— ``nodes`` 是装配后的响应列表；
        ``node_type_counts`` 为 ``{label: count}``；``node_ids`` 为本批节点的
        elementId 列表（顺序与 ``nodes`` 一致），供后续边查询/双端校验使用。
    """
    node_records: List[Tuple[str, str, Dict[str, Any]]] = []
    node_ids: List[str] = []
    for record in node_rows:
        node_id = record.get("id")
        if not node_id:
            continue
        labels_value = record.get("labels") or []
        node_label = labels_value[0] if labels_value else "Unknown"
        node_props = record.get("properties") or {}
        node_records.append((node_id, node_label, node_props))
        node_ids.append(node_id)

    rel_count_map = await _query_rel_count_batch(_neo4j_connector, node_ids)

    nodes: List[Dict[str, Any]] = []
    node_type_counts: Dict[str, int] = {}
    for node_id, node_label, node_props in node_records:
        rel_count = int(rel_count_map.get(node_id, 0))
        filtered_props = _extract_node_properties(
            node_label, node_props, rel_count=rel_count
        )
        nodes.append({
            "id": node_id,
            "label": node_label,
            "properties": filtered_props,
            "caption": filtered_props.get("caption", node_label),
        })
        node_type_counts[node_label] = node_type_counts.get(node_label, 0) + 1

    return nodes, node_type_counts, node_ids


async def _format_edges(
    node_ids: List[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Q3 边查询 + 装配边列表（Requirement 8.4：异常时降级为空边）。

    Returns:
        ``(edges, edge_type_counts)`` —— ``edges`` 为装配后的响应边列表，仅保留两端都
        在 ``node_ids`` 中的边；``edge_type_counts`` 为 ``{rel_type: count}``。
        当 ``node_ids`` 为空时直接返回 ``([], {})``，不发起查询。
    """
    if not node_ids:
        return [], {}

    try:
        edge_rows = await _query_edges_among_nodes(node_ids)
    except Exception as e:
        logger.error(f"边查询失败，降级为空边: {e}", exc_info=True)
        edge_rows = []

    node_id_set = set(node_ids)
    edges: List[Dict[str, Any]] = []
    edge_type_counts: Dict[str, int] = {}
    for record in edge_rows:
        source = record.get("source")
        target = record.get("target")
        # 防御性双端过滤：Cypher 已过滤悬空边，这里保险起见再校验一次
        if source not in node_id_set or target not in node_id_set:
            continue
        rel_type = record.get("rel_type")
        cleaned_edge_props = {
            key: _clean_neo4j_value(value)
            for key, value in (record.get("properties") or {}).items()
        }
        edges.append({
            "id": record.get("id"),
            "source": source,
            "target": target,
            "type": rel_type,
            "properties": cleaned_edge_props,
            "caption": _resolve_edge_caption(rel_type, cleaned_edge_props),
        })
        edge_type_counts[rel_type] = edge_type_counts.get(rel_type, 0) + 1

    return edges, edge_type_counts


async def _build_per_type_stat(
    *,
    mode: str,
    end_user_id: str,
    type_limits: Dict[str, int],
    node_type_counts: Dict[str, int],
) -> Dict[str, Dict[str, Any]]:
    """Q4 全量计数 + 装配 ``statistics.per_type`` 截断元数据。

    Default_Mode / Center_Mode 用全部 :data:`SUPPORTED_NODE_TYPES` 以呈现「全量 vs
    当前」；Filter_Mode 收敛到 ``type_limits`` 的键集合。
    """
    if mode == "Filter":
        stat_types = sorted(type_limits.keys())
    else:
        stat_types = sorted(SUPPORTED_NODE_TYPES)

    total_by_type = await _query_total_count_by_type(
        _neo4j_connector,
        end_user_id=end_user_id,
        supported_types=stat_types,
    )

    per_type_stat: Dict[str, Dict[str, Any]] = {}
    for node_type in stat_types:
        returned = node_type_counts.get(node_type, 0)
        total = int(total_by_type.get(node_type, 0))
        type_limit = int(type_limits.get(node_type, 0))
        per_type_stat[node_type] = {
            "returned": returned,
            "total": total,
            "limit": type_limit,
            "truncated": total > returned,
        }
    return per_type_stat


def _empty_graph_response(message: str) -> Dict[str, Any]:
    """构造空响应结构（用户不存在 / UUID 非法等场景）。"""
    return {
        "nodes": [],
        "edges": [],
        "statistics": {
            "total_nodes": 0,
            "total_edges": 0,
            "node_types": {},
            "edge_types": {},
            "per_type": {},
        },
        "message": message,
    }


async def _query_center_node_neighbors(
    *,
    end_user_id: str,
    center_node_id: str,
    depth: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """以中心节点为起点的 1..depth 跳邻居查询（Center_Mode）。

    Cypher 不允许直接参数化变长路径长度（``[*1..n]``），因此需要将 ``depth``
    安全拼入字符串；调用方需保证 ``depth`` 已被钳制为 ≤ 3。
    """
    safe_depth = max(1, min(int(depth), DEPTH_HARD_MAX))
    cypher = f"""
    MATCH path = (center)-[*1..{safe_depth}]-(connected)
    WHERE center.end_user_id = $end_user_id
      AND elementId(center) = $center_node_id
    WITH collect(DISTINCT center) + collect(DISTINCT connected) as all_nodes
    UNWIND all_nodes as n
    RETURN DISTINCT
        elementId(n) as id,
        labels(n) as labels,
        properties(n) as properties
    LIMIT $limit
    """
    rows = await _neo4j_connector.execute_query(
        cypher,
        end_user_id=end_user_id,
        center_node_id=center_node_id,
        limit=int(limit),
    )
    return list(rows)


async def _query_edges_among_nodes(node_ids: List[str]) -> List[Dict[str, Any]]:
    """查询若干节点之间的有向关系（Q3）。Cypher 已过滤悬空边。"""
    cypher = """
    MATCH (n)-[r]->(m)
    WHERE elementId(n) IN $node_ids
      AND elementId(m) IN $node_ids
    RETURN
        elementId(r) as id,
        elementId(n) as source,
        elementId(m) as target,
        type(r) as rel_type,
        properties(r) as properties
    """
    rows = await _neo4j_connector.execute_query(cypher, node_ids=list(node_ids))
    return list(rows)


# 辅助函数

async def analytics_community_graph_data(
    db: Session,
    end_user_id: str,
) -> Dict[str, Any]:
    """
    获取社区图谱数据，包含 Community 节点、ExtractedEntity 节点及其关系。

    Returns:
        包含 nodes、edges、statistics 的字典，格式与 analytics_graph_data 一致
    """
    try:
        user_uuid = uuid.UUID(end_user_id)
        repo = EndUserRepository(db)
        end_user = repo.get_by_id(user_uuid)
        if not end_user:
            return {
                "nodes": [], "edges": [],
                "statistics": {"total_nodes": 0, "total_edges": 0, "node_types": {}, "edge_types": {}},
                "message": "用户不存在"
            }

        # 查询社区节点、实体节点、BELONGS_TO_COMMUNITY 边、实体间关系
        from app.repositories.neo4j.cypher_queries import GET_COMMUNITY_GRAPH_DATA
        rows = await _neo4j_connector.execute_query(GET_COMMUNITY_GRAPH_DATA, end_user_id=end_user_id)

        nodes_map: Dict[str, dict] = {}
        edges_map: Dict[str, dict] = {}
        # 记录每个 Community 对应的实体 id 列表
        community_members: Dict[str, list] = {}

        for row in rows:
            # Community 节点
            c_id = row["c_id"]
            if c_id and c_id not in nodes_map:
                raw = row["c_props"] or {}
                props = {k: _clean_neo4j_value(raw.get(k)) for k in (
                    "community_id", "end_user_id", "member_count", "updated_at",
                    "name", "summary", "core_entities",
                ) if k in raw}
                nodes_map[c_id] = {
                    "id": c_id,
                    "label": "Community",
                    "properties": props,
                }

            # ExtractedEntity 节点 (e)
            e_id = row["e_id"]
            if e_id and e_id not in nodes_map:
                raw = row["e_props"] or {}
                props = {k: _clean_neo4j_value(raw.get(k)) for k in (
                    "name", "end_user_id", "description", "created_at", "entity_type",
                ) if k in raw}
                # 注入所属社区名称（c 是 e 直接归属的社区）
                c_raw = row["c_props"] or {}
                props["community_name"] = _clean_neo4j_value(c_raw.get("name")) or ""
                nodes_map[e_id] = {
                    "id": e_id,
                    "label": "ExtractedEntity",
                    "properties": props,
                }

            # ExtractedEntity 节点 (e2，可选)
            e2_id = row.get("e2_id")
            if e2_id and e2_id not in nodes_map:
                raw = row["e2_props"] or {}
                props = {k: _clean_neo4j_value(raw.get(k)) for k in (
                    "name", "end_user_id", "description", "created_at", "entity_type",
                ) if k in raw}
                # e2 的社区归属在后处理阶段通过 community_members 补充
                props["community_name"] = ""
                nodes_map[e2_id] = {
                    "id": e2_id,
                    "label": "ExtractedEntity",
                    "properties": props,
                }

            # BELONGS_TO_COMMUNITY 边
            b_id = row["b_id"]
            if b_id and b_id not in edges_map:
                edges_map[b_id] = {
                    "id": b_id,
                    "source": e_id,
                    "target": c_id,
                }
            # 收集社区成员 id
            if c_id and e_id:
                community_members.setdefault(c_id, [])
                if e_id not in community_members[c_id]:
                    community_members[c_id].append(e_id)

            # EXTRACTED_RELATIONSHIP 边（可选）
            r_id = row.get("r_id")
            if r_id and r_id not in edges_map and e2_id:
                r_props = {k: _clean_neo4j_value(v) for k, v in (row["r_props"] or {}).items()}
                source = e_id if row.get("r_from_e") else e2_id
                target = e2_id if row.get("r_from_e") else e_id
                edges_map[r_id] = {
                    "id": r_id,
                    "source": source,
                    "target": target,
                }

        nodes = list(nodes_map.values())
        edges = list(edges_map.values())

        # 为每个 Community 节点注入 member_entity_ids，同时补全 e2 节点的 community_name
        for c_id, member_ids in community_members.items():
            c_node = nodes_map.get(c_id)
            if c_node:
                c_node["properties"]["member_entity_ids"] = member_ids
                c_name = c_node["properties"].get("name") or ""
                # 补全属于该社区但 community_name 为空的实体（即 e2 节点）
                for eid in member_ids:
                    e_node = nodes_map.get(eid)
                    if e_node and e_node["label"] == "ExtractedEntity":
                        if not e_node["properties"].get("community_name"):
                            e_node["properties"]["community_name"] = c_name

        node_type_counts: Dict[str, int] = {}
        for n in nodes:
            node_type_counts[n["label"]] = node_type_counts.get(n["label"], 0) + 1

        return {
            "nodes": nodes,
            "edges": edges,
            "statistics": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "node_types": node_type_counts,
            }
        }

    except ValueError:
        logger.error(f"无效的 end_user_id 格式: {end_user_id}")
        return {
            "nodes": [], "edges": [],
            "statistics": {"total_nodes": 0, "total_edges": 0, "node_types": {}, "edge_types": {}},
            "message": "无效的用户ID格式"
        }
    except Exception as e:
        logger.error(f"获取社区图谱数据失败: {str(e)}", exc_info=True)
        raise


def _extract_node_properties(
    label: str,
    properties: Dict[str, Any],
    *,
    rel_count: int,
) -> Dict[str, Any]:
    """根据节点类型从 ``properties`` 中提取受白名单约束的字段。

    白名单读取自 :data:`NODE_PROPERTY_WHITELIST`；当 ``label`` 未在白名单中
    时回落到 :data:`_DEFAULT_FIELDS`（仅 ``caption``）。``associative_memory``
    由调用方通过 ``rel_count`` 关键字参数注入，本函数不再发起 Cypher 查询，
    用于消除原先 ``count(r)`` 子查询造成的 N+1。

    保留 ``entity_type`` / ``emotion_type`` / ``emotion_subject`` 三个字段
    的枚举映射逻辑，以维持响应字段的中文化展示。

    Args:
        label: 节点类型标签（``labels(n)[0]``）。
        properties: 节点的全部属性。
        rel_count: 节点的关联边数量；由调用方批量计数后注入，写入返回字典的
            ``associative_memory`` 字段。

    Returns:
        过滤后的属性字典，至少包含 ``associative_memory`` 字段。
    """
    allowed_fields = NODE_PROPERTY_WHITELIST.get(label, _DEFAULT_FIELDS)

    filtered_props: Dict[str, Any] = {}
    for field in allowed_fields:
        if field not in properties:
            continue
        value = properties[field]
        mapper = _NODE_FIELD_VALUE_MAPPERS.get(field)
        if mapper is not None:
            value = mapper(value)
        filtered_props[field] = _clean_neo4j_value(value)
    filtered_props["associative_memory"] = rel_count
    return filtered_props


# 节点 ``properties`` 中需要做枚举/字典映射的字段。
# 修改本表即可扩展新的字段映射规则，无需触碰 ``_extract_node_properties`` 主体。
_NODE_FIELD_VALUE_MAPPERS: Dict[str, Callable[[Any], Any]] = {
    "entity_type": lambda v: type_mapping.get(v, ""),
    "emotion_type": lambda v: EmotionType.EMOTION_MAPPING.get(v),
    "emotion_subject": lambda v: EmotionSubject.SUBJECT_MAPPING.get(v),
}


def _resolve_edge_caption(
    rel_type: Optional[str],
    edge_props: Dict[str, Any],
) -> Optional[str]:
    """派生边的展示文案 ``caption``。

    取值优先级（与 docs/api/graph-data-quickref.md 描述一致）::

        1. ``edge_props.caption`` —— 写入端显式提供
        2. ``rel_type == "EXTRACTED_RELATIONSHIP"`` 时取 ``edge_props.predicate`` —— 实体—实体语义关系
        3. 回落到 ``rel_type`` 字面量

    Args:
        rel_type: Cypher ``type(r)`` 返回的关系类型字符串。
        edge_props: ``properties(r)`` 经过 ``_clean_neo4j_value`` 清洗后的关系属性。

    Returns:
        最终用于响应的 ``caption`` 字符串；若 ``rel_type`` 与 ``edge_props.caption``
        都缺失则返回 ``None``（实际场景下不会发生，因为 Cypher 必返回 ``rel_type``）。
    """
    explicit = edge_props.get("caption")
    if explicit:
        return explicit
    if rel_type == "EXTRACTED_RELATIONSHIP":
        return edge_props.get("predicate") or rel_type
    return rel_type


def _format_datetime_naive_iso(dt: datetime) -> str:
    """将 ``datetime`` 序列化为不带时区后缀的 ISO 字符串。

    Neo4j 节点写入端历史上既有用 naive ``datetime`` 的（``Statement`` / ``Chunk`` /
    ``MemorySummary`` 等），也有用 tz-aware ``datetime`` 的（``AssistantOriginal`` /
    ``AssistantPruned``）。直接 ``isoformat()`` 会让响应里两类节点的 ``created_at``
    一会儿带 ``+00:00`` 一会儿不带，前端体验不一致。

    本函数对 tz-aware 的 ``datetime`` 先转换为 UTC，再剥掉 ``tzinfo``，最后调用
    ``isoformat()``；naive 的 ``datetime`` 直接 ``isoformat()``。结果格式统一为
    形如 ``"2026-05-28T06:35:16.910751"``。
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _clean_neo4j_value(value: Any) -> Any:
    """
    清理单个值的 Neo4j 特殊类型
    
    Args:
        value: 需要清理的值
        
    Returns:
        清理后的值
    """
    if value is None:
        return None
    
    # 处理列表
    if isinstance(value, list):
        return [_clean_neo4j_value(item) for item in value]
    
    # 处理字典
    if isinstance(value, dict):
        return {k: _clean_neo4j_value(v) for k, v in value.items()}
    # 处理 Neo4j DateTime 类型
    if hasattr(value, '__class__') and 'neo4j.time' in str(type(value)):
        try:
            if hasattr(value, 'to_native'):
                native_dt = value.to_native()
                return _format_datetime_naive_iso(native_dt)
            return str(value)
        except Exception:
            return str(value)
    
    # 处理其他 Neo4j 特殊类型
    if hasattr(value, '__class__') and 'neo4j' in str(type(value)):
        try:
            return str(value)
        except Exception:
            return None
    # 返回原始值
    return value