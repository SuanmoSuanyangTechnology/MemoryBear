"""
去重功能函数
"""
import asyncio
import difflib  # 提供字符串相似度计算工具
import importlib
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from app.core.memory.models.graph_models import (
    EntityEntityEdge,
    ExtractedEntityNode,
    StatementEntityEdge,
)
from app.core.memory.models.variate_config import DedupConfig


# 模块级类型统一工具函数
def _unify_entity_type(canonical: ExtractedEntityNode, losing: ExtractedEntityNode, suggested_type: str = None) -> None:
    """统一实体类型：基于LLM建议或启发式规则选择最合适的类型。
    
    Args:
        canonical: 规范实体（保留的实体）
        losing: 被合并的实体
        suggested_type: LLM建议的统一类型（可选）
    """
    canonical_type = (getattr(canonical, "entity_type", "") or "").strip()
    losing_type = (getattr(losing, "entity_type", "") or "").strip()
    
    if suggested_type and suggested_type.strip():
        # 优先使用LLM建议的类型
        canonical.entity_type = suggested_type.strip()
    elif canonical_type.upper() == "UNKNOWN" and losing_type.upper() != "UNKNOWN":
        # 如果canonical是UNKNOWN，使用losing的类型
        canonical.entity_type = losing_type
    elif canonical_type.upper() != "UNKNOWN" and losing_type.upper() == "UNKNOWN":
        # 如果losing是UNKNOWN，保持canonical的类型（无需操作）
        pass
    elif canonical_type and losing_type and canonical_type != losing_type:
        # 两个类型都不是UNKNOWN且不同，选择更具体的类型
        # 启发式规则：
        # 1. 更长的类型名通常更具体（如 HistoricalPeriod vs Organization）
        # 2. 包含特定领域词汇的类型更具体（如 MilitaryCapability vs Concept）
        
        # 定义通用类型（优先级低）
        generic_types = {"Concept", "Phenomenon", "Condition", "State", "Attribute", "Event"}
        
        canonical_is_generic = canonical_type in generic_types
        losing_is_generic = losing_type in generic_types
        
        if canonical_is_generic and not losing_is_generic:
            # canonical是通用类型，losing是具体类型，使用losing
            canonical.entity_type = losing_type
        elif not canonical_is_generic and losing_is_generic:
            # losing是通用类型，canonical是具体类型，保持canonical（无需操作）
            pass
        elif len(losing_type) > len(canonical_type):
            # 两者都是具体类型或都是通用类型，选择更长的（通常更具体）
            canonical.entity_type = losing_type
        # 否则保持canonical的类型


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

    # 别名合并（去重保序，使用标准化工具）
    try:
        canonical_name = (getattr(canonical, "name", "") or "").strip()
        incoming_name = (getattr(ent, "name", "") or "").strip()
        
        # 收集所有需要合并的别名
        all_aliases = []
        
        # 1. 添加canonical现有的别名
        existing = getattr(canonical, "aliases", []) or []
        all_aliases.extend(existing)
        
        # 2. 添加incoming实体的名称（如果不同于canonical的名称）
        if incoming_name and incoming_name != canonical_name:
            all_aliases.append(incoming_name)
        
        # 3. 添加incoming实体的所有别名
        incoming = getattr(ent, "aliases", []) or []
        all_aliases.extend(incoming)
        
        # 4. 标准化并去重（优先使用alias_utils工具函数）
        try:
            from app.core.memory.utils.alias_utils import normalize_aliases
            canonical.aliases = normalize_aliases(canonical_name, all_aliases)
        except Exception:
            # 如果导入失败，使用增强的去重逻辑
            seen_normalized = set()
            unique_aliases = []
            
            for alias in all_aliases:
                if not alias:
                    continue
                
                alias_stripped = str(alias).strip()
                if not alias_stripped or alias_stripped == canonical_name:
                    continue
                
                # 标准化：转小写用于去重判断
                alias_normalized = alias_stripped.lower()
                
                if alias_normalized not in seen_normalized:
                    seen_normalized.add(alias_normalized)
                    unique_aliases.append(alias_stripped)
            
            # 排序并赋值
            canonical.aliases = sorted(unique_aliases)
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
            id_redirect[ent.id] = ent.id
            continue
        canonical = canonical_map[key]

        # 执行精确属性与强弱合并，并建立重定向
        _merge_attribute(canonical, ent)
        id_redirect[ent.id] = canonical.id
        # 记录精确匹配的合并项（使用规范化键，避免外层变量误用）
        try:
            k = f"{canonical.group_id}|{(canonical.name or '').strip()}|{(canonical.entity_type or '').strip()}"
            if k not in exact_merge_map:
                exact_merge_map[k] = {
                    "canonical_id": canonical.id,
                    "group_id": canonical.group_id,
                    "name": canonical.name,
                    "entity_type": canonical.entity_type,
                    "merged_ids": set(),
                }
            exact_merge_map[k]["merged_ids"].add(ent.id)
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
    模糊匹配：基于名称、别名、类型相似度进行实体去重合并。
    
    判断因素：
    - 名称相似度（包含别名匹配）：70%权重
    - 类型相似度：30%权重
    
    返回: (updated_entities, updated_redirect, fuzzy_merge_records)
    """
    fuzzy_merge_records: List[str] = []

    # ========== 第一层：基础工具函数 ==========
    
    def _normalize_text(s: str) -> str:
        """文本标准化：转小写、去除特殊字符、规范化空格"""
        try:
            return re.sub(r"\s+", " ", re.sub(r"[^\w\u4e00-\u9fff]+", " ", (s or "").lower())).strip()
        except Exception:
            return str(s).lower().strip()

    def _tokenize(s: str) -> List[str]:
        """分词：提取中文字符和英文数字单词"""
        norm = _normalize_text(s)
        tokens = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", norm)
        return tokens

    def _jaccard(a_tokens: List[str], b_tokens: List[str]) -> float:
        """Jaccard相似度：计算两个token集合的交集/并集"""
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
        """余弦相似度：计算两个向量的夹角余弦值"""
        try:
            if not a or not b or len(a) != len(b):
                return 0.0
            dot = sum(x * y for x, y in zip(a, b, strict=False))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(y * y for y in b) ** 0.5
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)
        except Exception:
            return 0.0

    # ========== 第二层：中层工具函数 ==========
    
    def _has_exact_alias_match(e1: ExtractedEntityNode, e2: ExtractedEntityNode) -> bool:
        """检测两个实体之间是否存在完全别名匹配（case-insensitive）
        
        检查以下情况：
        - e1的主名称与e2的某个别名完全匹配
        - e2的主名称与e1的某个别名完全匹配
        - e1和e2的别名列表有交集
        
        Args:
            e1: 第一个实体
            e2: 第二个实体
            
        Returns:
            bool: 存在完全匹配返回True
        """
        def _simple_normalize(s: str) -> str:
            return (s or "").strip().lower()
        
        # 获取e1的所有名称（主名称 + 别名）
        names1 = set()
        name1 = _simple_normalize(getattr(e1, "name", "") or "")
        if name1:
            names1.add(name1)
        
        aliases1 = getattr(e1, "aliases", []) or []
        for alias in aliases1:
            normalized = _simple_normalize(alias)
            if normalized:
                names1.add(normalized)
        
        # 获取e2的所有名称（主名称 + 别名）
        names2 = set()
        name2 = _simple_normalize(getattr(e2, "name", "") or "")
        if name2:
            names2.add(name2)
        
        aliases2 = getattr(e2, "aliases", []) or []
        for alias in aliases2:
            normalized = _simple_normalize(alias)
            if normalized:
                names2.add(normalized)
        
        # 检查是否有交集
        if names1 & names2:
            return True
        
        return False
    
    # ========== 第三层：高层综合函数 ==========
    
    def _name_similarity_with_aliases(e1: ExtractedEntityNode, e2: ExtractedEntityNode):
        """名称相似度综合评分系统
        
        综合考虑主名称和别名，计算两个实体的相似度。
        
        算法：
        1. 计算主名称的向量相似度和Token Jaccard相似度
        2. 计算所有别名的Token Jaccard相似度
        3. 找出所有名称间的最佳匹配
        4. 使用 _has_exact_alias_match 检测是否存在完全匹配
        
        评分权重：
        - 有完全匹配：embedding(40%) + primary_jaccard(20%) + max_alias_sim(40%)
        - 无完全匹配：embedding(60%) + primary_jaccard(20%) + max_alias_sim(20%)
        
        Args:
            e1: 第一个实体
            e2: 第二个实体
            
        Returns:
            tuple: (综合相似度, 向量相似度, 主名称Jaccard, 别名Jaccard, 
                   最佳别名匹配度, 是否完全匹配)
        """
        # 1. 主名称向量相似度
        emb_sim = _cosine(getattr(e1, "name_embedding", []) or [], getattr(e2, "name_embedding", []) or [])
        
        # 2. 主名称token相似度
        
        # 2. 主名称token相似度
        tokens1 = set(_tokenize(getattr(e1, "name", "") or ""))
        tokens2 = set(_tokenize(getattr(e2, "name", "") or ""))
        j_primary = _jaccard(list(tokens1), list(tokens2))
        
        # 3. 获取所有别名
        j_primary = _jaccard(list(tokens1), list(tokens2))
        
        # 3. 获取所有别名
        aliases1 = getattr(e1, "aliases", []) or []
        aliases2 = getattr(e2, "aliases", []) or []
        
        # 4. 计算所有别名的token集合（用于整体Jaccard）
        
        # 4. 计算所有别名的token集合（用于整体Jaccard）
        alias_tokens1 = set(tokens1)
        alias_tokens2 = set(tokens2)
        for a in aliases1:
            alias_tokens1 |= set(_tokenize(a))
        for a in aliases2:
            alias_tokens2 |= set(_tokenize(a))
        j_alias = _jaccard(list(alias_tokens1), list(alias_tokens2))
        
        # 5. 使用 _has_exact_alias_match 检测完全匹配
        has_exact_match = _has_exact_alias_match(e1, e2)
        
        # 6. 计算最佳别名匹配度（所有名称两两比较）
        all_names1 = [getattr(e1, "name", "") or "", *aliases1]
        all_names2 = [getattr(e2, "name", "") or "", *aliases2]
        
        max_alias_sim = 0.0
        
        if has_exact_match:
            max_alias_sim = 1.0
        else:
            for n1 in all_names1:
                if not n1:
                    continue
                tokens_n1 = set(_tokenize(n1))
                
                for n2 in all_names2:
                    if not n2:
                        continue
                    
                    tokens_n2 = set(_tokenize(n2))
                    sim = _jaccard(list(tokens_n1), list(tokens_n2))
                    max_alias_sim = max(max_alias_sim, sim)
        
        # 7. 综合评分
        if has_exact_match:
            s_name = 0.4 * emb_sim + 0.2 * j_primary + 0.4 * max_alias_sim
        else:
            s_name = 0.6 * emb_sim + 0.2 * j_primary + 0.2 * max_alias_sim
        
        return s_name, emb_sim, j_primary, j_alias, max_alias_sim, has_exact_match
    
    # ========== 类型相似度工具函数 ==========
    
    def _canonicalize_type(t: str) -> str:
        """类型标准化：将各种类型别名映射到规范类型"""
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
        """类型相似度：计算两个类型的相似度（基于规范化和相似度表）"""
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
    # 阈值与权重设定
    _defaults = DedupConfig()
    
    # 核心阈值
    T_NAME_STRICT = (config.fuzzy_name_threshold_strict if config is not None else _defaults.fuzzy_name_threshold_strict)
    T_TYPE_STRICT = (config.fuzzy_type_threshold_strict if config is not None else _defaults.fuzzy_type_threshold_strict)
    T_OVERALL = (config.fuzzy_overall_threshold if config is not None else _defaults.fuzzy_overall_threshold)
    UNKNOWN_NAME_T = (config.fuzzy_unknown_type_name_threshold if config is not None else _defaults.fuzzy_unknown_type_name_threshold)
    UNKNOWN_TYPE_T = (config.fuzzy_unknown_type_type_threshold if config is not None else _defaults.fuzzy_unknown_type_type_threshold)
    
    # 权重：名称70%，类型30%
    W_NAME = 0.7
    W_TYPE = 0.3


    def _merge_entities_with_aliases(canonical: ExtractedEntityNode, losing: ExtractedEntityNode):
        """ 模糊匹配中的实体合并。
        
        合并策略：
        1. 保留canonical的主名称不变
        2. 将losing的主名称添加为alias（如果不同）
        3. 合并两个实体的所有aliases
        4. 自动去重（case-insensitive）并排序
        
        Args:
            canonical: 规范实体（保留）
            losing: 被合并实体（删除）
            
        Note:
            使用alias_utils.normalize_aliases进行标准化去重
        """
        # 获取规范实体的名称
        canonical_name = (getattr(canonical, "name", "") or "").strip()
        losing_name = (getattr(losing, "name", "") or "").strip()
        
        # 收集所有需要合并的别名
        all_aliases = []
        
        # 1. 添加canonical现有的别名
        current_aliases = getattr(canonical, "aliases", []) or []
        all_aliases.extend(current_aliases)
        
        # 2. 添加losing实体的名称（如果不同于canonical的名称）
        if losing_name and losing_name != canonical_name:
            all_aliases.append(losing_name)
        
        # 3. 添加losing实体的所有别名
        losing_aliases = getattr(losing, "aliases", []) or []
        all_aliases.extend(losing_aliases)
        
        # 4. 标准化并去重（使用标准化后的字符串进行去重）
        try:
            from app.core.memory.utils.alias_utils import normalize_aliases
            canonical.aliases = normalize_aliases(canonical_name, all_aliases)
        except Exception:
            # 如果导入失败，使用增强的去重逻辑
            # 使用标准化后的字符串作为key进行去重
            seen_normalized = set()
            unique_aliases = []
            
            for alias in all_aliases:
                if not alias:
                    continue
                
                alias_stripped = str(alias).strip()
                if not alias_stripped or alias_stripped == canonical_name:
                    continue
                
                # 标准化：转小写用于去重判断
                alias_normalized = alias_stripped.lower()
                
                if alias_normalized not in seen_normalized:
                    seen_normalized.add(alias_normalized)
                    unique_aliases.append(alias_stripped)
            
            # 排序并赋值
            canonical.aliases = sorted(unique_aliases)
    
    # ========== 主循环：遍历所有实体对进行模糊匹配 ==========
    i = 0
    while i < len(deduped_entities):
        a = deduped_entities[i]
        j = i + 1
        while j < len(deduped_entities):
            b = deduped_entities[j]
            
            # 跳过不同业务组的实体
            if getattr(a, "group_id", None) != getattr(b, "group_id", None):
                j += 1
                continue
            
            # ========== 第一步：计算相似度分数 ==========
            
            # 1.1 名称+别名相似度（包含完全匹配检测）
            s_name, emb_sim, j_primary, j_alias, max_alias_sim, has_exact_match = _name_similarity_with_aliases(a, b)
            
            # 1.2 类型相似度
            s_type = _type_similarity(getattr(a, "entity_type", None), getattr(b, "entity_type", None))
            
            # ========== 第二步：动态调整阈值 ==========
            
            # 2.1 检测是否存在UNKNOWN类型
            unknown_present = (
                str(getattr(a, "entity_type", "")).upper() == "UNKNOWN"
                or str(getattr(b, "entity_type", "")).upper() == "UNKNOWN"
            )
            
            # 2.2 根据类型设置名称阈值
            tn = UNKNOWN_NAME_T if unknown_present else T_NAME_STRICT
            
            # 2.3 如果有完全别名匹配，降低名称相似度阈值
            if has_exact_match:
                tn = min(tn, 0.75)
            
            # 2.4 设置类型阈值和综合阈值
            type_threshold = UNKNOWN_TYPE_T if unknown_present else T_TYPE_STRICT
            tover = T_OVERALL
            
            # ========== 第三步：计算综合评分 ==========
            # 公式：overall = 名称权重(70%) × 名称相似度 + 类型权重(30%) × 类型相似度
            overall = W_NAME * s_name + W_TYPE * s_type
            
            # ========== 第四步：特殊规则判断（别名完全匹配快速通道）==========
            
            # 4.1 检查主名称是否相同
            name_a_normalized = (getattr(a, "name", "") or "").strip().lower()
            name_b_normalized = (getattr(b, "name", "") or "").strip().lower()
            same_name = (name_a_normalized == name_b_normalized) and name_a_normalized != ""
            
            # 4.2 别名匹配特殊规则（满足任一条件即可快速合并）
            alias_match_merge = False
            
            # 规则1：别名完全匹配 + 类型相似度 ≥ 0.7
            if has_exact_match and s_type >= 0.7:
                alias_match_merge = True
            
            # 规则2：名称相同 + 别名匹配 + 类型相似度 ≥ 0.5
            elif same_name and has_exact_match and s_type >= 0.5:
                alias_match_merge = True
            
            # 规则3：名称相同 + 别名匹配 + 类型完全相同
            elif same_name and has_exact_match and s_type >= 1.0:
                alias_match_merge = True

            # ========== 第五步：最终合并判断 ==========
            # 满足以下任一条件即执行合并：
            # 条件A（快速通道）：alias_match_merge = True
            # 条件B（标准通道）：s_name ≥ tn AND s_type ≥ type_threshold AND overall ≥ tover
            if alias_match_merge or (s_name >= tn and s_type >= type_threshold and overall >= tover):
                # ========== 第六步：执行实体合并 ==========
                
                # 6.1 合并别名
                _merge_entities_with_aliases(a, b)
                
                # 6.2 合并其他属性（描述、事实摘要、时间范围等）
                _merge_attribute(a, b)
                
                # 6.3 记录合并日志
                try:
                    merge_reason = "[别名匹配]" if alias_match_merge else "[模糊]"
                    merge_reason = "[别名匹配]" if alias_match_merge else "[模糊]"
                    fuzzy_merge_records.append(
                        f"{merge_reason} 规范实体 {a.id} ({a.group_id}|{a.name}|{a.entity_type}) <- 合并实体 {b.id} ({b.group_id}|{b.name}|{b.entity_type}) | "
                        f"s_name={s_name:.3f}, s_type={s_type:.3f}, overall={overall:.3f}, exact_alias={has_exact_match}"
                    )
                except Exception:
                    pass
                
                # 6.4 建立 ID 重定向映射
                try:
                    canonical_id = id_redirect.get(getattr(a, "id", None), getattr(a, "id", None))
                    losing_id = getattr(b, "id", None)
                    if losing_id and canonical_id:
                        # 将被合并实体的ID指向规范实体
                        id_redirect[losing_id] = canonical_id
                        
                        # 扁平化重定向链：确保所有指向losing_id的映射都指向canonical_id
                        for k, v in list(id_redirect.items()):
                            if v == losing_id:
                                id_redirect[k] = canonical_id
                except Exception:
                    pass
                
                # 6.5 从列表中移除被合并的实体
                deduped_entities.pop(j)
                continue  # 不增加j，继续检查当前位置的下一个实体
            
            # ========== 未达到合并条件：检查下一对 ==========
            else:
                j += 1  # 移动到下一个实体
        i += 1

    return deduped_entities, id_redirect, fuzzy_merge_records

async def LLM_decision(  # 决策中包含去重和消歧的功能
    deduped_entities: List[ExtractedEntityNode],
    statement_entity_edges: List[StatementEntityEdge],
    entity_entity_edges: List[EntityEntityEdge],
    id_redirect: Dict[str, str],
    config: DedupConfig,
    llm_client = None,
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
        if not bool(config.enable_llm_dedup_blockwise):
            return deduped_entities, id_redirect, llm_records
        # 从配置读取 LLM 迭代参数
        block_size = config.llm_block_size
        block_concurrency = config.llm_block_concurrency
        pair_concurrency = config.llm_pair_concurrency
        max_rounds = config.llm_max_rounds

        try:
            llm_mod = importlib.import_module("app.core.memory.storage_services.extraction_engine.deduplication.entity_dedup_llm")
            llm_fn = llm_mod.llm_dedup_entities_iterative_blocks
        except Exception as e:
            llm_records.append(f"[LLM错误] 无法导入 entity_dedup_llm 模块: {e}")
            return deduped_entities, id_redirect, llm_records

        # 验证 LLM 客户端
        if llm_client is None:
            llm_records.append("[LLM错误] LLM 客户端未提供")
            return deduped_entities, id_redirect, llm_records

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
    config: DedupConfig,
    llm_client = None,
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
        if not bool(config.enable_llm_disambiguation):
            return deduped_entities, id_redirect, blocked_pairs, disamb_records

        from app.core.memory.storage_services.extraction_engine.deduplication.entity_dedup_llm import (
            llm_disambiguate_pairs_iterative,
        )
        
        # 验证 LLM 客户端
        if llm_client is None:
            disamb_records.append("[DISAMB错误] LLM 客户端未提供")
            return deduped_entities, id_redirect, blocked_pairs, disamb_records
        
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
    llm_client = None,
) -> Tuple[
    List[ExtractedEntityNode], 
    List[StatementEntityEdge], 
    List[EntityEntityEdge],
    Dict[str, Any]  # 新增：返回详细的去重消歧记录
]:
    """
    主流程：依次执行精确匹配、模糊匹配与（可选）LLM 决策融合，随后对边做重定向与去重。之后再处理边，是关系去重和消歧
    返回：去重后的实体、语句→实体边、实体↔实体边。
    """
    local_llm_records: List[str] = [] # 作为“审计日志”的本地收集器 初始化，保留为了之后对于LLM决策追溯
    # 1) 精确匹配
    deduped_entities, id_redirect, exact_merge_map = accurate_match(entity_nodes)

    # 1.5) LLM 决策消歧：阻断同名不同类型的高相似对，并应用必要的合并
    deduped_entities, id_redirect, blocked_pairs, disamb_records = await LLM_disamb_decision(
        deduped_entities, statement_entity_edges, entity_entity_edges, id_redirect, config=dedup_config, llm_client=llm_client
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
            deduped_entities, statement_entity_edges, entity_entity_edges, id_redirect, config=dedup_config, llm_client=llm_client
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
    
    # 构建详细的去重消歧记录（用于内存访问，避免解析日志文件）
    dedup_details = {
        "exact_merge_map": exact_merge_map,
        "fuzzy_merge_records": fuzzy_merge_records,
        "llm_decision_records": local_llm_records,
        "disamb_records": disamb_records,
        "id_redirect": id_redirect,
        "blocked_pairs": blocked_pairs,
    }

    return deduped_entities, list(stmt_ent_map.values()), list(ent_ent_map.values()), dedup_details

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
                merged_ids = sorted(info.get("merged_ids", set()))
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
