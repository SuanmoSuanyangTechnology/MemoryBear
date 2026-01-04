"""
User Memory Service

处理用户记忆相关的业务逻辑，包括记忆洞察、用户摘要、节点统计和图数据等。
"""

import os
import uuid
from typing import Any, Dict, List, Optional

from app.core.logging_config import get_logger
from app.core.memory.analytics.memory_insight import MemoryInsight
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from sqlalchemy.orm import Session

logger = get_logger(__name__)

# Neo4j connector instance
_neo4j_connector = Neo4jConnector()


class UserMemoryService:
    """用户记忆服务类"""
    
    def __init__(self):
        logger.info("UserMemoryService initialized")
    
    async def get_cached_memory_insight(
        self, 
        db: Session, 
        end_user_id: str
    ) -> Dict[str, Any]:
        """
        从数据库获取缓存的记忆洞察
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            
        Returns:
            {
                "report": str,
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
                    "report": None,
                    "updated_at": None,
                    "is_cached": False,
                    "message": "用户不存在"
                }
            
            # 检查是否有缓存数据
            if end_user.memory_insight:
                logger.info(f"成功获取 end_user_id {end_user_id} 的缓存记忆洞察")
                return {
                    "report": end_user.memory_insight,
                    "updated_at": end_user.memory_insight_updated_at,
                    "is_cached": True
                }
            else:
                logger.info(f"end_user_id {end_user_id} 的记忆洞察缓存为空")
                return {
                    "report": None,
                    "updated_at": None,
                    "is_cached": False,
                    "message": "数据尚未生成，请稍后重试或联系管理员"
                }
                
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "report": None,
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
        end_user_id: str
    ) -> Dict[str, Any]:
        """
        从数据库获取缓存的用户摘要（四个部分）
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            
        Returns:
            {
                "basic_intro": str,
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
                    "basic_intro": None,
                    "personality": None,
                    "core_values": None,
                    "one_sentence": None,
                    "updated_at": None,
                    "is_cached": False,
                    "message": "用户不存在"
                }
            
            # 检查是否有缓存数据（至少有一个字段不为空）
            has_cache = any([
                end_user.memory_insight,
                end_user.personality_traits,
                end_user.core_values,
                end_user.one_sentence_summary
            ])
            
            if has_cache:
                logger.info(f"成功获取 end_user_id {end_user_id} 的缓存用户摘要")
                return {
                    "basic_intro": end_user.memory_insight,
                    "personality": end_user.personality_traits,
                    "core_values": end_user.core_values,
                    "one_sentence": end_user.one_sentence_summary,
                    "updated_at": end_user.user_summary_updated_at,
                    "is_cached": True
                }
            else:
                logger.info(f"end_user_id {end_user_id} 的用户摘要缓存为空")
                return {
                    "basic_intro": None,
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
                "basic_intro": None,
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
                "report": str,
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
                    "report": None,
                    "error": "用户不存在"
                }
            
            # 使用 end_user_id 调用分析函数
            try:
                logger.info(f"使用 end_user_id={end_user_id} 生成记忆洞察")
                result = await analytics_memory_insight_report(end_user_id)
                report = result.get("report", "")
                
                if not report:
                    logger.warning(f"end_user_id {end_user_id} 的记忆洞察生成结果为空")
                    return {
                        "success": False,
                        "report": None,
                        "error": "生成的洞察报告为空,可能Neo4j中没有该用户的数据"
                    }
                
                # 更新数据库缓存
                success = repo.update_memory_insight(user_uuid, report)
                
                if success:
                    logger.info(f"成功为 end_user_id {end_user_id} 生成并缓存记忆洞察")
                    return {
                        "success": True,
                        "report": report,
                        "error": None
                    }
                else:
                    logger.error(f"更新 end_user_id {end_user_id} 的记忆洞察缓存失败")
                    return {
                        "success": False,
                        "report": report,
                        "error": "数据库更新失败"
                    }
                    
            except Exception as e:
                logger.error(f"调用分析函数生成记忆洞察时出错: {str(e)}")
                return {
                    "success": False,
                    "report": None,
                    "error": f"Neo4j或LLM服务不可用: {str(e)}"
                }
                
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "success": False,
                "report": None,
                "error": "无效的用户ID格式"
            }
        except Exception as e:
            logger.error(f"生成并缓存记忆洞察时出错: {str(e)}")
            return {
                "success": False,
                "report": None,
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
                "basic_intro": str,
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
                    "basic_intro": None,
                    "personality": None,
                    "core_values": None,
                    "one_sentence": None,
                    "error": "用户不存在"
                }
            
            # 使用 end_user_id 调用分析函数
            try:
                logger.info(f"使用 end_user_id={end_user_id} 生成用户摘要")
                result = await analytics_user_summary(end_user_id)
                
                basic_intro = result.get("basic_intro", "")
                personality = result.get("personality", "")
                core_values = result.get("core_values", "")
                one_sentence = result.get("one_sentence", "")
                
                if not any([basic_intro, personality, core_values, one_sentence]):
                    logger.warning(f"end_user_id {end_user_id} 的用户摘要生成结果为空")
                    return {
                        "success": False,
                        "basic_intro": None,
                        "personality": None,
                        "core_values": None,
                        "one_sentence": None,
                        "error": "生成的用户摘要为空,可能Neo4j中没有该用户的数据"
                    }
                
                # 更新数据库缓存
                success = repo.update_user_summary(
                    user_uuid, 
                    basic_intro, 
                    personality, 
                    core_values, 
                    one_sentence
                )
                
                if success:
                    logger.info(f"成功为 end_user_id {end_user_id} 生成并缓存用户摘要")
                    return {
                        "success": True,
                        "basic_intro": basic_intro,
                        "personality": personality,
                        "core_values": core_values,
                        "one_sentence": one_sentence,
                        "error": None
                    }
                else:
                    logger.error(f"更新 end_user_id {end_user_id} 的用户摘要缓存失败")
                    return {
                        "success": False,
                        "basic_intro": basic_intro,
                        "personality": personality,
                        "core_values": core_values,
                        "one_sentence": one_sentence,
                        "error": "数据库更新失败"
                    }
                    
            except Exception as e:
                logger.error(f"调用分析函数生成用户摘要时出错: {str(e)}")
                return {
                    "success": False,
                    "basic_intro": None,
                    "personality": None,
                    "core_values": None,
                    "one_sentence": None,
                    "error": f"Neo4j或LLM服务不可用: {str(e)}"
                }
                
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "success": False,
                "basic_intro": None,
                "personality": None,
                "core_values": None,
                "one_sentence": None,
                "error": "无效的用户ID格式"
            }
        except Exception as e:
            logger.error(f"生成并缓存用户摘要时出错: {str(e)}")
            return {
                "success": False,
                "basic_intro": None,
                "personality": None,
                "core_values": None,
                "one_sentence": None,
                "error": str(e)
            }
    
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
    生成记忆洞察报告
    
    这个函数包含完整的业务逻辑：
    1. 使用 MemoryInsight 工具类获取基础数据（领域分布、活跃时段、社交关联）
    2. 构建提示词
    3. 调用 LLM 生成自然语言报告
    
    Args:
        end_user_id: 可选的终端用户ID
        
    Returns:
        包含报告的字典
    """
    insight = MemoryInsight(end_user_id)
    
    try:
        # 1. 并行获取三个维度的数据
        import asyncio
        domain_dist, active_periods, social_conn = await asyncio.gather(
            insight.get_domain_distribution(),
            insight.get_active_periods(),
            insight.get_social_connections(),
        )
        
        # 2. 构建提示词要点
        prompt_parts = []
        
        if domain_dist:
            top_domains = ", ".join([f"{k}({v:.0%})" for k, v in list(domain_dist.items())[:3]])
            prompt_parts.append(f"- 核心领域: 用户的记忆主要集中在 {top_domains}。")
        
        if active_periods:
            months_str = " 和 ".join(map(str, active_periods))
            prompt_parts.append(f"- 活跃时段: 用户在每年的 {months_str} 月最为活跃。")
        
        if social_conn:
            prompt_parts.append(
                f"- 社交关联: 与用户\"{social_conn['user_id']}\"拥有最多共同记忆({social_conn['common_memories_count']}条)，时间范围主要在 {social_conn['time_range']}。"
            )
        
        # 3. 如果没有足够数据，返回默认消息
        if not prompt_parts:
            return {"report": "暂无足够数据生成洞察报告。"}
        
        # 4. 构建 LLM 提示词
        system_prompt = '''你是一位资深的个人记忆分析师。你的任务是根据我提供的要点，为用户生成一段简洁、自然、个性化的记忆洞察报告。

重要规则：
1. 报告需要将所有要点流畅地串联成一个段落
2. 语言风格要亲切、易于理解，就像和朋友聊天一样
3. 不要添加任何额外的解释或标题，直接输出报告内容
4. 只使用我提供的要点，不要编造或推测任何信息
5. 如果某个维度没有数据（如没有活跃时段信息），就不要在报告中提及该维度

例如，如果输入是：
- 核心领域: 用户的记忆主要集中在 旅行(38%), 工作(24%), 家庭(21%)。
- 活跃时段: 用户在每年的 4 和 10 月最为活跃。
- 社交关联: 与用户"张明"拥有最多共同记忆(47条)，时间范围主要在 2017-2020。

你的输出应该是：
"您的记忆集中在旅行(38%)、工作(24%)和家庭(21%)三大领域。每年4月和10月是您最活跃的记录期，可能与春秋季旅行计划相关。您与'张明'共同拥有最多记忆(47条)，主要集中在2017-2020年间。"

如果输入只有：
- 核心领域: 用户的记忆主要集中在 教育(65%), 学习(25%)。

你的输出应该是：
"您的记忆主要集中在教育(65%)和学习(25%)两大领域，显示出您对知识和成长的持续关注。"'''
        
        user_prompt = "\n".join(prompt_parts)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # 5. 调用 LLM 生成报告
        response = await insight.llm_client.chat(messages=messages)
        
        # 6. 处理 LLM 响应，确保返回字符串类型
        content = response.content
        if isinstance(content, list):
            # 如果是列表格式（如 [{'type': 'text', 'text': '...'}]），提取文本
            if len(content) > 0:
                if isinstance(content[0], dict):
                    # 尝试提取 'text' 字段
                    text = content[0].get('text', content[0].get('content', str(content[0])))
                    report = str(text)
                else:
                    report = str(content[0])
            else:
                report = ""
        elif isinstance(content, dict):
            # 如果是字典格式，提取 text 字段
            report = str(content.get('text', content.get('content', str(content))))
        else:
            # 已经是字符串或其他类型，转为字符串
            report = str(content) if content is not None else ""
        
        return {"report": report}
        
    finally:
        # 确保关闭连接
        await insight.close()


async def analytics_user_summary(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    生成用户摘要（包含四个部分）
    
    这个函数包含完整的业务逻辑：
    1. 使用 UserSummary 工具类获取基础数据（实体、语句）
    2. 使用 prompt_utils 渲染提示词
    3. 调用 LLM 生成四部分内容：基本介绍、性格特点、核心价值观、一句话总结
    
    Args:
        end_user_id: 可选的终端用户ID
        
    Returns:
        包含四部分摘要的字典: {
            "basic_intro": str,
            "personality": str,
            "core_values": str,
            "one_sentence": str
        }
    """
    from app.core.memory.analytics.user_summary import UserSummary
    from app.core.memory.utils.prompt.prompt_utils import render_user_summary_prompt
    import re
    
    # 创建 UserSummary 实例
    user_summary = UserSummary(end_user_id or os.getenv("SELECTED_GROUP_ID", "group_123"))
    
    try:
        # 1) 收集上下文数据
        entities = await user_summary._get_top_entities(limit=40)
        statements = await user_summary._get_recent_statements(limit=100)

        entity_lines = [f"{name} ({freq})" for name, freq in entities][:20]
        statement_samples = [s.statement.strip() for s in statements if (s.statement or '').strip()][:20]

        # 2) 使用 prompt_utils 渲染提示词
        user_prompt = await render_user_summary_prompt(
            user_id=user_summary.user_id,
            entities=", ".join(entity_lines) if entity_lines else "(空)",
            statements=" | ".join(statement_samples) if statement_samples else "(空)"
        )

        messages = [
            {"role": "user", "content": user_prompt},
        ]

        # 3) 调用 LLM 生成摘要
        response = await user_summary.llm.chat(messages=messages)
        
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
        # 使用正则表达式提取四个部分
        basic_intro_match = re.search(r'【基本介绍】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        personality_match = re.search(r'【性格特点】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        core_values_match = re.search(r'【核心价值观】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        one_sentence_match = re.search(r'【一句话总结】\s*\n(.*?)(?=\n【|$)', full_response, re.DOTALL)
        
        basic_intro = basic_intro_match.group(1).strip() if basic_intro_match else ""
        personality = personality_match.group(1).strip() if personality_match else ""
        core_values = core_values_match.group(1).strip() if core_values_match else ""
        one_sentence = one_sentence_match.group(1).strip() if one_sentence_match else ""
        
        return {
            "basic_intro": basic_intro,
            "personality": personality,
            "core_values": core_values,
            "one_sentence": one_sentence
        }
        
    finally:
        # 确保关闭连接
        await user_summary.close()


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
            WHERE n.group_id = $group_id
            RETURN count(n) as count
            """
            result = await _neo4j_connector.execute_query(query, group_id=end_user_id)
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
    1. 感知记忆 (PERCEPTUAL_MEMORY) = statement + entity
    2. 工作记忆 (WORKING_MEMORY) = chunk + entity
    3. 短期记忆 (SHORT_TERM_MEMORY) = chunk
    4. 长期记忆 (LONG_TERM_MEMORY) = entity
    5. 显性记忆 (EXPLICIT_MEMORY) = 1/2 * entity
    6. 隐性记忆 (IMPLICIT_MEMORY) = 1/3 * entity
    7. 情绪记忆 (EMOTIONAL_MEMORY) = statement
    8. 情景记忆 (EPISODIC_MEMORY) = memory_summary
    
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
        - LONG_TERM_MEMORY: 长期记忆
        - EXPLICIT_MEMORY: 显性记忆
        - IMPLICIT_MEMORY: 隐性记忆
        - EMOTIONAL_MEMORY: 情绪记忆
        - EPISODIC_MEMORY: 情景记忆
    """
    # 定义需要查询的节点类型
    node_types = {
        "Statement": "Statement",
        "Entity": "ExtractedEntity",
        "Chunk": "Chunk",
        "MemorySummary": "MemorySummary"
    }
    
    # 存储每种节点类型的计数
    node_counts = {}
    
    # 查询每种节点类型的数量
    for key, node_type in node_types.items():
        if end_user_id:
            query = f"""
            MATCH (n:{node_type})
            WHERE n.group_id = $group_id
            RETURN count(n) as count
            """
            result = await _neo4j_connector.execute_query(query, group_id=end_user_id)
        else:
            query = f"""
            MATCH (n:{node_type})
            RETURN count(n) as count
            """
            result = await _neo4j_connector.execute_query(query)
        
        # 提取计数结果
        count = result[0]["count"] if result and len(result) > 0 else 0
        node_counts[key] = count
    
    # 获取各节点类型的数量
    statement_count = node_counts.get("Statement", 0)
    entity_count = node_counts.get("Entity", 0)
    chunk_count = node_counts.get("Chunk", 0)
    memory_summary_count = node_counts.get("MemorySummary", 0)
    
    # 按规则计算8种记忆类型的数量（使用英文枚举作为key）
    memory_counts = {
        "PERCEPTUAL_MEMORY": statement_count + entity_count,      # 感知记忆
        "WORKING_MEMORY": chunk_count + entity_count,             # 工作记忆
        "SHORT_TERM_MEMORY": chunk_count,                         # 短期记忆
        "LONG_TERM_MEMORY": entity_count,                         # 长期记忆
        "EXPLICIT_MEMORY": entity_count // 2,                     # 显性记忆 (1/2 entity)
        "IMPLICIT_MEMORY": entity_count // 3,                     # 隐性记忆 (1/3 entity)
        "EMOTIONAL_MEMORY": statement_count,                      # 情绪记忆
        "EPISODIC_MEMORY": memory_summary_count                   # 情景记忆
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
        # 1. 获取 group_id
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
            WHERE center.group_id = $group_id
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
                "group_id": end_user_id,
                "center_node_id": center_node_id,
                "limit": limit
            }
        elif node_types:
            # 按节点类型过滤查询
            node_query = """
            MATCH (n)
            WHERE n.group_id = $group_id
              AND labels(n)[0] IN $node_types
            RETURN 
                elementId(n) as id,
                labels(n)[0] as label,
                properties(n) as properties
            LIMIT $limit
            """
            node_params = {
                "group_id": end_user_id,
                "node_types": node_types,
                "limit": limit
            }
        else:
            # 查询所有节点
            node_query = """
            MATCH (n)
            WHERE n.group_id = $group_id
            RETURN 
                elementId(n) as id,
                labels(n)[0] as label,
                properties(n) as properties
            LIMIT $limit
            """
            node_params = {
                "group_id": end_user_id,
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
            filtered_props = _extract_node_properties(node_label, node_props)
            
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

def _extract_node_properties(label: str, properties: Dict[str, Any]) -> Dict[str, Any]:
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
        "Statement": ["temporal_info", "stmt_type", "statement", "valid_at", "created_at", "caption"],
        "ExtractedEntity": ["description", "name", "entity_type", "created_at", "caption"],
        "MemorySummary": ["summary", "content", "created_at", "caption"]  # 添加 content 字段
    }
    
    # 获取该节点类型的白名单字段
    allowed_fields = field_whitelist.get(label, [])
    
    # 如果没有定义白名单，返回空字典（或者可以返回所有字段）
    if not allowed_fields:
        # 对于未定义的节点类型，只返回基本字段
        allowed_fields = ["name", "created_at", "caption"]
    
    # 提取白名单中的字段
    filtered_props = {}
    for field in allowed_fields:
        if field in properties:
            value = properties[field]
            # 清理 Neo4j 特殊类型
            filtered_props[field] = _clean_neo4j_value(value)
    
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
