"""
User Memory Service

处理用户记忆相关的业务逻辑，包括记忆洞察、用户摘要、节点统计和图数据等。
"""

import os
import uuid
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging_config import get_logger
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_episodic_schema import EmotionSubject, EmotionType, type_mapping
from app.services.implicit_memory_service import ImplicitMemoryService
from app.services.memory_base_service import MemoryBaseService, MemoryTransService, Translation_English
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_perceptual_service import MemoryPerceptualService
from app.services.memory_short_service import ShortService
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

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
            
            if config_id:
                config_service = MemoryConfigService(db)
                memory_config = config_service.load_memory_config(config_id)
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
    
    def update_end_user_profile(
        self,
        db: Session,
        end_user_id: str,
        profile_update: Any
    ) -> Dict[str, Any]:
        """
        更新终端用户的基本信息
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            profile_update: 包含更新字段的 Pydantic 模型
            
        Returns:
            {
                "success": bool,
                "data": dict,  # 更新后的用户档案数据
                "error": Optional[str]
            }
        """
        try:
            # 转换为UUID并查询用户
            user_uuid = uuid.UUID(end_user_id)
            repo = EndUserRepository(db)
            end_user = repo.get_by_id(user_uuid)
            
            if not end_user:
                logger.warning(f"终端用户不存在: end_user_id={end_user_id}")
                return {
                    "success": False,
                    "data": None,
                    "error": "终端用户不存在"
                }
            
            # 获取更新数据（排除 end_user_id 字段）
            update_data = profile_update.model_dump(exclude_unset=True, exclude={'end_user_id'})
            
            # 特殊处理 hire_date：如果提供了时间戳，转换为 DateTime
            if 'hire_date' in update_data:
                hire_date_timestamp = update_data['hire_date']
                if hire_date_timestamp is not None:
                    from app.core.api_key_utils import timestamp_to_datetime
                    update_data['hire_date'] = timestamp_to_datetime(hire_date_timestamp)
                # 如果是 None，保持 None（允许清空）
            
            # 更新字段
            for field, value in update_data.items():
                setattr(end_user, field, value)
            
            # 更新时间戳
            end_user.updated_at = datetime.now()
            end_user.updatetime_profile = datetime.now()
            
            # 提交更改
            db.commit()
            db.refresh(end_user)
            
            # 构建响应数据
            from app.schemas.end_user_schema import EndUserProfileResponse
            profile_data = EndUserProfileResponse(
                id=end_user.id,
                other_name=end_user.other_name,
                position=end_user.position,
                department=end_user.department,
                contact=end_user.contact,
                phone=end_user.phone,
                hire_date=end_user.hire_date,
                updatetime_profile=end_user.updatetime_profile
            )
            
            logger.info(f"成功更新用户信息: end_user_id={end_user_id}, updated_fields={list(update_data.keys())}")
            
            return {
                "success": True,
                "data": self.convert_profile_to_dict_with_timestamp(profile_data),
                "error": None
            }
            
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "success": False,
                "data": None,
                "error": "无效的用户ID格式"
            }
        except Exception as e:
            db.rollback()
            logger.error(f"用户信息更新失败: end_user_id={end_user_id}, error={str(e)}")
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }
    
    async def get_cached_memory_insight(
        self, 
        db: Session, 
        end_user_id: str,
        model_id: str,
        language_type: str
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
                if language_type!='zh':
                    memory_insight=await Translation_English(model_id,memory_insight)
                    behavior_pattern=await Translation_English(model_id,behavior_pattern)
                    growth_trajectory=await Translation_English(model_id,growth_trajectory)
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
            
            # 只有当明确要求英文且当前是中文时才翻译
            if language_type == 'en':
                user_summary=await Translation_English(model_id, user_summary)
                personality_traits = await Translation_English(model_id, personality_traits)
                core_values = await Translation_English(model_id, core_values)
                one_sentence_summary = await Translation_English(model_id, one_sentence_summary)
            
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
        workspace_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """
        生成并缓存记忆洞察
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            workspace_id: 工作空间ID (可选)
            
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
            logger.info(f"开始为 end_user_id {end_user_id} 生成记忆洞察")
            
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
                result = await analytics_memory_insight_report(end_user_id)
                
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
        workspace_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """
        生成并缓存用户摘要（四个部分）
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            workspace_id: 工作空间ID (可选)
            
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
            logger.info(f"开始为 end_user_id {end_user_id} 生成用户摘要")
            
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
                result = await analytics_user_summary(end_user_id)
                
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
        workspace_id: uuid.UUID
    ) -> Dict[str, Any]:
        """
        为整个工作空间生成缓存
        
        Args:
            db: 数据库会话
            workspace_id: 工作空间ID
            
        Returns:
            {
                "total_users": int,
                "successful": int,
                "failed": int,
                "errors": List[Dict]
            }
        """
        logger.info(f"开始为工作空间 {workspace_id} 批量生成缓存")
        
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
                    insight_result = await self.generate_and_cache_insight(db, end_user_id)
                    
                    # 生成用户摘要
                    summary_result = await self.generate_and_cache_summary(db, end_user_id)
                    
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

async def analytics_memory_insight_report(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    生成记忆洞察报告（四个维度）
    
    这个函数包含完整的业务逻辑：
    1. 使用 MemoryInsightHelper 工具类获取基础数据（领域分布、活跃时段、社交关联）
    2. 使用 Jinja2 模板渲染提示词
    3. 调用 LLM 生成四个维度的自然语言报告
    4. 解析并返回四个部分
    
    Args:
        end_user_id: 可选的终端用户ID
        
    Returns:
        包含四个维度报告的字典: {
            "memory_insight": str,           # 总体概述
            "behavior_pattern": str,         # 行为模式
            "key_findings": List[str],       # 关键发现（数组）
            "growth_trajectory": str         # 成长轨迹
        }
    """
    from app.core.memory.utils.prompt.prompt_utils import render_memory_insight_prompt
    import re
    
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
            social_connections=social_connections_str
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
        # 使用正则表达式提取四个部分
        memory_insight_match = re.search(r'【总体概述】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        behavior_match = re.search(r'【行为模式】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        findings_match = re.search(r'【关键发现】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        trajectory_match = re.search(r'【成长轨迹】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        
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


async def analytics_user_summary(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    生成用户摘要（包含四个部分）
    
    这个函数包含完整的业务逻辑：
    1. 使用 UserSummaryHelper 工具类获取基础数据（实体、语句）
    2. 使用 prompt_utils 渲染提示词
    3. 调用 LLM 生成四部分内容：基本介绍、性格特点、核心价值观、一句话总结
    
    Args:
        end_user_id: 可选的终端用户ID
        
    Returns:
        包含四部分摘要的字典: {
            "user_summary": str,
            "personality": str,
            "core_values": str,
            "one_sentence": str
        }
    """
    from app.core.memory.utils.prompt.prompt_utils import render_user_summary_prompt
    from app.core.config import settings
    from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import validate_language
    import re
    
    # 获取语言配置
    language = validate_language(settings.DEFAULT_LANGUAGE)
    
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
            language=language
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
    5. 隐性记忆 (IMPLICIT_MEMORY) = Statement 节点数量的三分之一
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
            conversations = conversation_repo.get_conversation_by_user_id(
                user_id=uuid.UUID(end_user_id),
                limit=100,  # 获取更多会话以准确统计
                is_activate=True
            )
            work_count = len(conversations)
            logger.debug(f"工作记忆数量（会话数）: {work_count} (end_user_id={end_user_id})")
        except Exception as e:
            logger.warning(f"获取会话数量失败，工作记忆数量设为0: {str(e)}")
            work_count = 0
    
    # 获取隐性记忆数量（基于 Statement 节点数量的三分之一）
    implicit_count = 0
    if end_user_id:
        try:
            # 查询 Statement 节点数量
            query = """
            MATCH (n:Statement)
            WHERE n.end_user_id = $end_user_id
            RETURN count(n) as count
            """
            result = await _neo4j_connector.execute_query(query, end_user_id=end_user_id)
            statement_count = result[0]["count"] if result and len(result) > 0 else 0
            # 取三分之一作为隐性记忆数量
            implicit_count = round(statement_count / 3)
            logger.debug(f"隐性记忆数量（Statement数量的1/3）: {implicit_count} (Statement总数={statement_count}, end_user_id={end_user_id})")
        except Exception as e:
            logger.warning(f"获取Statement数量失败，隐性记忆数量设为0: {str(e)}")
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
            short_term_service = ShortService(end_user_id)
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
            from app.services.memory_agent_service import get_end_user_connected_config
            from app.core.memory.storage_services.forgetting_engine.config_utils import load_actr_config_from_db
            
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
        "IMPLICIT_MEMORY": implicit_count,                        # 隐性记忆（Statement数量的1/3）
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
    limit: int = 130,
    depth: int = 1,
    center_node_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    获取 Neo4j 图数据，用于前端可视化
    
    Args:
        db: 数据库会话
        end_user_id: 终端用户ID
        node_types: 可选的节点类型列表
        limit: 返回节点数量限制
        depth: 图遍历深度
        center_node_id: 可选的中心节点ID
        
    Returns:
        包含节点、边和统计信息的字典
    """
    try:
        # 1. 获取 end_user_id
        user_uuid = uuid.UUID(end_user_id)
        repo = EndUserRepository(db)
        end_user = repo.get_by_id(user_uuid)
        
        if not end_user:
            logger.warning(f"未找到 end_user_id 为 {end_user_id} 的用户")
            return {
                "nodes": [],
                "edges": [],
                "statistics": {
                    "total_nodes": 0,
                    "total_edges": 0,
                    "node_types": {},
                    "edge_types": {}
                },
                "message": "用户不存在"
            }
        
        # 2. 构建节点查询
        if center_node_id:
            # 基于中心节点的扩展查询
            node_query = f"""
            MATCH path = (center)-[*1..{depth}]-(connected)
            WHERE center.end_user_id = $end_user_id
              AND elementId(center) = $center_node_id
            WITH collect(DISTINCT center) + collect(DISTINCT connected) as all_nodes
            UNWIND all_nodes as n
            RETURN DISTINCT 
                elementId(n) as id,
                labels(n)[0] as label,
                properties(n) as properties
            LIMIT $limit
            """
            node_params = {
                "end_user_id": end_user_id,
                "center_node_id": center_node_id,
                "limit": limit
            }
        elif node_types:
            # 按节点类型过滤查询
            node_query = """
            MATCH (n)
            WHERE n.end_user_id = $end_user_id
              AND labels(n)[0] IN $node_types
            RETURN 
                elementId(n) as id,
                labels(n)[0] as label,
                properties(n) as properties
            LIMIT $limit
            """
            node_params = {
                "end_user_id": end_user_id,
                "node_types": node_types,
                "limit": limit
            }
        else:
            # 查询所有节点
            node_query = """
            MATCH (n)
            WHERE n.end_user_id = $end_user_id
            RETURN 
                elementId(n) as id,
                labels(n)[0] as label,
                properties(n) as properties
            LIMIT $limit
            """
            node_params = {
                "end_user_id": end_user_id,
                "limit": limit
            }


        # 执行节点查询
        node_results = await _neo4j_connector.execute_query(node_query, **node_params)
        
        # 3. 格式化节点数据
        nodes = []
        node_ids = []
        node_type_counts = {}
        
        for record in node_results:
            node_id = record["id"]
            node_label = record["label"]
            node_props = record["properties"]
            
            # 根据节点类型提取需要的属性字段
            filtered_props = await _extract_node_properties(node_label, node_props,node_id)
            
            # 直接使用数据库中的 caption，如果没有则使用节点类型作为默认值
            caption = filtered_props.get("caption", node_label)
            
            nodes.append({
                "id": node_id,
                "label": node_label,
                "properties": filtered_props,
                "caption": caption
            })
            
            node_ids.append(node_id)
            node_type_counts[node_label] = node_type_counts.get(node_label, 0) + 1
        
        # 4. 查询节点之间的关系
        if len(node_ids) > 0:
            edge_query = """
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
            edge_results = await _neo4j_connector.execute_query(
                edge_query,
                node_ids=node_ids
            )
        else:
            edge_results = []
        
        # 5. 格式化边数据
        edges = []
        edge_type_counts = {}
        
        for record in edge_results:
            edge_id = record["id"]
            source = record["source"]
            target = record["target"]
            rel_type = record["rel_type"]
            edge_props = record["properties"]
            
            # 清理边属性中的 Neo4j 特殊类型
            # 对于边，我们保留所有属性，但清理特殊类型
            cleaned_edge_props = {}
            if edge_props:
                for key, value in edge_props.items():
                    cleaned_edge_props[key] = _clean_neo4j_value(value)
            
            # 直接使用关系类型作为 caption，如果 properties 中有 caption 则使用它
            caption = cleaned_edge_props.get("caption", rel_type)
            
            edges.append({
                "id": edge_id,
                "source": source,
                "target": target,
                "type": rel_type,
                "properties": cleaned_edge_props,
                "caption": caption
            })
            
            edge_type_counts[rel_type] = edge_type_counts.get(rel_type, 0) + 1
        
        # 6. 构建统计信息
        statistics = {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "node_types": node_type_counts,
            "edge_types": edge_type_counts
        }
        
        logger.info(
            f"成功获取图数据: end_user_id={end_user_id}, "
            f"nodes={len(nodes)}, edges={len(edges)}"
        )

        return {
            "nodes": nodes,
            "edges": edges,
            "statistics": statistics
        }
        
    except ValueError:
        logger.error(f"无效的 end_user_id 格式: {end_user_id}")
        return {
            "nodes": [],
            "edges": [],
            "statistics": {
                "total_nodes": 0,
                "total_edges": 0,
                "node_types": {},
                "edge_types": {}
            },
            "message": "无效的用户ID格式"
        }
    except Exception as e:
        logger.error(f"获取图数据失败: {str(e)}", exc_info=True)
        raise


# 辅助函数

async  def _extract_node_properties(label: str, properties: Dict[str, Any],node_id: str) -> Dict[str, Any]:
    """
    根据节点类型提取需要的属性字段
    
    Args:
        label: 节点类型标签
        properties: 节点的所有属性
        
    Returns:
        过滤后的属性字典
    """
    # 定义每种节点类型需要的字段（白名单）
    field_whitelist = {
        "Dialogue": ["content", "created_at"],
        "Chunk": ["content", "created_at"],
        "Statement": ["temporal_info", "stmt_type", "statement", "valid_at", "created_at", "caption","emotion_keywords","emotion_type","emotion_subject"],
        "ExtractedEntity": ["description", "name", "entity_type", "created_at", "caption","aliases","connect_strength"],
        "MemorySummary": ["summary", "content", "created_at", "caption"]  # 添加 content 字段
    }
    
    # 获取该节点类型的白名单字段
    allowed_fields = field_whitelist.get(label, [])

    count_neo4j=f"""MATCH (n)-[r]-(m) WHERE elementId(n) ="{node_id}" RETURN count(r) AS rel_count;"""
    node_results = await (_neo4j_connector.execute_query(count_neo4j))
    # 提取白名单中的字段
    filtered_props = {}
    for field in allowed_fields:
        if field in properties:
            value = properties[field]
            if  str(field) == 'entity_type':
                value=type_mapping.get(value,'')
            if str(field)=="emotion_type":
                value=EmotionType.EMOTION_MAPPING.get(value)
            if  str(field)=="emotion_subject":
                value=EmotionSubject.SUBJECT_MAPPING.get(value)
            filtered_props[field] = _clean_neo4j_value(value)
    filtered_props['associative_memory']=[i['rel_count'] for i in node_results][0]
    return filtered_props


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
                return native_dt.isoformat()
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