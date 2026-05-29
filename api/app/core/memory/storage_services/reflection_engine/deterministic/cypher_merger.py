"""子问题 3 · 合并执行：Neo4j 事务（方案A和B共用）"""
import logging
from typing import Dict, List, Optional

from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


def choose_keeper(
    entity_a, entity_b,
    llm_winner: Optional[str] = None,
) -> tuple:
    """选择 keeper（保留方）

    优先级：LLM指定 > activation_value高 > 默认A
    支持 dict 和对象两种入参。
    """
    if llm_winner == "a":
        return entity_a, entity_b
    if llm_winner == "b":
        return entity_b, entity_a

    def _get_activation(e):
        if isinstance(e, dict):
            return e.get("activation_value", 0) or 0
        return getattr(e, "activation_value", 0) or 0

    act_a = _get_activation(entity_a)
    act_b = _get_activation(entity_b)
    if act_a != act_b:
        return (entity_a, entity_b) if act_a > act_b else (entity_b, entity_a)

    return (entity_a, entity_b)


async def execute_merge(
    connector: Neo4jConnector,
    end_user_id: str,
    keeper_id: str,
    loser_id: str,
    merged_name: str,
    merged_aliases: List[str],
) -> bool:
    """执行合并事务（属性合并 + 边迁移 + 删除 loser）

    单条 Cypher 事务，耗时约 20~50ms。
    如果 loser 已被其他进程删除，MATCH 返回空，自动跳过。
    """
    from app.repositories.neo4j.cypher_queries import DEDUP_MERGE_ENTITIES

    try:
        result = await connector.execute_query(
            DEDUP_MERGE_ENTITIES,
            end_user_id=end_user_id,
            keeper_id=keeper_id,
            loser_id=loser_id,
            merged_name=merged_name,
            merged_aliases=merged_aliases,
        )
        return bool(result)
    except Exception as e:
        logger.error(f"合并事务失败 keeper={keeper_id} loser={loser_id}: {e}")
        return False


def build_merged_aliases(keeper: Dict, loser: Dict, merged_name: str) -> List[str]:
    """构建合并后的 aliases 列表

    规则：两侧 aliases 并集 + loser.name 降为 alias，去掉 merged_name 本身
    """
    all_names = set()
    all_names.update(keeper.get("aliases") or [])
    all_names.update(loser.get("aliases") or [])
    if loser.get("name") and loser["name"] != merged_name:
        all_names.add(loser["name"])
    if keeper.get("name") and keeper["name"] != merged_name:
        all_names.add(keeper["name"])
    all_names.discard(merged_name)
    all_names.discard("")
    return sorted(all_names)