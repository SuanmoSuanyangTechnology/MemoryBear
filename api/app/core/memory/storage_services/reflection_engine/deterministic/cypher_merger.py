"""子问题 3 · 合并执行：Neo4j 事务（方案A和B共用）"""
import logging
from typing import Dict, List, Optional, Tuple

from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


def choose_keeper(
    entity_a, entity_b,
    llm_winner: Optional[str] = None,
    degree_a: int = 0,
    degree_b: int = 0,
) -> tuple:
    """选择 keeper（保留方）

    优先级：LLM指定 > 默认A

    NOTE: 当前版本暂时关闭"度数优先"。下版本打开时把"度数绝对优先"块取消注释,
    并恢复 fetch_degrees / fetch_degrees_batch 的调用即可。degree_a/degree_b 参数
    仍保留以保持调用方签名兼容。
    """
    # === 度数优先（暂时关闭，下版打开）===
    # 度数优先是为了让度数小的当 loser，最小化边迁移量、规避超级节点 OOM。
    # if degree_a != degree_b:
    #     return (entity_a, entity_b) if degree_a > degree_b else (entity_b, entity_a)
    # ====================================

    # LLM 指定
    if llm_winner == "a":
        return entity_a, entity_b
    if llm_winner == "b":
        return entity_b, entity_a

    # 默认 A
    return (entity_a, entity_b)


# === 度数查询（暂时关闭，下版打开 choose_keeper 度数优先时一并恢复）===
# async def fetch_degrees(
#     connector: Neo4jConnector,
#     end_user_id: str,
#     id_a: str,
#     id_b: str,
# ) -> Tuple[int, int]:
#     """查两实体度数，返回 (degree_a, degree_b)；查不到按 0 处理"""
#     from app.repositories.neo4j.cypher_queries import ENTITY_DEGREE_COUNT
#     try:
#         rows = await connector.execute_query(
#             ENTITY_DEGREE_COUNT,
#             end_user_id=end_user_id, id_a=id_a, id_b=id_b,
#         )
#         deg = {r["id"]: r["degree"] for r in rows}
#         return deg.get(id_a, 0), deg.get(id_b, 0)
#     except Exception as e:
#         logger.warning(f"度数查询失败 {id_a}/{id_b}: {e}")
#         return 0, 0
#
#
# async def fetch_degrees_batch(
#     connector: Neo4jConnector,
#     end_user_id: str,
#     ids: List[str],
# ) -> Dict[str, int]:
#     """批量查多个实体度数，返回 {id: degree}；查不到的按 0 处理（调用方用 .get(id, 0)）。
#
#     用于方案B 同名同类型直合的桶内度数批量获取，避免 N-1 次成对查询。
#     """
#     from app.repositories.neo4j.cypher_queries import ENTITY_DEGREES_BY_IDS
#     if not ids:
#         return {}
#     try:
#         rows = await connector.execute_query(
#             ENTITY_DEGREES_BY_IDS,
#             end_user_id=end_user_id, ids=ids,
#         )
#         return {r["id"]: r["degree"] for r in rows}
#     except Exception as e:
#         logger.warning(f"批量度数查询失败 ids_count={len(ids)}: {e}")
#         return {}
# ========================================================================


async def execute_merge(
    connector: Neo4jConnector,
    end_user_id: str,
    keeper_id: str,
    loser_id: str,
    merged_name: str,
    merged_aliases: List[str],
    loser_degree: int = 0,
    merge_max_degree: int = 1000,
) -> str:
    """执行合并事务（属性合并 + 边迁移 + 删除 loser）

    返回状态："success" | "skipped_super_node" | "failed"
    - loser_degree > merge_max_degree：跳过（避免单事务 OOM / 锁阻塞），仅 logger 记录。
    - 否则执行单条 Cypher 事务（原子）。

    NOTE: 当前版本暂时关闭超级节点保护（loser_degree / merge_max_degree 参数仍保留，
    用于度数优先的 keeper 选择 / 调用方签名兼容）。下个版本恢复时把下方 if 块取消注释即可。
    """
    from app.repositories.neo4j.cypher_queries import DEDUP_MERGE_ENTITIES

    # === 超级节点保护（暂时关闭，下版打开）===
    # if loser_degree > merge_max_degree:
    #     logger.warning(
    #         f"跳过超级节点合并 keeper={keeper_id} loser={loser_id} "
    #         f"loser_degree={loser_degree} > merge_max_degree={merge_max_degree}"
    #     )
    #     return "skipped_super_node"
    # ========================================

    try:
        result = await connector.execute_query(
            DEDUP_MERGE_ENTITIES,
            end_user_id=end_user_id,
            keeper_id=keeper_id,
            loser_id=loser_id,
            merged_name=merged_name,
            merged_aliases=merged_aliases,
        )
        return "success" if result else "failed"
    except Exception as e:
        logger.error(f"合并事务失败 keeper={keeper_id} loser={loser_id}: {e}")
        return "failed"


def build_merged_aliases(
    keeper: Dict, loser: Dict, merged_name: str,
    new_aliases: Optional[List[str]] = None,
) -> List[str]:
    """构建合并后的 aliases 列表

    规则：两侧已有 aliases 并集 ∪ LLM new_aliases，去掉 merged_name 与空串。
    不再做旧名降级（旧名是否保留为别名由 LLM 在 new_aliases 决定）。
    """
    all_names = set()
    all_names.update(keeper.get("aliases") or [])
    all_names.update(loser.get("aliases") or [])
    if new_aliases:
        all_names.update(new_aliases)
    all_names.discard(merged_name)
    all_names.discard("")
    return sorted(all_names)