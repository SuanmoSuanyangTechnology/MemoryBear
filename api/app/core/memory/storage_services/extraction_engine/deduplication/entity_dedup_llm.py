"""
用于实体去重，基于LLM的决策
提供“LLM判定逻辑”的核心实现与并发控制。
"""

import asyncio
import difflib
import json
import logging
from typing import List, Tuple, Dict
import anyio

from app.core.memory.models.graph_models import ExtractedEntityNode, StatementEntityEdge, EntityEntityEdge
from app.core.memory.models.dedup_models import EntityDedupDecision, EntityDisambDecision
from app.core.memory.utils.prompt.prompt_utils import render_entity_dedup_prompt
from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
    _merge_attribute,
    _unify_entity_type
)

logger = logging.getLogger(__name__)


# --- 类型同义归并与相似度 ---
_TYPE_ALIASES_UPPER: Dict[str, set[str]] = {
    # 设备/器材类近义：统一到 EQUIPMENT
    "EQUIPMENT": {s.upper() for s in {"设备", "器材", "摄影器材", "装备", "工具", "APPLIANCE", "TOOL"}},
    # 活动/技能近义：统一到 ACTIVITY，放宽“技术活动/技能”的同类判断
    "ACTIVITY": {s.upper() for s in {"活动", "技术活动", "技能", "ACTIVITY", "SKILL"}},
    # 常见类别，按需扩展
    "PERSON": {s.upper() for s in {"生命体", "人物", "人", "个人", "人名", "PERSON"}},
    "LOCATION": {s.upper() for s in {"地点", "位置", "LOCATION", "城市", "CITY", "国家", "COUNTRY"}},
    "SOFTWARE": {s.upper() for s in {"软件", "SOFTWARE"}},
    "EVENT": {s.upper() for s in {"事件", "EVENT"}},
}

def _canonicalize_type(t: str | None) -> str:
    u = (str(t or "").strip().upper())
    if not u or u == "UNKNOWN":
        return "UNKNOWN"
    for canon, aliases in _TYPE_ALIASES_UPPER.items():
        if u in aliases:
            return canon
    return u  # 未知类型直接返回自身（保守兼容）

def _type_similarity(t1: str | None, t2: str | None) -> float:
    c1, c2 = _canonicalize_type(t1), _canonicalize_type(t2)
    if c1 == c2:
        return 1.0
    if c1 == "UNKNOWN" or c2 == "UNKNOWN":
        return 0.6  # 任一未知，给中等相似度，允许模型结合描述判断
    return 0.0

def _simple_type_ok(t1: str | None, t2: str | None) -> bool:
    """类型门控：
    - 允许同类（含近义归并后同类）或任一 UNKNOWN/空；
    - 其余不同类不放行（例如 PERSON vs EQUIPMENT）。
    """
    c1, c2 = _canonicalize_type(t1), _canonicalize_type(t2)
    if c1 == "UNKNOWN" or c2 == "UNKNOWN":
        return True
    return c1 == c2


def parse_llm_response_safe(response_text: str, response_model) -> EntityDedupDecision | EntityDisambDecision | None:
    """安全解析LLM响应，带错误处理。
    
    Args:
        response_text: LLM返回的JSON文本
        response_model: 期望的响应模型类（EntityDedupDecision或EntityDisambDecision）
    
    Returns:
        解析后的决策对象，如果解析失败则返回None
    """
    try:
        data = json.loads(response_text)
        
        # 使用Pydantic模型验证和解析
        return response_model(**data)
        
    except json.JSONDecodeError as e:
        logger.warning(f"LLM response JSON parsing failed: {e}")
        return None
    except Exception as e:
        logger.warning(f"LLM response parsing failed: {e}")
        return None


def _name_embed_sim(a: List[float] | None, b: List[float] | None) -> float: # 计算实体名称嵌入向量的余弦相似度
    a = a or []
    b = b or []
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = (sum(x * x for x in a)) ** 0.5
        nb = (sum(y * y for y in b)) ** 0.5
        if na > 0 and nb > 0:
            return dot / (na * nb)
    except Exception:
        pass
    return 0.0


def _name_text_sim(name1: str, name2: str) -> float: # 计算实体名称文本的字符串相似度
    name1 = (name1 or "").strip().lower()
    name2 = (name2 or "").strip().lower()
    if not name1 or not name2:
        return 0.0
    return difflib.SequenceMatcher(None, name1, name2).ratio()


def _co_occurrence(statement_edges: List[StatementEntityEdge], a_id: str, b_id: str) -> bool:  # 判断两个实体是否在同一陈述中 “同现”
    try:
        sources_a = {e.source for e in statement_edges if getattr(e, "target", None) == a_id}
        sources_b = {e.source for e in statement_edges if getattr(e, "target", None) == b_id}
        return bool(sources_a & sources_b)
    except Exception:
        return False


def _relation_statements(entity_edges: List[EntityEntityEdge], a_id: str, b_id: str) -> List[str]: # 提取两个实体间的所有关联语句
    stmts: List[str] = []
    for e in entity_edges:
        if (getattr(e, "source", None) == a_id and getattr(e, "target", None) == b_id) or (
            getattr(e, "source", None) == b_id and getattr(e, "target", None) == a_id
        ):
            s_text = getattr(e, "statement", None) or ""
            r_type = getattr(e, "relation_type", None) or ""
            if s_text or r_type:
                stmts.append(f"{r_type}: {s_text}".strip(': '))
    return stmts


def _choose_canonical(a: ExtractedEntityNode, b: ExtractedEntityNode) -> int: # 选择 “规范实体”（合并时保留的实体）
    # 0 for a, 1 for b
     # 1. 第一优先级：按“连接强度”排序（连接强度越高，实体越可靠）
    cs_a = (getattr(a, "connect_strength", "") or "").lower()
    cs_b = (getattr(b, "connect_strength", "") or "").lower()
    prio = {"strong": 3, "both": 3, "weak": 1, "": 0}
    if prio.get(cs_a, 0) != prio.get(cs_b, 0):
        return 0 if prio.get(cs_a, 0) > prio.get(cs_b, 0) else 1
    # pick longer description/fact_summary
     # 2. 第二优先级：按“描述+事实摘要”的总长度排序（内容越长，信息越完整）
    desc_a = (getattr(a, "description", "") or "")
    desc_b = (getattr(b, "description", "") or "")
    # TODO: fact_summary 功能暂时禁用，待后续开发完善后启用
    # fact_a = (getattr(a, "fact_summary", "") or "")
    # fact_b = (getattr(b, "fact_summary", "") or "")
    # score_a = len(desc_a) + len(fact_a)
    # score_b = len(desc_b) + len(fact_b)
    score_a = len(desc_a)
    score_b = len(desc_b)
    if score_a != score_b:
        return 0 if score_a >= score_b else 1
    return 0

# _judge_pair（单对实体的 LLM 判断） 已经有分块迭代的函数内容是否还需要单对LLM判断--这是已经创建的工具服务于分块迭代的函数
async def _judge_pair(
    llm_client,
    a: ExtractedEntityNode,
    b: ExtractedEntityNode,
    statement_edges: List[StatementEntityEdge],
    entity_edges: List[EntityEntityEdge],
) -> Tuple[EntityDedupDecision, Dict]:
# 1. 计算实体名称的核心相似度指标
    name_text_sim = _name_text_sim(getattr(a, "name", ""), getattr(b, "name", ""))
    name_embed_sim = _name_embed_sim(getattr(a, "name_embedding", []), getattr(b, "name_embedding", []))
# 2. 判断名称是否存在“包含关系”（如“苹果公司”包含“苹果”）
    name_contains = False
    try:
        n1 = (getattr(a, "name", "") or "").strip().lower()
        n2 = (getattr(b, "name", "") or "").strip().lower()
        name_contains = bool(n1 and n2 and (n1 in n2 or n2 in n1))
    except Exception:
        pass
# 3. 构建LLM判断的“上下文信息”（规则层计算的所有特征）  判断上下文特征有助于实体消歧首先判断的类型关系
    ctx = {
        "same_group": getattr(a, "end_user_id", None) == getattr(b, "end_user_id", None),
        "type_ok": _simple_type_ok(getattr(a, "entity_type", None), getattr(b, "entity_type", None)),
        "type_similarity": _type_similarity(getattr(a, "entity_type", None), getattr(b, "entity_type", None)),
        "name_text_sim": name_text_sim,
        "name_embed_sim": name_embed_sim,
        "name_contains": name_contains,
        "co_occurrence": _co_occurrence(statement_edges, getattr(a, "id", None), getattr(b, "id", None)),
        "relation_statements": _relation_statements(entity_edges, getattr(a, "id", None), getattr(b, "id", None)),
    }

    entity_a = {
        "name": getattr(a, "name", None),
        "entity_type": getattr(a, "entity_type", None),
        "description": getattr(a, "description", None),
        "aliases": getattr(a, "aliases", None) or [],
        # TODO: fact_summary 功能暂时禁用，待后续开发完善后启用
        # "fact_summary": getattr(a, "fact_summary", None),
        "connect_strength": getattr(a, "connect_strength", None),
    }
    entity_b = {
        "name": getattr(b, "name", None),
        "entity_type": getattr(b, "entity_type", None),
        "description": getattr(b, "description", None),
        "aliases": getattr(b, "aliases", None) or [],
        # TODO: fact_summary 功能暂时禁用，待后续开发完善后启用
        # "fact_summary": getattr(b, "fact_summary", None),
        "connect_strength": getattr(b, "connect_strength", None),
    }
 # 5. 渲染LLM提示词（用工具函数填充模板，包含实体信息、上下文、输出格式）
    prompt = render_entity_dedup_prompt(
        entity_a=entity_a,
        entity_b=entity_b,
        context=ctx,
        json_schema=EntityDedupDecision.model_json_schema(),
        disambiguation_mode=False,  # 去重模式
    )

    messages = [
        {"role": "system", "content": "You judge whether two entities are the same. Return valid JSON only."},
        {"role": "user", "content": prompt},
    ]

    decision = await llm_client.call_structured(messages, EntityDedupDecision)
    return decision, ctx

# 消歧场景（同名不同类型）下的LLM判断
async def _judge_pair_disamb(
    llm_client,
    a: ExtractedEntityNode,
    b: ExtractedEntityNode,
    statement_edges: List[StatementEntityEdge],
    entity_edges: List[EntityEntityEdge],
) -> Tuple[EntityDisambDecision, Dict]:
    name_text_sim = _name_text_sim(getattr(a, "name", ""), getattr(b, "name", ""))
    name_embed_sim = _name_embed_sim(getattr(a, "name_embedding", []), getattr(b, "name_embedding", []))
    name_contains = False
    try:
        n1 = (getattr(a, "name", "") or "").strip().lower()
        n2 = (getattr(b, "name", "") or "").strip().lower()
        name_contains = bool(n1 and n2 and (n1 in n2 or n2 in n1))
    except Exception:
        pass
    ctx = {
        "same_group": getattr(a, "end_user_id", None) == getattr(b, "end_user_id", None),
        "type_ok": _simple_type_ok(getattr(a, "entity_type", None), getattr(b, "entity_type", None)),
        "name_text_sim": name_text_sim,
        "name_embed_sim": name_embed_sim,
        "name_contains": name_contains,
        "co_occurrence": _co_occurrence(statement_edges, getattr(a, "id", None), getattr(b, "id", None)),
        "relation_statements": _relation_statements(entity_edges, getattr(a, "id", None), getattr(b, "id", None)),
    }
    entity_a = {
        "name": getattr(a, "name", None),
        "entity_type": getattr(a, "entity_type", None),
        "description": getattr(a, "description", None),
        "aliases": getattr(a, "aliases", None) or [],
        # TODO: fact_summary 功能暂时禁用，待后续开发完善后启用
        # "fact_summary": getattr(a, "fact_summary", None),
        "connect_strength": getattr(a, "connect_strength", None),
    }
    entity_b = {
        "name": getattr(b, "name", None),
        "entity_type": getattr(b, "entity_type", None),
        "description": getattr(b, "description", None),
        "aliases": getattr(b, "aliases", None) or [],
        # TODO: fact_summary 功能暂时禁用，待后续开发完善后启用
        # "fact_summary": getattr(b, "fact_summary", None),
        "connect_strength": getattr(b, "connect_strength", None),
    }
    prompt = render_entity_dedup_prompt(
        entity_a=entity_a,
        entity_b=entity_b,
        context=ctx,
        json_schema=EntityDisambDecision.model_json_schema(),
        disambiguation_mode=True,
    )
    messages = [
        {"role": "system", "content": "You disambiguate same-name different-type entities. Return valid JSON only."},
        {"role": "user", "content": prompt},
    ]
    decision = await llm_client.call_structured(messages, EntityDisambDecision)
    return decision, ctx

# llm_dedup_entities（单轮实体去重）
async def llm_dedup_entities(  # 保留对偶判断作为子流程，是为了保证高精度、可审计、可复用和行为一致性
                               # 对偶判断让每次决策只聚焦于一对实体，信息维度清晰，噪声更低，模型更容易给出稳定的“是否同一实体”与“规范方”选择。
                               # 考虑是否将其保留
    entity_nodes: List[ExtractedEntityNode],
    statement_entity_edges: List[StatementEntityEdge],
    entity_entity_edges: List[EntityEntityEdge],
    llm_client,
    max_concurrency: int = 4,
    auto_merge_threshold: float = 0.90,
    co_ctx_threshold: float = 0.83,
) -> Tuple[Dict[str, str], List[str]]:
    """
    Use LLM to assist fuzzy deduplication among candidate entity pairs and
    produce an `id_redirect` mapping plus audit log records.

    Parameters:
    - entity_nodes: deduplication input entities
    - statement_entity_edges: edges from statements to entities (for co-occurrence context)
    - entity_entity_edges: relational edges between entities (for relation statements)
    - llm_client: configured async client used to call the model
    - max_concurrency: semaphore limit for concurrent LLM calls (default 4)
    - auto_merge_threshold: confidence threshold to auto-merge without co-occurrence (default 0.90)
    - co_ctx_threshold: slightly lower threshold when co-occurrence is detected (default 0.83)

    Returns:
    - id_redirect_updates: dict of losing_id -> canonical_id decided by LLM
    - records: textual logs for decisions, errors, and non-merges

    Notes:
    - Candidate generation uses simple gates: same group, type compatible, and
      name similarity or containment, optionally lowered threshold with co-occurrence.
    - The higher-level pipeline should call this async function upstream, then
      pass the resulting mapping and records into `deduplicate_entities_and_edges`
      via `llm_redirect` and `llm_records` to apply merges synchronously before
      edge redirection.
    """
    # 1. 构建“候选实体对”（用规则层筛选，减少LLM调用量，提高效率）
    # Build candidate pairs: simple gates
    candidates: List[Tuple[int, int]] = []
    for i in range(len(entity_nodes)):
        a = entity_nodes[i]
        for j in range(i + 1, len(entity_nodes)):
            b = entity_nodes[j]
            # 规则1：必须属于同一组（end_user_id相同，不同组的实体不重复）
            if getattr(a, "end_user_id", None) != getattr(b, "end_user_id", None):
                continue
            # 规则2：类型必须兼容（调用_simple_type_ok判断）
            if not _simple_type_ok(getattr(a, "entity_type", None), getattr(b, "entity_type", None)):
                continue
            
            # 规则2.5：过滤掉应该在模糊匹配阶段就被合并的实体对
            # 如果名称相同且别名有交集，说明应该在模糊匹配阶段就被合并了
            # 这些实体对不应该进入LLM阶段，避免重复处理
            try:
                name_a = (getattr(a, "name", "") or "").strip().lower()
                name_b = (getattr(b, "name", "") or "").strip().lower()
                same_name = (name_a == name_b) and name_a != ""
                
                if same_name:
                    # 检查别名是否有交集
                    names_a = {name_a}
                    names_a |= {(alias or "").strip().lower() for alias in (getattr(a, "aliases", []) or [])}
                    names_a.discard("")
                    
                    names_b = {name_b}
                    names_b |= {(alias or "").strip().lower() for alias in (getattr(b, "aliases", []) or [])}
                    names_b.discard("")
                    
                    has_alias_overlap = bool(names_a & names_b)
                    
                    # 如果名称相同且别名有交集，跳过（应该在模糊匹配阶段处理）
                    if has_alias_overlap:
                        continue
            except Exception:
                pass  # 如果检查失败，继续处理（保守策略）
            
            # 规则3：名称相似度达标（文本/嵌入相似度取最大值）
            txt_sim = _name_text_sim(getattr(a, "name", ""), getattr(b, "name", ""))
            emb_sim = _name_embed_sim(getattr(a, "name_embedding", []), getattr(b, "name_embedding", []))
            # 规则4：名称是否包含（如“苹果公司”和“苹果”）
            contains = False
            try:
                n1 = (getattr(a, "name", "") or "").strip().lower()
                n2 = (getattr(b, "name", "") or "").strip().lower()
                contains = bool(n1 and n2 and (n1 in n2 or n2 in n1))
            except Exception:
                pass
            # 规则5：是否同现（同现的实体更可能重复，降低相似度阈值）
            co_ctx = _co_occurrence(statement_entity_edges, getattr(a, "id", None), getattr(b, "id", None))
            sim = max(txt_sim, emb_sim)
            # 候选对筛选条件：满足任一即加入（减少漏判）
            if (sim >= 0.80) or (co_ctx and sim >= 0.75) or contains:
                candidates.append((i, j))

    # Use anyio for cross-compatibility with asyncio and trio
    results = []
    async with anyio.create_task_group() as tg:
        result_list = [None] * len(candidates)

        async def _wrapped(idx: int, i: int, j: int):
            try:
                result_list[idx] = await _judge_pair(llm_client, entity_nodes[i], entity_nodes[j], statement_entity_edges, entity_entity_edges)
            except Exception as e:
                logger.error(f"Error judging pair ({i}, {j}): {e}", exc_info=True)
                result_list[idx] = e

        # Limit concurrency using semaphore
        sem = anyio.Semaphore(max_concurrency)

        async def _limited_wrapped(idx: int, i: int, j: int):
            async with sem:
                await _wrapped(idx, i, j)

        for idx, (i, j) in enumerate(candidates):
            tg.start_soon(_limited_wrapped, idx, i, j)

    results = result_list

    id_redirect_updates: Dict[str, str] = {}
    records: List[str] = []
    for idx, res in enumerate(results):
        if isinstance(res, Exception):
            i, j = candidates[idx]
            a = entity_nodes[i]
            b = entity_nodes[j]
            records.append(f"[LLM异常] pair ({a.id},{b.id}) -> {res}")
            continue
        decision, ctx = res
        i, j = candidates[idx]
        a = entity_nodes[i]
        b = entity_nodes[j]
        th = auto_merge_threshold if not ctx.get("co_occurrence") else co_ctx_threshold
        if decision.same_entity and decision.confidence >= th:
            canon_idx = decision.canonical_idx if decision.canonical_idx in (0, 1) else _choose_canonical(a, b)
            canon = a if canon_idx == 0 else b
            other = b if canon_idx == 0 else a
            
            # 应用LLM合并决策：合并属性和统一类型
            _merge_attribute(canon, other)
            _unify_entity_type(canon, other, suggested_type=None)
            
            id_redirect_updates[other.id] = canon.id
            records.append(
                f"[LLM合并] 规范实体 {canon.id} 名称 '{getattr(canon, 'name', '')}' <- 合并实体 {other.id} 名称 '{getattr(other, 'name', '')}' | conf={decision.confidence:.3f}, th={th:.3f}, co_ctx={ctx.get('co_occurrence')}"
            )
            # 若类型相同且名称高度相似/包含关系，补充“同类名称相似”记录，格式与报告要求一致（名称后带类型）
            try:
                type_same = (getattr(a, "entity_type", None) == getattr(b, "entity_type", None)) and getattr(a, "entity_type", None) is not None
                name_sim = max(float(ctx.get("name_text_sim", 0.0)), float(ctx.get("name_embed_sim", 0.0)))
                name_contains = bool(ctx.get("name_contains", False))
                if type_same and (name_sim >= 0.80 or name_contains):
                    name_a = (getattr(a, "name", "") or "").strip()
                    name_b = (getattr(b, "name", "") or "").strip()
                    type_a = getattr(a, "entity_type", "")
                    type_b = getattr(b, "entity_type", "")
                    records.append(
                        f"[LLM去重] 同类名称相似 {name_a}（{type_a}）|{name_b}（{type_b}） | conf={decision.confidence:.2f} | reason={decision.reason}"
                    )
            except Exception:
                pass
        else:
            records.append(
                f"[LLM不合并] A={a.id} B={b.id} | same={decision.same_entity} conf={decision.confidence:.3f} co_ctx={ctx.get('co_occurrence')}"
            )

    return id_redirect_updates, records


# LLM 消歧：同名不同类型的实体对，输出合并建议与阻断对列表
# 写入阶段已经没有消息，这部分内容可以准备删除，包含llm_client
async def llm_disambiguate_pairs_iterative(
    entity_nodes: List[ExtractedEntityNode],
    statement_entity_edges: List[StatementEntityEdge],
    entity_entity_edges: List[EntityEntityEdge],
    llm_client,
    max_concurrency: int = 4,
    merge_conf_threshold: float = 0.88,
    block_conf_threshold: float = 0.60,
) -> Tuple[Dict[str, str], List[Tuple[str, str]], List[str]]:
    """
    Disambiguate same-name different-type pairs using LLM.

    Returns:
    - merge_redirect: dict losing_id -> canonical_id for merges decided by LLM
    - block_pairs: list of sorted (id1, id2) pairs to block from fuzzy/heuristic merges
    - records: textual logs for audit
    """
    records: List[str] = []
    merge_redirect: Dict[str, str] = {}
    block_pairs: List[Tuple[str, str]] = []

    def _is_typed(t: str) -> bool:
        t = (t or "").strip().upper()
        return bool(t) and t not in {"UNKNOWN", "UNDEFINED", ""}

    candidates: List[Tuple[int, int]] = []
    n = len(entity_nodes)
    for i in range(n):
        for j in range(i + 1, n):
            a = entity_nodes[i]
            b = entity_nodes[j]
            # 必须同组
            if getattr(a, "end_user_id", None) != getattr(b, "end_user_id", None):
                continue
            ta = getattr(a, "entity_type", None)
            tb = getattr(b, "entity_type", None)
            # 必须不同类型且两者均为已定义类型
            if ta == tb:
                continue
            if not (_is_typed(ta) and _is_typed(tb)):
                continue
            # 严格“同名不同义”：名称需严格相同（大小写与首尾空格忽略）
            try:
                na = (getattr(a, "name", "") or "").strip().lower()
                nb = (getattr(b, "name", "") or "").strip().lower()
            except Exception:
                na, nb = "", ""
            if not na or not nb:
                continue
            if na == nb:
                candidates.append((i, j))

    if not candidates:
        return merge_redirect, block_pairs, records

    # Use anyio for cross-compatibility with asyncio and trio
    judged = [None] * len(candidates)
    async with anyio.create_task_group() as tg:
        async def _wrapped(idx: int, i: int, j: int):
            try:
                judged[idx] = await _judge_pair_disamb(llm_client, entity_nodes[i], entity_nodes[j], statement_entity_edges, entity_entity_edges)
            except Exception as e:
                logger.error(f"Error in disamb pair ({i}, {j}): {e}", exc_info=True)
                judged[idx] = e

        # Limit concurrency using semaphore
        sem = anyio.Semaphore(max_concurrency)

        async def _limited_wrapped(idx: int, i: int, j: int):
            async with sem:
                await _wrapped(idx, i, j)

        for idx, (i, j) in enumerate(candidates):
            tg.start_soon(_limited_wrapped, idx, i, j)
    for k, res in enumerate(judged):
        i, j = candidates[k]
        a = entity_nodes[i]
        b = entity_nodes[j]
        a_id = getattr(a, "id", None) or ""
        b_id = getattr(b, "id", None) or ""
        if isinstance(res, Exception):
            records.append(f"[DISAMB错误] 对({a_id},{b_id})调用失败: {res}")
            block_pairs.append(tuple(sorted((a_id, b_id))))
            continue
        decision, ctx = res
        try:
            if decision.should_merge and decision.confidence >= merge_conf_threshold:
                can_idx = 0 if decision.canonical_idx == 0 else 1
                canonical = a if can_idx == 0 else b
                losing = b if can_idx == 0 else a
                
                # 应用LLM合并决策：合并属性和统一类型
                _merge_attribute(canonical, losing)
                _unify_entity_type(canonical, losing, suggested_type=decision.suggested_type)
                
                merge_redirect[getattr(losing, "id", "")] = getattr(canonical, "id", "")
                records.append(
                    f"[DISAMB合并] {getattr(losing,'id','')} -> {getattr(canonical,'id','')} | conf={decision.confidence:.2f} | reason={decision.reason} | suggested_type={decision.suggested_type or ''}"
                )
                # 追加 LLM 决策去重记录，以便下方报告展示到“LLM 决策去重”区块
                records.append(
                    f"[LLM去重] 同名类型相似 {getattr(a,'name','')}（{getattr(a,'entity_type','')}）|{getattr(b,'name','')}（{getattr(b,'entity_type','')}） | conf={decision.confidence:.2f} | reason={decision.reason}"
                )
            else:
                # Fallback：同名且类型不同，但语义高度相似且未要求阻断，按“同名类型相似”进行合并
                name_a = (getattr(a, "name", "") or "").strip().lower()
                name_b = (getattr(b, "name", "") or "").strip().lower()
                def _strength_rank(x: str) -> int:
                    s = (x or "").strip().lower()
                    return {"strong": 3, "both": 2, "weak": 1}.get(s, 0)
                if (
                    name_a and name_b and name_a == name_b
                    and (not decision.block_pair)
                    and decision.confidence >= max(0.80, block_conf_threshold)
                ):
                    # 选择规范实体：优先使用 canonical_idx；否则根据连接强度挑选更强者
                    if decision.canonical_idx in (0, 1):
                        canonical = a if decision.canonical_idx == 0 else b
                        losing = b if decision.canonical_idx == 0 else a
                    else:
                        sa = _strength_rank(getattr(a, "connect_strength", None))
                        sb = _strength_rank(getattr(b, "connect_strength", None))
                        canonical = a if sa >= sb else b
                        losing = b if sa >= sb else a
                    
                    # 应用LLM合并决策：合并属性和统一类型
                    _merge_attribute(canonical, losing)
                    _unify_entity_type(canonical, losing, suggested_type=decision.suggested_type)
                    
                    merge_redirect[getattr(losing, "id", "")] = getattr(canonical, "id", "")
                    # 消歧合并审计
                    records.append(
                        f"[DISAMB合并] {getattr(losing,'id','')} -> {getattr(canonical,'id','')} | conf={decision.confidence:.2f} | reason={decision.reason} | suggested_type={decision.suggested_type or ''}"
                    )
                    # 追加 LLM 决策去重记录（同名类型相似）
                    records.append(
                        f"[LLM去重] 同名类型相似 {getattr(a,'name','')}（{getattr(a,'entity_type','')}）|{getattr(b,'name','')}（{getattr(b,'entity_type','')}） | conf={decision.confidence:.2f} | reason={decision.reason}"
                    )
                else:
                    if decision.block_pair or decision.confidence >= block_conf_threshold:
                        block_pairs.append(tuple(sorted((a_id, b_id))))
                    # 仅保留阻断条目在预筛选报告，包含实体名称与类型，便于人读
                    records.append(
                        f"[DISAMB阻断] {getattr(a,'name','')}（{getattr(a,'entity_type','')}）|{getattr(b,'name','')}（{getattr(b,'entity_type','')}） | conf={decision.confidence:.2f} | reason={decision.reason} || block_pair={decision.block_pair}"
                    )
        except Exception:
            block_pairs.append(tuple(sorted((a_id, b_id))))
            # 异常情况也以阻断形式记录，包含名称便于定位
            records.append(
                f"[DISAMB异常阻断] {getattr(a,'name','')}（{getattr(a,'entity_type','')}）|{getattr(b,'name','')}（{getattr(b,'entity_type','')}） | ids=({a_id},{b_id})"
            )

    return merge_redirect, block_pairs, records
