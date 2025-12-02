"""
去重功能函数
"""
from app.core.memory.models.variate_config import DedupConfig
from typing import List, Dict, Tuple
from app.core.memory.models.graph_models import(
    StatementEntityEdge,
    EntityEntityEdge,
    ExtractedEntityNode
)
import os
from datetime import datetime
import difflib # 提供字符串相似度计算工具
import asyncio
import importlib
import re
# 模块级属性融合工具函数（统一行为）
def _merge_attribute(canonical: ExtractedEntityNode, ent: ExtractedEntityNode):
    # 强弱连接合并
    can_strength = (getattr(canonical, "connect_strength", "") or "").lower()
    inc_strength = (getattr(ent, "connect_strength", "") or "").lower()
    pair = {can_strength, inc_strength} - {""}
    if pair:
        if "both" in pair or pair == {"strong", "weak"}:
            canonical.connect_strength = "both"
        elif pair == {"strong"}:
            canonical.connect_strength = "strong"
        elif pair == {"weak"}:
            canonical.connect_strength = "weak"
        else:
            canonical.connect_strength = next(iter(pair))

    # 别名合并（去重保序）
    try:
        existing = getattr(canonical, "aliases", []) or []
        incoming = getattr(ent, "aliases", []) or []
        seen = set()
        merged_list: List[str] = []
        for x in existing + incoming:
            xn = (x or "").strip()
            if xn and xn not in seen:
                seen.add(xn)
                merged_list.append(x)
        canonical.aliases = merged_list
    except Exception:
        pass

    # 描述与事实摘要（保留更长者）
    try:
        desc_a = getattr(canonical, "description", "") or ""
        desc_b = getattr(ent, "description", "") or ""
        if len(desc_b) > len(desc_a):
            canonical.description = desc_b
        # 合并事实摘要：统一保留一个“实体: name”行，来源行去重保序
        fact_a = getattr(canonical, "fact_summary", "") or ""
        fact_b = getattr(ent, "fact_summary", "") or ""
        def _extract_sources(txt: str) -> List[str]:
            sources: List[str] = []
            if not txt:
                return sources
            for line in str(txt).splitlines():
                ln = line.strip()
                # 支持“来源:”或“来源：”前缀
                m = re.match(r"^来源[:：]\s*(.+)$", ln)
                if m:
                    content = m.group(1).strip()
                    if content:
                        sources.append(content)
            # 如果不存在“来源”前缀，则将整体文本视为一个来源片段，避免信息丢失
            if not sources and txt.strip():
                sources.append(txt.strip())
            return sources
        try:
            src_a = _extract_sources(fact_a)
            src_b = _extract_sources(fact_b)
            seen = set()
            merged_sources: List[str] = []
            for s in src_a + src_b:
                if s and s not in seen:
                    seen.add(s)
                    merged_sources.append(s)
            if merged_sources:
                name_line = f"实体: {getattr(canonical, 'name', '')}".strip()
                canonical.fact_summary = "\n".join([name_line] + [f"来源: {s}" for s in merged_sources])
            elif fact_b and not fact_a:
                canonical.fact_summary = fact_b
        except Exception:
            # 兜底：若解析失败，保留较长文本
            if len(fact_b) > len(fact_a):
                canonical.fact_summary = fact_b
    except Exception:
        pass

    # 名称向量补全
    try:
        emb_a = getattr(canonical, "name_embedding", []) or []
        emb_b = getattr(ent, "name_embedding", []) or []
        if not emb_a and emb_b:
            canonical.name_embedding = emb_b
    except Exception:
        pass

    # 时间范围合并
    try:
        # 统一使用 created_at / expired_at
        if getattr(ent, "created_at", None) and getattr(canonical, "created_at", None) and ent.created_at < canonical.created_at:
            canonical.created_at = ent.created_at
        if getattr(ent, "expired_at", None) and getattr(canonical, "expired_at", None):
            if canonical.expired_at is None:
                canonical.expired_at = ent.expired_at
            elif ent.expired_at and ent.expired_at > canonical.expired_at:
                canonical.expired_at = ent.expired_at
    except Exception:
        pass

def accurate_match(
    entity_nodes: List[ExtractedEntityNode]
) -> Tuple[List[ExtractedEntityNode], Dict[str, str], Dict[str, Dict]]:
    """
    精确匹配：按 (group_id, name, entity_type) 合并实体并建立重定向与合并记录。
    返回: (deduped_entities, id_redirect, exact_merge_map)
    """
    exact_merge_map: Dict[str, Dict] = {}
    canonical_map: Dict[str, ExtractedEntityNode] = {}
    id_redirect: Dict[str, str] = {}

    # 1) 构建规范实体映射（按名称+类型+group 精确匹配）
    for ent in entity_nodes:
        name_norm = (getattr(ent, "name", "") or "").strip()
        type_norm = (getattr(ent, "entity_type", "") or "").strip()
        key = f"{getattr(ent, 'group_id', None)}|{name_norm}|{type_norm}"
        # 为避免跨业务组误并，明确以 group_id 为范围边界
        if key not in canonical_map:
            canonical_map[key] = ent
            id_redirect[getattr(ent, "id")] = getattr(ent, "id")
            continue
        canonical = canonical_map[key]

        # 执行精确属性与强弱合并，并建立重定向
        _merge_attribute(canonical, ent)
        id_redirect[getattr(ent, "id")] = getattr(canonical, "id")
        # 记录精确匹配的合并项（使用规范化键，避免外层变量误用）
        try:
            k = f"{getattr(canonical, 'group_id')}|{(getattr(canonical, 'name') or '').strip()}|{(getattr(canonical, 'entity_type') or '').strip()}"
            if k not in exact_merge_map:
                exact_merge_map[k] = {
                    "canonical_id": getattr(canonical, "id"),
                    "group_id": getattr(canonical, "group_id"),
                    "name": getattr(canonical, "name"),
                    "entity_type": getattr(canonical, "entity_type"),
                    "merged_ids": set(),
                }
            exact_merge_map[k]["merged_ids"].add(getattr(ent, "id"))
        except Exception:
            pass

    deduped_entities = list(canonical_map.values())
    return deduped_entities, id_redirect, exact_merge_map

def fuzzy_match(
    deduped_entities: List[ExtractedEntityNode],
    statement_entity_edges: List[StatementEntityEdge],
    id_redirect: Dict[str, str],
    config: DedupConfig | None = None,
) -> Tuple[List[ExtractedEntityNode], Dict[str, str], List[str]]:
    """
    模糊匹配：在精确匹配之后，基于名称/类型相似度与上下文共现，进一步融合高相似实体。
    返回: (updated_entities, updated_redirect, fuzzy_merge_records)
    """
    fuzzy_merge_records: List[str] = []

    def _normalize_text(s: str) -> str:
        try:
            return re.sub(r"\s+", " ", re.sub(r"[^\w\u4e00-\u9fff]+", " ", (s or "").lower())).strip()
        except Exception:
            return str(s).lower().strip()

    def _tokenize(s: str) -> List[str]:
        norm = _normalize_text(s)
        tokens = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", norm)
        return tokens

    def _jaccard(a_tokens: List[str], b_tokens: List[str]) -> float:
        try:
            set_a, set_b = set(a_tokens), set(b_tokens)
            if not set_a and not set_b:
                return 0.0
            inter = len(set_a & set_b)
            union = len(set_a | set_b)
            return inter / union if union > 0 else 0.0
        except Exception:
            return 0.0

    def _cosine(a: List[float], b: List[float]) -> float:
        try:
            if not a or not b or len(a) != len(b):
                return 0.0
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(y * y for y in b) ** 0.5
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)
        except Exception:
            return 0.0

    def _name_similarity(e1: ExtractedEntityNode, e2: ExtractedEntityNode):
        emb_sim = _cosine(getattr(e1, "name_embedding", []) or [], getattr(e2, "name_embedding", []) or [])
        tokens1 = set(_tokenize(getattr(e1, "name", "") or ""))
        tokens2 = set(_tokenize(getattr(e2, "name", "") or ""))
        aliases1 = getattr(e1, "aliases", []) or []
        aliases2 = getattr(e2, "aliases", []) or []
        alias_tokens1 = set(tokens1)
        alias_tokens2 = set(tokens2)
        for a in aliases1:
            alias_tokens1 |= set(_tokenize(a))
        for a in aliases2:
            alias_tokens2 |= set(_tokenize(a))
        j_primary = _jaccard(list(tokens1), list(tokens2))
        j_alias = _jaccard(list(alias_tokens1), list(alias_tokens2))
        s_name = 0.6 * emb_sim + 0.2 * j_primary + 0.2 * j_alias
        return s_name, emb_sim, j_primary, j_alias

    def _desc_similarity(e1: ExtractedEntityNode, e2: ExtractedEntityNode):
        """
        计算实体描述的相似度（Jaccard + SequenceMatcher）
        返回: (相似度得分, Jaccard 相似度(词重合), SequenceMatcher 相似度（序列相似）)
        """
        d1 = getattr(e1, "description", "") or ""
        d2 = getattr(e2, "description", "") or ""
        if not d1 and not d2:
            return 0.0, 0.0, 0.0
        t1 = _tokenize(d1)
        t2 = _tokenize(d2)
        j = _jaccard(t1, t2)
        try:
            seq = difflib.SequenceMatcher(None, _normalize_text(d1), _normalize_text(d2)).ratio()
        except Exception:
            seq = 0.0
        # 平衡词重合与序列相似（更鲁棒）
        s_desc = 0.5 * j + 0.5 * seq
        return s_desc, j, seq

    def _canonicalize_type(t: str) -> str: # 扩展类型同义归一
        t = (t or "").strip()
        if not t:
            return ""
        t_up = t.upper()
        TYPE_ALIASES = {
            "PERSON": {"人物", "人", "个人", "人名", "PERSON", "PEOPLE", "INDIVIDUAL"},
            "ORG": {"组织", "ORG"},
            "COMPANY": {"公司", "企业", "COMPANY"},
            "INSTITUTION": {"机构", "INSTITUTION"},
            "LOCATION": {"地点", "位置", "LOCATION"},
            "CITY": {"城市", "CITY"},
            "COUNTRY": {"国家", "COUNTRY"},
            "EVENT": {"事件", "EVENT"},
            # 扩展活动与技能近义，统一到 ACTIVITY，便于本地模糊匹配
            "ACTIVITY": {"活动", "技术活动", "技能", "ACTIVITY", "SKILL"},
            "PRODUCT": {"产品", "商品", "物品", "OBJECT", "PRODUCT"},
            "TOOL": {"工具", "TOOL"},
            "SOFTWARE": {"软件", "SOFTWARE"},
            "FOOD": {"食品", "食物", "FOOD"},
            "INGREDIENT": {"食材", "配料", "原料", "INGREDIENT"},
            "SWEETMEATS": {"甜点", "甜品", "甜食", "SWEETMEATS"},
            # 统一本地与 LLM 阶段：将 EQUIPMENT/装备 映射为 APPLIANCE
            "APPLIANCE": {"设备", "器材", "摄影器材", "摄影设备", "电器", "烤箱", "装备","镜头", "EQUIPMENT", "APPLIANCE"},
            "ART": {"艺术", "艺术形式", "ART"},
            "FLOWER": {"花卉", "鲜花", "FLOWER"},
            "PLANT": {"植物", "PLANT"},
            "AGENT": {"AI助手", "助手", "人工智能助手", "智能助手", "智能体", "Agent", "AGENTA"},
            "ROLE": {"角色", "ROLE"},
            "SCENE_ELEMENT": {"场景元素", "SCENE_ELEMENT"},
            "UNKNOWN": {"UNKNOWN", "未知", "不明"},
        }
        for canon, aliases in TYPE_ALIASES.items():
            if t_up in {a.upper() for a in aliases}:
                return canon
        return t_up

    def _type_similarity(t1: str, t2: str) -> float:
        import difflib
        c1 = _canonicalize_type(t1)
        c2 = _canonicalize_type(t2)
        if not c1 or not c2:
            return 0.0
        if c1 == c2:
            return 0.5 if c1 == "UNKNOWN" else 1.0
        if c1 == "UNKNOWN" or c2 == "UNKNOWN":
            return 0.5
        sim_table = {
            ("ORG", "COMPANY"): 0.9, ("COMPANY", "ORG"): 0.9,
            ("ORG", "INSTITUTION"): 0.85, ("INSTITUTION", "ORG"): 0.85,
            ("LOCATION", "CITY"): 0.9, ("CITY", "LOCATION"): 0.9,
            ("LOCATION", "COUNTRY"): 0.9, ("COUNTRY", "LOCATION"): 0.9,
            ("EVENT", "ACTIVITY"): 0.8, ("ACTIVITY", "EVENT"): 0.8,
            ("PRODUCT", "TOOL"): 0.8, ("TOOL", "PRODUCT"): 0.8,
            ("PRODUCT", "SOFTWARE"): 0.8, ("SOFTWARE", "PRODUCT"): 0.8,
            ("FOOD", "SWEETMEATS"): 0.8, ("SWEETMEATS", "FOOD"): 0.8,
            ("INGREDIENT", "FOOD"): 0.85, ("FOOD", "INGREDIENT"): 0.85,
            ("APPLIANCE", "TOOL"): 0.8, ("TOOL", "APPLIANCE"): 0.8,
            ("APPLIANCE", "PRODUCT"): 0.7, ("PRODUCT", "APPLIANCE"): 0.7,
            ("FLOWER", "PLANT"): 0.9, ("PLANT", "FLOWER"): 0.9,
            ("AGENT", "SOFTWARE"): 0.85, ("SOFTWARE", "AGENT"): 0.85,
            ("AGENT", "PRODUCT"): 0.7, ("PRODUCT", "AGENT"): 0.7,
            ("AGENT", "ROLE"): 0.9, ("ROLE", "AGENT"): 0.9,
            ("SCENE_ELEMENT", "PRODUCT"): 0.6, ("PRODUCT", "SCENE_ELEMENT"): 0.6,
        }
        base = sim_table.get((c1, c2), 0.0)
        if base:
            return base
        t1n = (t1 or "").strip().lower()
        t2n = (t2 or "").strip().lower()
        seq_ratio = difflib.SequenceMatcher(None, t1n, t2n).ratio()
        return seq_ratio * 0.6
    # 阈值与权重设定（从配置读取；若无配置则使用 DedupConfig 的默认值）
    _defaults = DedupConfig()
    T_NAME_STRICT = (config.fuzzy_name_threshold_strict if config is not None else _defaults.fuzzy_name_threshold_strict)
    T_TYPE_STRICT = (config.fuzzy_type_threshold_strict if config is not None else _defaults.fuzzy_type_threshold_strict)
    T_OVERALL = (config.fuzzy_overall_threshold if config is not None else _defaults.fuzzy_overall_threshold)
    UNKNOWN_NAME_T = (config.fuzzy_unknown_type_name_threshold if config is not None else _defaults.fuzzy_unknown_type_name_threshold)
    UNKNOWN_TYPE_T = (config.fuzzy_unknown_type_type_threshold if config is not None else _defaults.fuzzy_unknown_type_type_threshold)
    W_NAME = (config.name_weight if config is not None else _defaults.name_weight)
    W_DESC = (config.desc_weight if config is not None else _defaults.desc_weight)
    W_TYPE = (config.type_weight if config is not None else _defaults.type_weight)
    CTX_BONUS = (config.context_bonus if config is not None else _defaults.context_bonus) # 上下文共现加分
    FALL_FLOOR = (config.llm_fallback_floor if config is not None else _defaults.llm_fallback_floor)
    FALL_CEIL = (config.llm_fallback_ceiling if config is not None else _defaults.llm_fallback_ceiling)


    i = 0
    while i < len(deduped_entities):
        a = deduped_entities[i]
        j = i + 1
        while j < len(deduped_entities):
            b = deduped_entities[j]
            if getattr(a, "group_id", None) != getattr(b, "group_id", None):
                j += 1
                continue
            # 上下文共现
            try:
                sources_a = {e.source for e in statement_entity_edges if getattr(e, "target", None) == getattr(a, "id", None)}
                sources_b = {e.source for e in statement_entity_edges if getattr(e, "target", None) == getattr(b, "id", None)}
                co_ctx = bool(sources_a & sources_b)
            except Exception:
                co_ctx = False
            s_name, emb_sim, j_primary, j_alias = _name_similarity(a, b)
            s_desc, j_desc, seq_desc = _desc_similarity(a, b)
            s_type = _type_similarity(getattr(a, "entity_type", None), getattr(b, "entity_type", None))
            unknown_present = (
                str(getattr(a, "entity_type", "")).upper() == "UNKNOWN"
                or str(getattr(b, "entity_type", "")).upper() == "UNKNOWN"
            )
            tn = UNKNOWN_NAME_T if unknown_present else T_NAME_STRICT
            tn = min(tn, 0.88) if co_ctx else tn
            type_threshold = UNKNOWN_TYPE_T if unknown_present else T_TYPE_STRICT
            tover = T_OVERALL
            a_cs = (getattr(a, "connect_strength", "") or "").lower()
            b_cs = (getattr(b, "connect_strength", "") or "").lower()
            if a_cs in ("strong", "both") or b_cs in ("strong", "both"):
                tover = 0.80
            # 综合评分：名称、描述、类型加权 + 上下文加分
            overall = W_NAME * s_name + W_DESC * s_desc + W_TYPE * s_type + (CTX_BONUS if co_ctx else 0.0)

            if s_name >= tn and s_type >= type_threshold and overall >= tover:
                _merge_attribute(a, b)
                try:
                    fuzzy_merge_records.append(
                        f"[模糊] 规范实体 {a.id} ({a.group_id}|{a.name}|{a.entity_type}) <- 合并实体 {b.id} ({b.group_id}|{b.name}|{b.entity_type}) | s_name={s_name:.3f}, s_desc={s_desc:.3f}, s_type={s_type:.3f}, overall={overall:.3f}, ctx={co_ctx}"
                    )
                except Exception:
                    pass
                # 用于处理合并实体后，Statement节点下方无挂载边的情况  后续考虑将其代码逻辑统一由关系去重消歧管理
                # 建立 ID 重定向：将合并实体 b 的 ID 指向规范实体 a 的 ID
                try:
                    canonical_id = id_redirect.get(getattr(a, "id", None), getattr(a, "id", None))
                    losing_id = getattr(b, "id", None)
                    if losing_id and canonical_id:
                        id_redirect[losing_id] = canonical_id
                        # 扁平化可能的重定向链：凡是映射到 b.id 的，统一指向 a.id
                        for k, v in list(id_redirect.items()):
                            if v == losing_id:
                                id_redirect[k] = canonical_id
                except Exception:
                    pass
                deduped_entities.pop(j)
                continue
            else:
                try:
                    if s_name >= tn and s_type >= type_threshold and (FALL_FLOOR <= overall < tover) and (overall <= FALL_CEIL):
                        fuzzy_merge_records.append(
                            f"[边界] {a.id}<->{b.id} ({a.group_id}|{a.name}|{a.entity_type} ~ {b.group_id}|{b.name}|{b.entity_type}) | s_name={s_name:.3f}, s_desc={s_desc:.3f}, s_type={s_type:.3f}, overall={overall:.3f}, ctx={co_ctx}"
                        )
                except Exception:
                    pass
                j += 1
        i += 1

    return deduped_entities, id_redirect, fuzzy_merge_records

async def LLM_decision(  # 决策中包含去重和消歧的功能
    deduped_entities: List[ExtractedEntityNode],
    statement_entity_edges: List[StatementEntityEdge],
    entity_entity_edges: List[EntityEntityEdge],
    id_redirect: Dict[str, str],
    config: DedupConfig | None = None,
) -> Tuple[List[ExtractedEntityNode], Dict[str, str], List[str]]:
    """
    基于迭代分块并发的 LLM 判定，生成实体重定向并在本地应用融合。
    返回 (updated_entities, updated_redirect, llm_records)。
    - 仅在配置 enable_llm_dedup_blockwise 为 True 时启用；
      若未提供配置，则使用 DedupConfig 的默认值作为回退。
    - 内部调用 llm_dedup_entities_iterative_blocks 获取 pairwise 的重定向映射。
    - 将映射应用到 deduped_entities 与 id_redirect，并记录融合日志。
    """
    llm_records: List[str] = []
    try:
        # 优先使用运行时配置；若未提供配置，使用模型默认值，不再回退到环境变量
        enable_switch = (
            bool(config.enable_llm_dedup_blockwise) if config is not None else DedupConfig().enable_llm_dedup_blockwise
        )
        if not enable_switch:
            return deduped_entities, id_redirect, llm_records
        # 从配置读取 LLM 迭代参数；若无配置则使用 DedupConfig 的默认值
        _defaults = DedupConfig()
        block_size = (config.llm_block_size if config is not None else _defaults.llm_block_size)
        block_concurrency = (config.llm_block_concurrency if config is not None else _defaults.llm_block_concurrency)
        pair_concurrency = (config.llm_pair_concurrency if config is not None else _defaults.llm_pair_concurrency)
        max_rounds = (config.llm_max_rounds if config is not None else _defaults.llm_max_rounds)

        # 动态导入 llm 客户端（统一从 app.core.memory.utils.llm_utils 获取）
        try:
            llm_utils_mod = importlib.import_module("app.core.memory.utils.llm_utils")
            get_llm_client_fn = getattr(llm_utils_mod, "get_llm_client")
        except Exception:
            get_llm_client_fn = lambda: None

        try:
            llm_mod = importlib.import_module("app.core.memory.storage_services.extraction_engine.deduplication.entity_dedup_llm")
            llm_fn = getattr(llm_mod, "llm_dedup_entities_iterative_blocks")
        except Exception:
            raise RuntimeError("LLM 模块加载失败：deduplication.entity_dedup_llm 缺少 llm_dedup_entities_iterative_blocks")

        # 获取 LLM 客户端，若环境未配置或抛错则回退为 None
        try:
            llm_client = get_llm_client_fn()
        except Exception:
            llm_client = None

        llm_redirect, llm_records = await llm_fn(
            entity_nodes=deduped_entities,
            statement_entity_edges=statement_entity_edges,
            entity_entity_edges=entity_entity_edges,
            llm_client=llm_client,
            block_size=block_size,
            block_concurrency=block_concurrency,
            pair_concurrency=pair_concurrency,
            max_rounds=max_rounds,
        )
    except Exception as e:
        # 记录错误，不中断主流程
        llm_records.append(f"[LLM错误] 迭代分块执行失败: {e}")
        return deduped_entities, id_redirect, llm_records

    # 若存在 LLM 的重定向，应用到实体与映射
    # 确保实体集合与 id_redirect 完整反映 LLM 的合并结果；否则后续边重定向不会指向规范 ID，实体仍然重复
    if llm_redirect:
        entity_by_id: Dict[str, ExtractedEntityNode] = {e.id: e for e in deduped_entities}
        for losing_id, canonical_id in list(llm_redirect.items()):
            if losing_id == canonical_id:
                continue
            a = entity_by_id.get(canonical_id)
            b = entity_by_id.get(losing_id)
            if not a or not b: # 若不存在 a 或 b，可能已在精确或模糊阶段合并，在之前阶段合并之后，不会再处理但是处于审计的目的会记录
                continue
            _merge_attribute(a, b)
            # ID 重定向
            try:
                id_redirect[b.id] = a.id
                for k, v in list(id_redirect.items()):
                    if v == b.id:
                        id_redirect[k] = a.id
            except Exception:
                pass
            # 记录 LLM 融合日志
            try:
                llm_records.append(
                    f"[LLM融合] 规范实体 {a.id} ({a.group_id}|{a.name}|{a.entity_type}) <- 合并实体 {b.id} ({b.group_id}|{b.name}|{b.entity_type})"
                )
                # 详细的“同类名称相似”记录改由 LLM 去重模块统一生成以携带 conf/reason
            except Exception:
                pass
            # 移除 losing 实体
            try:
                if b in deduped_entities:
                    deduped_entities.remove(b)
                    entity_by_id.pop(b.id, None)
            except Exception:
                pass

    return deduped_entities, id_redirect, llm_records

async def LLM_disamb_decision(
    deduped_entities: List[ExtractedEntityNode],
    statement_entity_edges: List[StatementEntityEdge],
    entity_entity_edges: List[EntityEntityEdge],
    id_redirect: Dict[str, str],
    config: DedupConfig | None = None,
) -> Tuple[List[ExtractedEntityNode], Dict[str, str], set[tuple[str, str]], List[str]]:
    """
    预消歧阶段：对“同名但类型不同”的实体对调用LLM进行消歧，
    产出：需阻断的实体对(blocked_pairs)与必要的合并(merge_redirect)。
    返回 (updated_entities, updated_redirect, blocked_pairs, disamb_records)。
    - 仅在配置开关 enable_llm_disambiguation 为 True 时启用；否则返回空阻断列表。
    """
    disamb_records: List[str] = []
    blocked_pairs: set[tuple[str, str]] = set()
    try:
        enable_switch = (
            config.enable_llm_disambiguation
            if config is not None
            else DedupConfig().enable_llm_disambiguation
        )
        if not bool(enable_switch):
            return deduped_entities, id_redirect, blocked_pairs, disamb_records

        from app.core.memory.utils.llm.llm_utils import get_llm_client
        from app.core.memory.storage_services.extraction_engine.deduplication.entity_dedup_llm import llm_disambiguate_pairs_iterative
        from app.core.memory.utils.config import definitions as config_defs
        llm_client = get_llm_client(config_defs.SELECTED_LLM_ID)
        merge_redirect, block_list, disamb_records = await llm_disambiguate_pairs_iterative(
                entity_nodes=deduped_entities,
                statement_entity_edges=statement_entity_edges,
                entity_entity_edges=entity_entity_edges,
                llm_client=llm_client,
            )

        # 应用LLM消歧的合并建议
        if merge_redirect:
            entity_by_id: Dict[str, ExtractedEntityNode] = {e.id: e for e in deduped_entities}
            for losing_id, canonical_id in list(merge_redirect.items()):
                if losing_id == canonical_id:
                    continue
                a = entity_by_id.get(canonical_id)
                b = entity_by_id.get(losing_id)
                if not a or not b:
                    continue
                _merge_attribute(a, b)
                id_redirect[b.id] = a.id
                for k, v in list(id_redirect.items()):
                    if v == b.id:
                        id_redirect[k] = a.id
                try:
                    disamb_records.append(
                        f"[DISAMB合并应用] 规范实体 {a.id} ({a.group_id}|{a.name}|{a.entity_type}) <- 合并实体 {b.id} ({b.group_id}|{b.name}|{b.entity_type})"
                    )
                except Exception:
                    pass
                try:
                    if b in deduped_entities:
                        deduped_entities.remove(b)
                        entity_by_id.pop(b.id, None)
                except Exception:
                    pass
        # 保存阻断对
        try:
            blocked_pairs = {tuple(sorted(p)) for p in (block_list or [])}
        except Exception:
            blocked_pairs = set()
    except Exception as e:
        disamb_records.append(f"[DISAMB错误] 消歧执行失败: {e}")
        return deduped_entities, id_redirect, blocked_pairs, disamb_records

    return deduped_entities, id_redirect, blocked_pairs, disamb_records

async def deduplicate_entities_and_edges(
    entity_nodes: List[ExtractedEntityNode],
    statement_entity_edges: List[StatementEntityEdge],
    entity_entity_edges: List[EntityEntityEdge],
    report_stage: str = "第一层去重消歧",
    report_append: bool = False,
    report_stage_notes: List[str] | None = None,
    dedup_config: DedupConfig | None = None,
) -> Tuple[List[ExtractedEntityNode], List[StatementEntityEdge], List[EntityEntityEdge]]:
    """
    主流程：依次执行精确匹配、模糊匹配与（可选）LLM 决策融合，随后对边做重定向与去重。之后再处理边，是关系去重和消歧
    返回：去重后的实体、语句→实体边、实体↔实体边。
    """
    local_llm_records: List[str] = [] # 作为“审计日志”的本地收集器 初始化，保留为了之后对于LLM决策追溯
    # 1) 精确匹配
    deduped_entities, id_redirect, exact_merge_map = accurate_match(entity_nodes)

    # 1.5) LLM 决策消歧：阻断同名不同类型的高相似对，并应用必要的合并
    deduped_entities, id_redirect, blocked_pairs, disamb_records = await LLM_disamb_decision(
        deduped_entities, statement_entity_edges, entity_entity_edges, id_redirect, config=dedup_config
    )

    # 2) 模糊匹配（本地规则）
    deduped_entities, id_redirect, fuzzy_merge_records = fuzzy_match(
        deduped_entities, statement_entity_edges, id_redirect, config=dedup_config
    )

    # 3) LLM 决策（仅按配置开关）
    try:
        enable_switch = (
            dedup_config.enable_llm_dedup_blockwise
            if dedup_config is not None
            else DedupConfig().enable_llm_dedup_blockwise
        )
        should_trigger_llm = bool(enable_switch)
        # 将触发信息写入阶段备注，便于输出报告审计
        if report_stage_notes is None:
            report_stage_notes = []
        report_stage_notes.append(f"LLM触发: {'是' if should_trigger_llm else '否'}")
    except Exception:
        should_trigger_llm = False

    if should_trigger_llm:
        deduped_entities, id_redirect, llm_decision_records = await LLM_decision(
            deduped_entities, statement_entity_edges, entity_entity_edges, id_redirect, config=dedup_config
        )
    else:
        llm_decision_records = []
    # 累加 LLM 记录  把 LLM_decision 返回的日志 llm_decision_records 追加到 local_llm_records
    try:
        local_llm_records.extend(llm_decision_records or [])
    except Exception:
        pass


# 在主流程这里 这里是之后关系去重和消歧的地方，方法可以写在其他地方
# 此处统一对边进行处理，使用累积的 id_redirect 把边的 source/target 改成规范ID
    # 4) 边重定向与去重
    # 4.1 语句→实体边：重复时优先保留 strong
    stmt_ent_map: Dict[str, StatementEntityEdge] = {}
    for edge in statement_entity_edges:
        new_target = id_redirect.get(edge.target, edge.target)
        edge.target = new_target
        key = f"{edge.source}_{edge.target}"
        if key not in stmt_ent_map:
            stmt_ent_map[key] = edge
        else:
            existing = stmt_ent_map[key]
            old_strength = getattr(existing, "connect_strength", "")
            new_strength = getattr(edge, "connect_strength", "")
            if old_strength != "strong" and new_strength == "strong":
                stmt_ent_map[key] = edge

    # 4.2 实体↔实体边：按 source_target 去重（无强弱属性）
    ent_ent_map: Dict[str, EntityEntityEdge] = {}
    for edge in entity_entity_edges:
        new_source = id_redirect.get(edge.source, edge.source)
        new_target = id_redirect.get(edge.target, edge.target)
        edge.source = new_source
        edge.target = new_target
        key = f"{edge.source}_{edge.target}"
        if key not in ent_ent_map:
            ent_ent_map[key] = edge


    _write_dedup_fusion_report(
        exact_merge_map=exact_merge_map,
        fuzzy_merge_records=fuzzy_merge_records,
        local_llm_records=local_llm_records,
        disamb_records=disamb_records,
        stage_label=report_stage,
        append=report_append,
        stage_notes=report_stage_notes,
    )

    return deduped_entities, list(stmt_ent_map.values()), list(ent_ent_map.values())

# 独立模块：去重融合报告写入（与实体/边的计算解耦）
def _write_dedup_fusion_report(
    exact_merge_map: Dict[str, Dict],
    fuzzy_merge_records: List[str],
    local_llm_records: List[str],
    disamb_records: List[str] | None = None,
    stage_label: str | None = None,
    append: bool = False,
    stage_notes: List[str] | None = None,
):
    try:
        # 使用全局配置的输出路径
        from app.core.config import settings
        settings.ensure_memory_output_dir()
        out_path = settings.get_memory_output_path("dedup_entity_output.txt")
        report_lines: List[str] = []
        if not append:
            report_lines.append(f"去重融合报告 - {datetime.now().isoformat()}")
            report_lines.append("")
        if stage_label:
            # 追加写入时，在阶段标题前增加一个空行以增强分隔
            if append:
                report_lines.append("")
            report_lines.append(f"=== {stage_label} ===")
            report_lines.append("")
        # 阶段注释：在标题下追加，如候选数、是否跳过等
        if stage_notes:
            for note in stage_notes:
                try:
                    report_lines.append(str(note))
                except Exception:
                    pass
            report_lines.append("")
        # 精确
        report_lines.append("精确匹配去重：")
        aggregated_exact_lines: List[str] = []
        try:
            for k, info in (exact_merge_map or {}).items():
                merged_ids = sorted(list(info.get("merged_ids", set())))
                if merged_ids:
                    aggregated_exact_lines.append(
                        f"[精确] 键 {k} 规范实体 {info.get('canonical_id')} 名称 '{info.get('name')}' 类型 {info.get('entity_type')} <- 合并实体IDs {', '.join(merged_ids)}"
                    )
        except Exception:
            pass
        report_lines.extend(aggregated_exact_lines if aggregated_exact_lines else ["无合并项"])
        report_lines.append("")
        # 消歧
        report_lines.append("LLM 决策消歧：")
        try:
            # 仅展示阻断项，过滤掉合并与合并应用
            disamb_block_only = [
                line for line in (disamb_records or [])
                if str(line).startswith("[DISAMB阻断]") or str(line).startswith("[DISAMB异常阻断]")
            ]
        except Exception:
            disamb_block_only = disamb_records or []
        report_lines.extend(disamb_block_only if disamb_block_only else ["未执行或无阻断/合并项"])
        report_lines.append("")
        # 模糊
        report_lines.append("模糊匹配去重：")
        report_lines.extend(fuzzy_merge_records if fuzzy_merge_records else ["未执行或无合并项"])
        report_lines.append("")
        # LLM
        report_lines.append("LLM 决策去重：")
        try:
            # 仅保留 LLM 的“去重判定”记录，排除“合并指令/融合落地”
            def _is_llm_dedup_record(s: str) -> bool:
                try:
                    text = str(s)
                    return "[LLM去重]" in text
                except Exception:
                    return False

            llm_dedup_only = [
                line for line in (local_llm_records or [])
                if _is_llm_dedup_record(str(line))
            ]
            # 同名类型相似的 LLM 去重记录可能来源于消歧阶段，将其也纳入展示
            try:
                llm_dedup_only.extend([
                    line for line in (disamb_records or [])
                    if _is_llm_dedup_record(str(line))
                ])
            except Exception:
                pass
        except Exception:
            llm_dedup_only = []
        # 输出前移除块前缀（如 "[LLM块0] "），并对重复记录去重（保序）
        try:
            import re as _re
            def _strip_block_prefix(s: str) -> str:
                try:
                    return _re.sub(r"^\[LLM块\d+\]\s*", "", str(s))
                except Exception:
                    return str(s)
            stripped = [ _strip_block_prefix(line) for line in (llm_dedup_only or []) ]
            seen = set()
            deduped_ordered = []
            for line in stripped:
                if line not in seen:
                    seen.add(line)
                    deduped_ordered.append(line)
            llm_dedup_only = deduped_ordered
        except Exception:
            pass
        report_lines.extend(llm_dedup_only if llm_dedup_only else ["未执行或无合并项"])
        with open(out_path, ("a" if append else "w"), encoding="utf-8") as f:
            f.write("\n".join(report_lines) + "\n")
    except Exception:
        # 静默失败，避免影响主流程
        pass
