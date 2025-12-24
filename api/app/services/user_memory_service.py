"""
User Memory Service

处理用户记忆相关的业务逻辑，包括记忆洞察、用户摘要、节点统计和图数据等。
"""

import uuid
from typing import Any, Dict, List, Optional

from app.core.logging_config import get_logger
from app.core.memory.analytics.memory_insight import MemoryInsight
from app.core.memory.analytics.user_summary import generate_user_summary
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
        从数据库获取缓存的用户摘要
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            
        Returns:
            {
                "summary": str,
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
                    "summary": None,
                    "updated_at": None,
                    "is_cached": False,
                    "message": "用户不存在"
                }
            
            # 检查是否有缓存数据
            if end_user.user_summary:
                logger.info(f"成功获取 end_user_id {end_user_id} 的缓存用户摘要")
                return {
                    "summary": end_user.user_summary,
                    "updated_at": end_user.user_summary_updated_at,
                    "is_cached": True
                }
            else:
                logger.info(f"end_user_id {end_user_id} 的用户摘要缓存为空")
                return {
                    "summary": None,
                    "updated_at": None,
                    "is_cached": False,
                    "message": "数据尚未生成，请稍后重试或联系管理员"
                }
                
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "summary": None,
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
        生成并缓存用户摘要
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID (UUID)
            workspace_id: 工作空间ID (可选)
            
        Returns:
            {
                "success": bool,
                "summary": str,
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
                    "summary": None,
                    "error": "用户不存在"
                }
            
            # 使用 end_user_id 调用分析函数
            try:
                logger.info(f"使用 end_user_id={end_user_id} 生成用户摘要")
                summary = await generate_user_summary(end_user_id)
                
                if not summary:
                    logger.warning(f"end_user_id {end_user_id} 的用户摘要生成结果为空")
                    return {
                        "success": False,
                        "summary": None,
                        "error": "生成的用户摘要为空,可能Neo4j中没有该用户的数据"
                    }
                
                # 更新数据库缓存
                success = repo.update_user_summary(user_uuid, summary)
                
                if success:
                    logger.info(f"成功为 end_user_id {end_user_id} 生成并缓存用户摘要")
                    return {
                        "success": True,
                        "summary": summary,
                        "error": None
                    }
                else:
                    logger.error(f"更新 end_user_id {end_user_id} 的用户摘要缓存失败")
                    return {
                        "success": False,
                        "summary": summary,
                        "error": "数据库更新失败"
                    }
                    
            except Exception as e:
                logger.error(f"调用分析函数生成用户摘要时出错: {str(e)}")
                return {
                    "success": False,
                    "summary": None,
                    "error": f"Neo4j或LLM服务不可用: {str(e)}"
                }
                
        except ValueError:
            logger.error(f"无效的 end_user_id 格式: {end_user_id}")
            return {
                "success": False,
                "summary": None,
                "error": "无效的用户ID格式"
            }
        except Exception as e:
            logger.error(f"生成并缓存用户摘要时出错: {str(e)}")
            return {
                "success": False,
                "summary": None,
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
    
    Args:
        end_user_id: 可选的终端用户ID
        
    Returns:
        包含报告的字典
    """
    insight = MemoryInsight(end_user_id)
    report = await insight.generate_insight_report()
    await insight.close()
    data = {"report": report}
    return data


async def analytics_user_summary(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    生成用户摘要
    
    Args:
        end_user_id: 可选的终端用户ID
        
    Returns:
        包含摘要的字典
    """
    summary = await generate_user_summary(end_user_id)
    data = {"summary": summary}
    return data


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
) -> Dict[str, Any]:
    """
    统计8种记忆类型的数量
    
    计算规则：
    1. 感知记忆 = statement + entity
    2. 工作记忆 = chunk + entity
    3. 短期记忆 = chunk
    4. 长期记忆 = entity
    5. 显性记忆 = 1/2 * entity
    6. 隐形记忆 = 1/3 * entity
    7. 情绪记忆 = statement
    8. 情景记忆 = memory_summary
    
    Args:
        db: 数据库会话
        end_user_id: 可选的终端用户ID (UUID)，用于过滤特定用户的节点
        
    Returns:
        {
            "感知记忆": int,
            "工作记忆": int,
            "短期记忆": int,
            "长期记忆": int,
            "显性记忆": int,
            "隐形记忆": int,
            "情绪记忆": int,
            "情景记忆": int
        }
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
    
    # 按规则计算8种记忆类型
    memory_types = {
        "感知记忆": statement_count + entity_count,
        "工作记忆": chunk_count + entity_count,
        "短期记忆": chunk_count,
        "长期记忆": entity_count,
        "显性记忆": entity_count // 2,  # 1/2 entity，使用整除
        "隐形记忆": entity_count // 3,  # 1/3 entity，使用整除
        "情绪记忆": statement_count,
        "情景记忆": memory_summary_count
    }
    
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
