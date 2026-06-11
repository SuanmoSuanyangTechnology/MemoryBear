"""子问题 3 · 方案A确定性层：两路候选召回 + 归一化打分"""
import math
import logging
from typing import List, Dict, Tuple

from pydantic import BaseModel, Field
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.storage_services.extraction_engine.deduplication.second_layer_dedup import (
    _row_to_entity,
)
from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
    _would_merge_cross_role,
)
from app.core.memory.utils.name_similarity_utils import (
    name_similarity_with_aliases,
    has_exact_alias_match,
)

logger = logging.getLogger(__name__)

# _row_to_entity 需要的默认填充字段
_ENTITY_DEFAULTS = {"created_at": "", "entity_idx": 0, "statement_id": "", "connect_strength": ""}


def _prefixed_row(row: dict, prefix: str, end_user_id: str) -> dict:
    """从带前缀的 Cypher 行提取单侧实体字段，补全 _row_to_entity 所需的默认值"""
    return {
        "id": row[f"{prefix}_id"],
        "name": row[f"{prefix}_name"],
        "entity_type": row["entity_type"],
        "description": row.get(f"{prefix}_desc", ""),
        "aliases": row.get(f"{prefix}_aliases") or [],
        "name_embedding": [],
        "end_user_id": end_user_id,
        **_ENTITY_DEFAULTS,
    }


class DedupCandidatePair(BaseModel):
    """去重候选对"""
    a_id: str
    b_id: str
    a_name: str = ""
    b_name: str = ""
    entity_type: str = ""
    a_desc: str = ""
    b_desc: str = ""
    a_aliases: List[str] = Field(default_factory=list)
    b_aliases: List[str] = Field(default_factory=list)
    sim_name: float = 0.0       # 路径A：名称相似度（0~1）
    sim_embed: float = 0.0      # 路径B：向量相似度（0~1）
    probability: float = 0.0    # sigmoid 综合打分（0~1）
    source_paths: List[str] = Field(default_factory=list)  # ["name", "embed"]


async def fetch_name_candidates(
    connector: Neo4jConnector,
    end_user_id: str,
    candidate_cap: int = 500,
) -> List[DedupCandidatePair]:
    """路径 A：名称相似度候选召回 + Python 二次打分

    流程：
      1. Cypher 粗筛（name CONTAINS + aliases 交集）→ 宽松召回
      2. _row_to_entity 转为 ExtractedEntityNode 对象
      3. _would_merge_cross_role 角色保护过滤
      4. _name_similarity_with_aliases 精确打分
      5. 保留 sim ≥ 0.85 或别名完全匹配的候选
    """
    from app.repositories.neo4j.cypher_queries import DEDUP_CANDIDATES_BY_NAME

    rows = await connector.execute_query(
        DEDUP_CANDIDATES_BY_NAME,
        end_user_id=end_user_id,
        candidate_cap=candidate_cap,
    )

    candidates = []
    for row in rows:
        entity_a = _row_to_entity(_prefixed_row(row, "a", end_user_id))
        entity_b = _row_to_entity(_prefixed_row(row, "b", end_user_id))

        if _would_merge_cross_role(entity_a, entity_b):
            continue

        # emb_sim 由 Neo4j 侧 vector.similarity.cosine 算好直接传入，避免拉回向量在 Python 重算
        emb_sim = row.get("emb_sim") or 0.0
        sim_result = name_similarity_with_aliases(entity_a, entity_b, emb_sim=emb_sim)
        sim = sim_result[0]

        has_exact = has_exact_alias_match(entity_a, entity_b)

        if sim >= 0.85 or has_exact:
            # 别名完全匹配时保底 sim_name=0.85，避免被 emb_sim=0 拉低导致丢弃
            if has_exact:
                sim = max(sim, 0.85)
            candidates.append(DedupCandidatePair(
                a_id=row["a_id"], b_id=row["b_id"],
                a_name=row["a_name"], b_name=row["b_name"],
                entity_type=row["entity_type"],
                a_desc=row.get("a_desc", ""),
                b_desc=row.get("b_desc", ""),
                a_aliases=row.get("a_aliases") or [],
                b_aliases=row.get("b_aliases") or [],
                sim_name=sim,
                source_paths=["name"],
            ))
    return candidates


async def fetch_embed_candidates(
    connector: Neo4jConnector,
    end_user_id: str,
    top_k: int = 100,
    theta_embed_floor: float = 0.85,
    candidate_cap: int = 500,
) -> List[DedupCandidatePair]:
    """路径 B：向量相似度候选召回

    Neo4j 向量索引直接返回 cosine score，无需 Python 二次打分。
    top_k=100 是因为全局索引需要 post-filter 穿透跨用户干扰。
    如果向量索引查询失败（如维度不匹配），降级返回空列表。
    """
    from app.repositories.neo4j.cypher_queries import DEDUP_CANDIDATES_BY_EMBED

    try:
        rows = await connector.execute_query(
            DEDUP_CANDIDATES_BY_EMBED,
            end_user_id=end_user_id,
            top_k=top_k,
            theta_embed_floor=theta_embed_floor,
            candidate_cap=candidate_cap,
        )
    except Exception as e:
        logger.warning(f"路径B向量召回失败（降级跳过）: {e}")
        return []

    return [
        DedupCandidatePair(
            a_id=row["a_id"], b_id=row["b_id"],
            a_name=row["a_name"], b_name=row["b_name"],
            entity_type=row["entity_type"],
            a_desc=row.get("a_desc", ""),
            b_desc=row.get("b_desc", ""),
            a_aliases=row.get("a_aliases") or [],
            b_aliases=row.get("b_aliases") or [],
            sim_embed=row["sim_embed"],
            source_paths=["embed"],
        )
        for row in rows
    ]


def merge_and_score(
    name_cands: List[DedupCandidatePair],
    embed_cands: List[DedupCandidatePair],
    alpha: float = 0.4,
    beta: float = 0.6,
) -> List[DedupCandidatePair]:
    """合并两路候选 + sigmoid 归一化打分

    打分策略：只用有值的路径参与计算，权重归一化，缺失路径不惩罚。

    公式：
      有效权重 = Σ(有值路径的权重)
      logit = Σ(score × weight) / 有效权重
      probability = 1 / (1 + e^(-4×(logit-0.5)))
    """
    merged: Dict[Tuple[str, str], DedupCandidatePair] = {}

    for pair in name_cands + embed_cands:
        key = tuple(sorted((pair.a_id, pair.b_id)))
        if key not in merged:
            merged[key] = pair
        else:
            existing = merged[key]
            existing.sim_name = max(existing.sim_name, pair.sim_name)
            existing.sim_embed = max(existing.sim_embed, pair.sim_embed)
            existing.source_paths = list(set(existing.source_paths + pair.source_paths))

    for pair in merged.values():
        scores, weights = [], []
        if pair.sim_name > 0:
            scores.append(pair.sim_name)
            weights.append(alpha)
        if pair.sim_embed > 0:
            scores.append(pair.sim_embed)
            weights.append(beta)

        if not scores:
            pair.probability = 0.0
            continue

        total_weight = sum(weights)
        logit = sum(s * w / total_weight for s, w in zip(scores, weights))
        pair.probability = 1 / (1 + math.exp(-4 * (logit - 0.5)))

    return list(merged.values())


def partition_by_probability(
    candidates: List[DedupCandidatePair],
    theta_low: float = 0.70,
) -> Tuple[List[DedupCandidatePair], List[DedupCandidatePair]]:
    """两档分流：LLM确认 / 丢弃（去掉自动合并，全部走 LLM）"""
    llm_pool, discard_pool = [], []
    for pair in candidates:
        if pair.probability > theta_low:
            llm_pool.append(pair)
        else:
            discard_pool.append(pair)
    return llm_pool, discard_pool