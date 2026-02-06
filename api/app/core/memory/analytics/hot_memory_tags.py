import asyncio
import json
import os
from typing import List, Tuple

from app.core.config import settings
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services.memory_config_service import MemoryConfigService
from pydantic import BaseModel, Field


# 定义用于LLM结构化输出的Pydantic模型
class FilteredTags(BaseModel):
    """用于接收LLM筛选后的核心标签列表的模型。"""
    meaningful_tags: List[str] = Field(..., description="从原始列表中筛选出的具有核心代表意义的名词列表。")

async def filter_tags_with_llm(tags: List[str], end_user_id: str) -> List[str]:
    """
    使用LLM筛选标签列表，仅保留具有代表性的核心名词。
    
    Args:
        tags: 原始标签列表
        end_user_id: 用户组ID，用于获取配置
        
    Returns:
        筛选后的标签列表
        
    Raises:
        ValueError: 如果无法获取有效的LLM配置
    """
    try:
        # Get config_id using get_end_user_connected_config
        with get_db_context() as db:
            from app.services.memory_agent_service import (
                get_end_user_connected_config,
            )
            
            connected_config = get_end_user_connected_config(end_user_id, db)
            config_id = connected_config.get("memory_config_id")
            workspace_id = connected_config.get("workspace_id")
            
            if not config_id and not workspace_id:
                raise ValueError(
                    f"No memory_config_id found for end_user_id: {end_user_id}. "
                    "Please ensure the user has a valid memory configuration."
                )
            
            # Use the config_id to get the proper LLM client with workspace fallback
            config_service = MemoryConfigService(db)
            memory_config = config_service.load_memory_config(
                config_id=config_id,
                workspace_id=workspace_id
            )
            
            if not memory_config.llm_model_id:
                raise ValueError(
                    f"No llm_model_id found in memory config {config_id}. "
                    "Please configure a valid LLM model."
                )
            
            factory = MemoryClientFactory(db)
            llm_client = factory.get_llm_client(memory_config.llm_model_id)

        # 3. 构建Prompt
        tag_list_str = ", ".join(tags)
        messages = [
            {
                "role": "system",
                "content": "你是一位顶级的文本分析专家，任务是提炼、筛选并合并最具体、最核心的名词。你的目标是识别具体的事件、地点、物体或作品，并严格执行以下步骤：\n\n1. **筛选**: 严格过滤掉以下类型的词语：\n    *   **抽象概念或训练活动**: 任何描述抽象品质、训练项目或研究过程的词语（例如：'核心力量', '实际的历史研究', '团队合作'）。\n    *   **动作或过程词**: 任何描述具体动作或过程的词语（例如：'打篮球', '快攻', '远投'）。\n    *   **描述性短语**: 任何描述状态、关系或感受的短语（例如：'配合越来越默契'）。\n    *   **过于宽泛的类别**: 过于笼统的分类（例如：'历史剧'）。\n\n2. **合并**: 在筛选后，对语义相近或存在包含关系的词语进行合并，只保留最核心、最具代表性的一个。\n    *   例如，在“篮球赛”和“篮球场”中，“篮球赛”是更核心的事件，应保留“篮球赛”。\n\n你的最终输出应该是一个精炼的、无重复概念的列表，只包含最具体、最具有代表性的名词。\n\n**示例**:\n输入: ['篮球赛', '篮球场', '核心力量', '实际的历史研究', '《二战全史》', '攀岩']\n筛选后: ['篮球赛', '篮球场', '《二战全史》', '攀岩']\n合并后最终输出: ['篮球赛', '《二战全史》', '攀岩']"
            },
            {
                "role": "user",
                "content": f"请从以下标签列表中筛选出核心名词: {tag_list_str}"
            }
        ]

        # 调用LLM进行结构化输出
        structured_response = await llm_client.response_structured(
            messages=messages,
            response_model=FilteredTags
        )

        return structured_response.meaningful_tags

    except Exception as e:
        print(f"LLM筛选过程中发生错误: {e}")
        # 在LLM失败时返回原始标签，确保流程继续
        return tags

async def get_raw_tags_from_db(
    connector: Neo4jConnector,
    end_user_id: str,
    limit: int,
    by_user: bool = False
) -> List[Tuple[str, int]]:
    """
    TODO: not accurate tag extraction
    从数据库查询原始的、未经过滤的实体标签及其频率。
    
    使用项目的Neo4jConnector进行查询，遵循仓储模式。

    Args:
        connector: Neo4j连接器实例
        end_user_id: 如果by_user=False，则为end_user_id；如果by_user=True，则为user_id
        limit: 返回的标签数量限制
        by_user: 是否按user_id查询（默认False，按end_user_id查询）
        
    Returns:
        List[Tuple[str, int]]: 标签名称和频率的元组列表
    """
    names_to_exclude = ['AI', 'Caroline', 'Melanie', 'Jon', 'Gina', '用户', 'AI助手', 'John', 'Maria']

    if by_user:
        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE e.user_id = $id AND e.entity_type <> '人物' AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC "
            "LIMIT $limit"
        )
    else:
        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE e.end_user_id = $id AND e.entity_type <> '人物' AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC "
            "LIMIT $limit"
        )

    # 使用项目的Neo4jConnector执行查询
    results = await connector.execute_query(
        query,
        id=end_user_id,
        limit=limit,
        names_to_exclude=names_to_exclude
    )
    
    return [(record["name"], record["frequency"]) for record in results]

async def get_hot_memory_tags(end_user_id: str, limit: int = 40, by_user: bool = False) -> List[Tuple[str, int]]:
    """
    获取原始标签，然后使用LLM进行筛选，返回最终的热门标签列表。
    查询更多的标签(limit=40)给LLM提供更丰富的上下文进行筛选。

    Args:
        end_user_id: 必需参数。如果by_user=False，则为end_user_id；如果by_user=True，则为user_id
        limit: 返回的标签数量限制
        by_user: 是否按user_id查询（默认False，按end_user_id查询）
        
    Raises:
        ValueError: 如果end_user_id未提供或为空
    """
    # 验证end_user_id必须提供且不为空
    if not end_user_id or not end_user_id.strip():
        raise ValueError(
            "end_user_id is required. Please provide a valid end_user_id or user_id."
        )
    
    # 使用项目的Neo4jConnector
    connector = Neo4jConnector()
    try:
        # 1. 从数据库获取原始排名靠前的标签
        raw_tags_with_freq = await get_raw_tags_from_db(connector, end_user_id, limit, by_user=by_user)
        if not raw_tags_with_freq:
            return []

        raw_tag_names = [tag for tag, freq in raw_tags_with_freq]

        # 2. 初始化LLM客户端并使用LLM筛选出有意义的标签
        meaningful_tag_names = await filter_tags_with_llm(raw_tag_names, end_user_id)

        # 3. 根据LLM的筛选结果，构建最终的标签列表（保留原始频率和顺序）
        final_tags = []
        for tag, freq in raw_tags_with_freq:
            if tag in meaningful_tag_names:
                final_tags.append((tag, freq))

        return final_tags
    finally:
        # 确保关闭连接
        await connector.close()
