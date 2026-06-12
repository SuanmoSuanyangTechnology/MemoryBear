"""
去重功能函数

第一层去重：仅执行 (end_user_id, name, entity_type) 精确匹配合并。
更精细的去重（模糊匹配、alias-to-name、LLM 决策）留给反思阶段执行。
"""
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from app.core.utils.datetime_utils import to_iso_z, utcnow_naive
from app.core.memory.models.graph_models import (
    EntityEntityEdge,
    ExtractedEntityNode,
    StatementEntityEdge,
)
from app.core.memory.models.variate_config import DedupConfig
from app.core.memory.utils.data.ontology import get_type_id

logger = logging.getLogger(__name__)


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
    
    new_type = None
    if suggested_type and suggested_type.strip():
        # 优先使用LLM建议的类型
        new_type = suggested_type.strip()
    elif canonical_type.upper() == "UNKNOWN" and losing_type.upper() != "UNKNOWN":
        # 如果canonical是UNKNOWN，使用losing的类型
        new_type = losing_type
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
            new_type = losing_type
        elif not canonical_is_generic and losing_is_generic:
            # losing是通用类型，canonical是具体类型，保持canonical（无需操作）
            pass
        elif len(losing_type) > len(canonical_type):
            # 两者都是具体类型或都是通用类型，选择更长的（通常更具体）
            new_type = losing_type
        # 否则保持canonical的类型

    # 同步更新 entity_type 和 type_id
    if new_type is not None:
        canonical.entity_type = new_type
        canonical.type_id = get_type_id(new_type)


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
    # 用户实体的 aliases 由 PgSQL end_user_info 作为唯一权威源，去重合并时不修改
    try:
        canonical_name = (getattr(canonical, "name", "") or "").strip()
        if canonical_name.lower() not in _USER_PLACEHOLDER_NAMES:
            incoming_name = (getattr(ent, "name", "") or "").strip()
            
            # 收集所有需要合并的别名，过滤掉用户占位名避免污染非用户实体
            all_aliases = list(getattr(canonical, "aliases", []) or [])
            if incoming_name and incoming_name != canonical_name and incoming_name.lower() not in _USER_PLACEHOLDER_NAMES:
                all_aliases.append(incoming_name)
            all_aliases.extend(
                a for a in (getattr(ent, "aliases", []) or [])
                if a and a.strip().lower() not in _USER_PLACEHOLDER_NAMES
            )
            
            try:
                from app.core.memory.utils.alias_utils import normalize_aliases
                canonical.aliases = normalize_aliases(canonical_name, all_aliases)
            except Exception:
                seen_normalized = set()
                unique_aliases = []
                for alias in all_aliases:
                    if not alias:
                        continue
                    alias_stripped = str(alias).strip()
                    if not alias_stripped or alias_stripped == canonical_name:
                        continue
                    alias_normalized = alias_stripped.lower()
                    if alias_normalized not in seen_normalized:
                        seen_normalized.add(alias_normalized)
                        unique_aliases.append(alias_stripped)
                canonical.aliases = sorted(unique_aliases)
    except Exception:
        pass

    # 描述合并（去重拼接，分号分隔）
    try:
        desc_a = (getattr(canonical, "description", "") or "").strip()
        desc_b = (getattr(ent, "description", "") or "").strip()
        if desc_b and desc_b != desc_a:
            if desc_a:
                # 将已有 description 按分号拆分，检查新 description 是否已存在
                existing_parts = {p.strip() for p in desc_a.replace("；", ";").split(";") if p.strip()}
                if desc_b not in existing_parts:
                    canonical.description = f"{desc_a}；{desc_b}"
            else:
                canonical.description = desc_b
        # 合并事实摘要：统一保留一个“实体: name”行，来源行去重保序
        # TODO: fact_summary 功能暂时禁用，待后续开发完善后启用
        # fact_a = getattr(canonical, "fact_summary", "") or ""
        # fact_b = getattr(ent, "fact_summary", "") or ""
        # def _extract_sources(txt: str) -> List[str]:
            # sources: List[str] = []
            # if not txt:
                # return sources
            # for line in str(txt).splitlines():
                # ln = line.strip()
                # 支持“来源:”或“来源：”前缀
                # m = re.match(r"^来源[:：]\s*(.+)$", ln)
                # if m:
                    # content = m.group(1).strip()
                    # if content:
                        # sources.append(content)
            # 如果不存在“来源”前缀，则将整体文本视为一个来源片段，避免信息丢失
            # if not sources and txt.strip():
                # sources.append(txt.strip())
            # return sources
        try:
            #     src_a = _extract_sources(fact_a)
            #     src_b = _extract_sources(fact_b)
            #     seen = set()
            #     merged_sources: List[str] = []
            #     for s in src_a + src_b:
            #         if s and s not in seen:
            #             seen.add(s)
            #             merged_sources.append(s)
            #     if merged_sources:
            #         name_line = f"实体: {getattr(canonical, 'name', '')}".strip()
            #         canonical.fact_summary = "\n".join([name_line] + [f"来源: {s}" for s in merged_sources])
            #     elif fact_b and not fact_a:
            #         canonical.fact_summary = fact_b
            pass
        except Exception:
            # 兜底：若解析失败，保留较长文本
            # if len(fact_b) > len(fact_a):
            #     canonical.fact_summary = fact_b
            pass
    except Exception:
        pass

    # is_explicit_memory 合并（任一方为 True 则结果为 True）
    try:
        if getattr(ent, "is_explicit_memory", False) and not getattr(canonical, "is_explicit_memory", False):
            canonical.is_explicit_memory = True
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
        if getattr(ent, "created_at", None) and getattr(canonical, "created_at", None) and ent.created_at < canonical.created_at:
            canonical.created_at = ent.created_at
    except Exception:
        pass

# 用户和AI助手的占位名称集合（用于名称标准化）
_USER_PLACEHOLDER_NAMES = {"用户", "我", "user", "i"}
_ASSISTANT_PLACEHOLDER_NAMES = {"ai助手", "助手", "人工智能助手", "智能助手", "智能体", "ai assistant", "assistant"}

# 标准化后的规范名称和类型
_CANONICAL_USER_NAME = "用户"
_CANONICAL_USER_TYPE = "用户"
_CANONICAL_ASSISTANT_NAME = "AI助手"
_CANONICAL_ASSISTANT_TYPE = "Agent"

# 用户和AI助手的所有可能名称（用于判断实体是否为特殊角色实体）
_ALL_USER_NAMES = _USER_PLACEHOLDER_NAMES
_ALL_ASSISTANT_NAMES = _ASSISTANT_PLACEHOLDER_NAMES


def _is_user_entity(ent: ExtractedEntityNode) -> bool:
    """判断实体是否为用户实体（name 或 entity_type 匹配）"""
    name = (getattr(ent, "name", "") or "").strip().lower()
    etype = (getattr(ent, "entity_type", "") or "").strip()
    return name in _ALL_USER_NAMES or etype == _CANONICAL_USER_TYPE


def _is_assistant_entity(ent: ExtractedEntityNode) -> bool:
    """判断实体是否为AI助手实体（name 或 entity_type 匹配）"""
    name = (getattr(ent, "name", "") or "").strip().lower()
    etype = (getattr(ent, "entity_type", "") or "").strip()
    return name in _ALL_ASSISTANT_NAMES or etype == _CANONICAL_ASSISTANT_TYPE


def _would_merge_cross_role(a: ExtractedEntityNode, b: ExtractedEntityNode) -> bool:
    """判断两个实体的合并是否会跨越用户/AI助手角色边界。
    
    用户实体和AI助手实体永远不应该被合并在一起。
    如果一方是用户实体、另一方是AI助手实体，返回 True（阻止合并）。
    """
    return (
        (_is_user_entity(a) and _is_assistant_entity(b))
        or (_is_assistant_entity(a) and _is_user_entity(b))
    )


def _normalize_special_entity_names(
    entity_nodes: List[ExtractedEntityNode],
) -> None:
    """标准化用户和AI助手实体的名称和类型。

    多轮对话中，LLM 对同一角色可能使用不同的名称变体（如"用户"/"我"/"User"，
    "AI助手"/"助手"/"Assistant"），导致精确匹配无法合并。
    此函数在去重前将这些变体统一为规范名称，并强制绑定 entity_type，确保：
    - name="用户" 的实体 entity_type 一定为 "用户"
    - name="AI助手" 的实体 entity_type 一定为 "Agent"

    Args:
        entity_nodes: 实体节点列表（原地修改）
    """
    for ent in entity_nodes:
        name = (getattr(ent, "name", "") or "").strip()
        name_lower = name.lower()

        if name_lower in _USER_PLACEHOLDER_NAMES:
            ent.name = _CANONICAL_USER_NAME
            ent.entity_type = _CANONICAL_USER_TYPE
            ent.type_id = 1  # "生命体" 对应的本体 ID
        elif name_lower in _ASSISTANT_PLACEHOLDER_NAMES:
            ent.name = _CANONICAL_ASSISTANT_NAME
            ent.entity_type = _CANONICAL_ASSISTANT_TYPE
            ent.type_id = 1  # "生命体" 对应的本体 ID

    # 第二步：清洗用户/AI助手之间的别名交叉污染（复用 clean_cross_role_aliases）
    clean_cross_role_aliases(entity_nodes)


async def fetch_neo4j_assistant_aliases(neo4j_connector, end_user_id: str) -> set:
    """从 Neo4j 查询 AI 助手实体的所有别名（小写归一化）。

    这是助手别名查询的唯一入口，供 write_tools 和 extraction_orchestrator 共用，
    避免多处维护相同的 Cypher 和名称列表。

    Args:
        neo4j_connector: Neo4j 连接器实例（需提供 execute_query 方法）
        end_user_id: 终端用户 ID

    Returns:
        小写归一化后的助手别名集合
    """
    # 查询名称列表：规范名称 + 常见变体（与 _normalize_special_entity_names 标准化后一致）
    query_names = [_CANONICAL_ASSISTANT_NAME, *_ASSISTANT_PLACEHOLDER_NAMES]
    # 去重保序
    query_names = list(dict.fromkeys(query_names))

    cypher = """
    MATCH (e:ExtractedEntity)
    WHERE e.end_user_id = $end_user_id AND e.name IN $names
    RETURN e.aliases AS aliases
    """
    try:
        result = await neo4j_connector.execute_query(
            cypher, end_user_id=end_user_id, names=query_names
        )
        assistant_aliases: set = set()
        for record in (result or []):
            for alias in (record.get("aliases") or []):
                assistant_aliases.add(alias.strip().lower())
        if assistant_aliases:
            logger.debug(f"Neo4j 中 AI 助手别名: {assistant_aliases}")
        return assistant_aliases
    except Exception as e:
        logger.warning(f"查询 Neo4j AI 助手别名失败: {e}")
        return set()


def clean_cross_role_aliases(
    entity_nodes: List[ExtractedEntityNode],
    external_assistant_aliases: set = None,
) -> None:
    """清洗用户实体和AI助手实体之间的别名交叉污染。

    在 Neo4j 写入前调用，确保：
    - 用户实体的 aliases 不包含 AI 助手的别名
    - AI 助手实体的 aliases 不包含用户的别名

    Args:
        entity_nodes: 实体节点列表（原地修改）
        external_assistant_aliases: 外部传入的 AI 助手别名集合（如从 Neo4j 查询），
                                    与本轮实体中的 AI 助手别名合并使用
    """
    # 收集本轮 AI 助手实体的所有别名
    assistant_aliases = set(external_assistant_aliases or set())
    user_aliases = set()

    for ent in entity_nodes:
        if _is_assistant_entity(ent):
            for alias in (getattr(ent, "aliases", []) or []):
                assistant_aliases.add(alias.strip().lower())
        elif _is_user_entity(ent):
            for alias in (getattr(ent, "aliases", []) or []):
                user_aliases.add(alias.strip().lower())

    # 从用户实体的 aliases 中移除 AI 助手别名
    if assistant_aliases:
        for ent in entity_nodes:
            if _is_user_entity(ent):
                original = getattr(ent, "aliases", []) or []
                cleaned = [a for a in original if a.strip().lower() not in assistant_aliases]
                if len(cleaned) < len(original):
                    ent.aliases = cleaned

    # 从 AI 助手实体的 aliases 中移除用户别名
    if user_aliases:
        for ent in entity_nodes:
            if _is_assistant_entity(ent):
                original = getattr(ent, "aliases", []) or []
                cleaned = [a for a in original if a.strip().lower() not in user_aliases]
                if len(cleaned) < len(original):
                    ent.aliases = cleaned


def accurate_match(
    entity_nodes: List[ExtractedEntityNode],
) -> Tuple[List[ExtractedEntityNode], Dict[str, str], Dict[str, Dict]]:
    """
    精确匹配：按 (end_user_id, name, entity_type) 合并实体并建立重定向与合并记录。
    
    仅当 name 和 entity_type 完全相同时才合并实体。
    更精细的去重（模糊匹配、alias-to-name、LLM 决策）留给反思阶段执行。
    
    Args:
        entity_nodes: 待去重的实体节点列表
    
    返回: (deduped_entities, id_redirect, exact_merge_map)
    """
    exact_merge_map: Dict[str, Dict] = {}
    canonical_map: Dict[str, ExtractedEntityNode] = {}
    id_redirect: Dict[str, str] = {}

    # 构建规范实体映射（按 end_user_id + name + entity_type 精确匹配）
    for ent in entity_nodes:
        name_norm = (getattr(ent, "name", "") or "").strip()
        type_norm = (getattr(ent, "entity_type", "") or "").strip()
        key = f"{getattr(ent, 'end_user_id', None)}|{name_norm}|{type_norm}"
        # 为避免跨业务组误并，明确以 end_user_id 为范围边界
        if key not in canonical_map:
            canonical_map[key] = ent
            id_redirect[ent.id] = ent.id
            continue
        canonical = canonical_map[key]

        # 执行精确属性与强弱合并，并建立重定向
        _merge_attribute(canonical, ent)
        id_redirect[ent.id] = canonical.id
        # 记录精确匹配的合并项
        try:
            k = f"{canonical.end_user_id}|{(canonical.name or '').strip()}|{(canonical.entity_type or '').strip()}"
            if k not in exact_merge_map:
                exact_merge_map[k] = {
                    "canonical_id": canonical.id,
                    "end_user_id": canonical.end_user_id,
                    "name": canonical.name,
                    "entity_type": canonical.entity_type,
                    "merged_ids": set(),
                }
            exact_merge_map[k]["merged_ids"].add(ent.id)
        except Exception:
            pass

    deduped_entities = list(canonical_map.values())
    return deduped_entities, id_redirect, exact_merge_map

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
    第一层去重：仅执行 (end_user_id, name, entity_type) 精确匹配合并，随后对边做重定向与去重。

    更精细的去重（模糊匹配、alias-to-name、LLM 决策）留给反思阶段执行。

    返回：去重后的实体、语句→实体边、实体↔实体边。
    """
    # 0) 标准化用户和AI助手实体名称（确保多轮对话中的变体名称统一）
    _normalize_special_entity_names(entity_nodes)

    # 1) 精确匹配：仅按 (end_user_id, name, entity_type) 合并
    deduped_entities, id_redirect, exact_merge_map = accurate_match(entity_nodes)

    logger.info(
        f"[第一层去重] 精确匹配完成: {len(entity_nodes)} -> {len(deduped_entities)} 实体"
    )

    # 初始化空记录（模糊匹配和LLM决策已移除，保留结构以兼容报告格式）
    fuzzy_merge_records: List[str] = []
    disamb_records: List[str] = []
    blocked_pairs: set = set()
    local_llm_records: List[str] = []

# 在主流程这里 这里是之后关系去重和消歧的地方，方法可以写在其他地方
# 此处统一对边进行处理，使用累积的 id_redirect 把边的 source/target 改成规范ID
    # 4) 边重定向与去重
    #    注意：写入阶段不再处理 "别名属于" 关系（不把 source.name 写入 target.aliases，
    #    也不归并 description），别名合并统一延迟到反思阶段执行。
    #    这里仅根据精确匹配产生的 id_redirect 做边的端点重写与去重。

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
            report_lines.append(f"去重融合报告 - {to_iso_z(utcnow_naive())}")
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
