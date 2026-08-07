"""Neo4j 终端用户合并仓储。

将 merge_end_users 中的 Neo4j 操作从 service 层抽离到 repositories/neo4j 下，
Cypher 查询统一存放在 cypher_queries.py。
"""

import json
import logging
from typing import Any, List

from app.repositories.neo4j.cypher_queries import (
    END_USER_MERGE_DELETE_USER_ENTITY,
    END_USER_MERGE_FIND_USER_ENTITIES,
    END_USER_MERGE_REASSIGN_EDGES,
    END_USER_MERGE_REASSIGN_NODES,
    END_USER_MERGE_REASSIGN_USER_ENTITY,
    END_USER_MERGE_REDIRECT_INCOMING,
    END_USER_MERGE_REDIRECT_OUTGOING,
    END_USER_MERGE_UPDATE_USER,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


class EndUserMergeNeo4jRepository:
    """终端用户合并 — Neo4j 侧操作。"""

    def __init__(self, connector: Neo4jConnector):
        self.connector = connector

    async def reassign_all_to_target(
            self,
            source_ids: List[str],
            target_id: str,
    ) -> dict:
        """将 source 用户在 Neo4j 中的所有数据归入 target。

        Returns:
            {"nodes": int, "edges": int}
        """
        total_nodes = 0
        total_edges = 0

        for src_id in source_ids:
            await self._merge_user_entity_nodes(src_id, target_id)

            node_result = await self.connector.execute_query(
                END_USER_MERGE_REASSIGN_NODES,
                old_id=src_id,
                new_id=target_id,
            )
            total_nodes += node_result[0]["updated_nodes"] if node_result else 0

            edge_result = await self.connector.execute_query(
                END_USER_MERGE_REASSIGN_EDGES,
                old_id=src_id,
                new_id=target_id,
            )
            total_edges += edge_result[0]["updated_edges"] if edge_result else 0

            logger.info(
                f"[Neo4jMerge] src={src_id} → target={target_id}, "
                f"nodes={total_nodes}, edges={total_edges}"
            )

        return {"nodes": total_nodes, "edges": total_edges}

    async def _merge_user_entity_nodes(
            self, src_id_str: str, target_id_str: str
    ) -> None:
        src_records = await self.connector.execute_query(
            END_USER_MERGE_FIND_USER_ENTITIES,
            end_user_id=src_id_str,
        )
        if not src_records:
            return

        tgt_records = await self.connector.execute_query(
            END_USER_MERGE_FIND_USER_ENTITIES,
            end_user_id=target_id_str,
        )

        if tgt_records:
            await self._merge_into_target_user(
                src_id_str, target_id_str, src_records, tgt_records
            )
        else:
            await self.connector.execute_query(
                END_USER_MERGE_REASSIGN_USER_ENTITY,
                old_id=src_id_str,
                new_id=target_id_str,
            )
            logger.info(
                f"[Neo4jMerge] User 实体 end_user_id 变更: "
                f"{src_id_str} → {target_id_str}"
            )

    async def _merge_into_target_user(
            self,
            src_id_str: str,
            target_id_str: str,
            src_records: list,
            tgt_records: list,
    ) -> None:
        """双方都有 User 节点：合并描述 → 边重定向 → 删除 source。"""
        src_node = src_records[0].get("n", {})
        tgt_node = tgt_records[0].get("n", {})
        tgt_elem_id = tgt_records[0].get("elem_id", "")

        def _dedup_list(items: List[Any]) -> List[Any]:
            """列表去重，对 dict 使用 JSON 序列化作为去重键。"""
            seen: set = set()
            deduped: list = []
            for item in items:
                key = (
                    json.dumps(item, sort_keys=True, ensure_ascii=False)
                    if isinstance(item, dict)
                    else item
                )
                if key not in seen:
                    seen.add(key)
                    deduped.append(item)
            return deduped

        def _merge_separated_string(tgt_str: str, src_str: str, sep: str = "；") -> str:
            """合并两个以分隔符拼接的字符串，去重已存在的条目。

            description_timeline / event_timeline / description 在 Neo4j
            中存储为以 ``；``（U+FF1B）分隔的单个字符串，合并时需按条目级去重。
            """
            tgt_parts = [p.strip() for p in tgt_str.split(sep) if p.strip()] if tgt_str else []
            src_parts = [p.strip() for p in src_str.split(sep) if p.strip()] if src_str else []
            # 保留 tgt 顺序，仅追加 src 中未出现的条目
            existing = set(tgt_parts)
            new_parts = [p for p in src_parts if p not in existing]
            return sep.join(tgt_parts + new_parts)

        # --- 合并列表字段（去重） ---
        merged_aliases = _dedup_list(
            (tgt_node.get("aliases") or []) + (src_node.get("aliases") or [])
        )
        merged_anchors = _dedup_list(
            (tgt_node.get("anchors") or []) + (src_node.get("anchors") or [])
        )
        merged_beliefs_or_stances = _dedup_list(
            (tgt_node.get("beliefs_or_stances") or [])
            + (src_node.get("beliefs_or_stances") or [])
        )
        merged_core_facts = _dedup_list(
            (tgt_node.get("core_facts") or []) + (src_node.get("core_facts") or [])
        )
        merged_events = _dedup_list(
            (tgt_node.get("events") or []) + (src_node.get("events") or [])
        )
        merged_goals = _dedup_list(
            (tgt_node.get("goals") or []) + (src_node.get("goals") or [])
        )
        merged_interests = _dedup_list(
            (tgt_node.get("interests") or []) + (src_node.get("interests") or [])
        )
        merged_relations = _dedup_list(
            (tgt_node.get("relations") or []) + (src_node.get("relations") or [])
        )
        merged_traits = _dedup_list(
            (tgt_node.get("traits") or []) + (src_node.get("traits") or [])
        )

        # --- 合并以；分隔的字符串字段（条目级去重） ---
        merged_description_timeline = _merge_separated_string(
            tgt_node.get("description_timeline") or "",
            src_node.get("description_timeline") or "",
        )
        merged_event_timeline = _merge_separated_string(
            tgt_node.get("event_timeline") or "",
            src_node.get("event_timeline") or "",
        )

        # --- 合并普通字符串字段 ---
        src_desc = (src_node.get("description") or "").strip()
        tgt_desc = (tgt_node.get("description") or "").strip()
        merged_desc = tgt_desc
        if src_desc and tgt_desc:
            merged_desc = f"{tgt_desc}；{src_desc}"
        elif src_desc:
            merged_desc = src_desc

        src_summary = (src_node.get("description_summary") or "").strip()
        tgt_summary = (tgt_node.get("description_summary") or "").strip()
        merged_description_summary = tgt_summary
        if src_summary and tgt_summary:
            merged_description_summary = f"{tgt_summary}\n{src_summary}"
        elif src_summary:
            merged_description_summary = src_summary

        # --- 更新目标节点 ---
        await self.connector.execute_query(
            END_USER_MERGE_UPDATE_USER,
            elem_id=tgt_elem_id,
            description=merged_desc,
            description_summary=merged_description_summary,
            aliases=merged_aliases,
            anchors=merged_anchors,
            beliefs_or_stances=merged_beliefs_or_stances,
            core_facts=merged_core_facts,
            description_timeline=merged_description_timeline,
            event_timeline=merged_event_timeline,
            events=merged_events,
            goals=merged_goals,
            interests=merged_interests,
            relations=merged_relations,
            traits=merged_traits,
        )
        logger.info(f"[Neo4jMerge] User 实体属性合并完成: src={src_id_str}")

        await self.connector.execute_query(
            END_USER_MERGE_REDIRECT_INCOMING,
            old_id=src_id_str,
            new_id=target_id_str,
        )
        await self.connector.execute_query(
            END_USER_MERGE_REDIRECT_OUTGOING,
            old_id=src_id_str,
            new_id=target_id_str,
        )
        await self.connector.execute_query(
            END_USER_MERGE_DELETE_USER_ENTITY,
            old_id=src_id_str,
        )
        logger.info(f"[Neo4jMerge] 已删除 source User 实体: src={src_id_str}")
