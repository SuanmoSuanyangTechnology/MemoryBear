"""记忆引擎展示 Service

负责：
- 从 ExtractionResult 组装 0~3 个有效引擎事件（EXTRACTION / CROSS_MODAL / EMOTION）
- 从遗忘、反思引擎的汇总结果组装 FORGETTING / REFLECTION 事件
- 查询时按用户时区日期聚合事件并生成卡片文案
- 异常隔离（PG 写入失败不影响主流程）
"""

import logging
import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.datetime_utils import to_timestamp_ms, utcnow_naive
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.memory_engine_display_event_repository import (
    MemoryEngineDisplayEventRepository,
)

logger = logging.getLogger(__name__)

# 通用角色实体排除列表（跨模态主题过滤）
_ROLE_ENTITY_NAMES = frozenset({
    "用户", "我", "user", "i", "ai助手", "助手", "助理", "ai",
    "assistant", "ai回复", "ai assistant",
})

_EMOTION_TYPE_LABELS: Dict[str, Dict[str, str]] = {
    "zh": {
        "joy": "喜悦",
        "sadness": "悲伤",
        "anger": "愤怒",
        "fear": "恐惧",
        "surprise": "惊讶",
        "neutral": "中性",
    },
    "en": {
        "joy": "joy",
        "sadness": "sadness",
        "anger": "anger",
        "fear": "fear",
        "surprise": "surprise",
        "neutral": "neutral emotion",
    },
}


class MemoryEngineDisplayService:
    """引擎展示业务逻辑层"""

    @staticmethod
    async def query_cards(
        db: AsyncSession,
        end_user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        timezone: str,
        language: str,
        page: int,
        pagesize: int,
    ) -> tuple[List[Dict[str, Any]], int] | None:
        """查询聚合事件并组装引擎展示卡片。

        返回 None 表示终端用户不属于当前工作空间。
        """
        end_user_repo = EndUserRepository(db)
        if await end_user_repo.get_active_end_user_in_workspace_async(
            end_user_id,
            workspace_id,
        ) is None:
            return None

        repo = MemoryEngineDisplayEventRepository(db)
        groups, total = await repo.query_aggregated_paginated(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            timezone=timezone,
            page=page,
            pagesize=pagesize,
        )
        cards = MemoryEngineDisplayService.build_cards_from_groups(
            groups=groups,
            end_user_id=str(end_user_id),
            timezone=timezone,
            language=language,
        )
        return cards, total

    # ──────────────────────────────────────────────
    # 写入侧：从各引擎的汇总结果组装事件
    # ──────────────────────────────────────────────

    @staticmethod
    async def save_events(
        end_user_id: str,
        extraction_result: Any,
    ) -> None:
        """从 ExtractionResult 组装引擎事件并写入 PG。

        一次最多产生 3 条事件（萃取、跨模态、情感），
        共用同一个 operation_id 和 occurred_at。

        PG 写入只执行一次，不重试。失败时记录日志后立即结束。

        Args:
            end_user_id: 终端用户 ID（字符串形式 UUID）
            extraction_result: WritePipeline 的 ExtractionResult
        """
        try:
            events_data = []

            extraction_event = _build_extraction_event(extraction_result)
            if extraction_event is not None:
                events_data.append(extraction_event)

            cross_modal_event = _build_cross_modal_event(extraction_result)
            if cross_modal_event is not None:
                events_data.append(cross_modal_event)

            emotion_event = _build_emotion_event(extraction_result)
            if emotion_event is not None:
                events_data.append(emotion_event)

            if not events_data:
                return
        except Exception as e:
            logger.error(
                f"[EngineDisplay] 事件组装失败: end_user_id={end_user_id}, error={e}",
                exc_info=True,
            )
            return

        await _persist_events(end_user_id, events_data)

    @staticmethod
    async def save_forgetting_event(
        end_user_id: str,
        forget_summary: dict,
    ) -> None:
        """配额驱动的定时遗忘整理完成后调用。

        只有本轮实际软删除数 > 0 才写入一条 FORGETTING 事件；
        手动删除单节点和清空全部记忆不走这里。

        Args:
            end_user_id: 终端用户 ID（字符串形式 UUID）
            forget_summary: ForgetService.run() 的返回值
        """
        try:
            event = _build_forgetting_event(forget_summary)
        except Exception as e:
            logger.error(
                f"[EngineDisplay] 遗忘事件组装失败: end_user_id={end_user_id}, error={e}",
                exc_info=True,
            )
            return
        if event is None:
            return
        await _persist_events(end_user_id, [event])

    @staticmethod
    async def save_reflection_event(
        end_user_id: str,
        layer2_result: dict,
        scan_type: str = "layer2_frequent",
    ) -> None:
        """Layer 2 高频巡检或每日全量去重完成后调用。

        五类成果合计为 0（含未配置 LLM、全部子问题被跳过）时不写入。

        Args:
            end_user_id: 终端用户 ID（字符串形式 UUID）
            layer2_result: Layer2Inspector.run() 或 run_dedup_full_scan() 的返回值
            scan_type: "layer2_frequent"（高频巡检）或 "dedup_full_scan"（全量去重）
        """
        try:
            event = _build_reflection_event(layer2_result, scan_type)
        except Exception as e:
            logger.error(
                f"[EngineDisplay] 反思事件组装失败: end_user_id={end_user_id}, error={e}",
                exc_info=True,
            )
            return
        if event is None:
            return
        await _persist_events(end_user_id, [event])

    # ──────────────────────────────────────────────
    # 查询侧：聚合事件并生成卡片
    # ──────────────────────────────────────────────

    @staticmethod
    def build_cards_from_groups(
        groups: List[Dict[str, Any]],
        end_user_id: str,
        timezone: str,
        language: str,
    ) -> List[Dict[str, Any]]:
        """将聚合组转换为前端卡片列表。

        Args:
            groups: repository 返回的聚合组列表
            end_user_id: 终端用户 ID
            timezone: 请求时区（用于生成确定性 ID）
            language: 响应文案语言（zh / en）

        Returns:
            卡片列表，每项包含 id, engine_type, name, content, occurred_at
        """
        cards = []
        for group in groups:
            engine_type = group["engine_type"]
            local_date = group["local_date"]
            max_occurred_at = group["max_occurred_at"]
            events = group["events"]

            # 生成确定性 ID（UUID v5）
            card_id = _generate_card_id(end_user_id, engine_type, timezone, local_date)

            # 聚合 details
            merged = _merge_event_details(engine_type, events)

            name, content = _generate_card_text(engine_type, merged, language)

            cards.append({
                "id": card_id,
                "engine_type": engine_type,
                "name": name,
                "content": content,
                "occurred_at": to_timestamp_ms(max_occurred_at),
            })

        return cards


# ──────────────────────────────────────────────
# 内部函数：统一写入
# ──────────────────────────────────────────────


async def _persist_events(end_user_id: str, events_data: List[Dict[str, Any]]) -> None:
    """生成共享标识、批量写入 PG、异常隔离。三个入口共用。

    每次调用新生成 operation_id，因此不会撞唯一约束
    uq_engine_display_user_type_op；本方案不要求跨调用幂等。
    PG 写入只执行一次，不重试，失败只记日志。
    """
    from app.db import get_db_context
    from app.models.memory_engine_display_event_model import MemoryEngineDisplayEvent

    if not events_data:
        return

    # end_user_id 需要转为 UUID 对象（PG 外键是 UUID 类型）
    try:
        user_uuid = uuid.UUID(end_user_id)
    except (ValueError, AttributeError, TypeError):
        logger.warning(
            f"[EngineDisplay] 无法将 end_user_id 转为 UUID: {end_user_id}"
        )
        return

    operation_id = uuid.uuid4()
    occurred_at = utcnow_naive()

    records = [
        MemoryEngineDisplayEvent(
            id=uuid.uuid4(),
            end_user_id=user_uuid,
            operation_id=operation_id,
            engine_type=data["engine_type"],
            details=data["details"],
            occurred_at=occurred_at,
        )
        for data in events_data
    ]

    try:
        with get_db_context() as db:
            MemoryEngineDisplayEventRepository(db).bulk_insert_events(records)
        logger.info(
            f"[EngineDisplay] PG 写入成功: end_user_id={end_user_id}, "
            f"operation_id={operation_id}, "
            f"engines={[d['engine_type'] for d in events_data]}"
        )
    except Exception as e:
        logger.error(
            f"[EngineDisplay] PG 写入失败: end_user_id={end_user_id}, error={e}",
            exc_info=True,
        )


# ──────────────────────────────────────────────
# 内部函数：事件组装
# ──────────────────────────────────────────────


def _build_extraction_event(result: Any) -> Optional[Dict[str, Any]]:
    """组装记忆萃取引擎事件。"""
    statement_count = len(result.statement_nodes) if result.statement_nodes else 0
    entity_count = len(result.entity_nodes) if result.entity_nodes else 0
    relation_count = len(result.entity_entity_edges) if result.entity_entity_edges else 0

    if statement_count == 0 and entity_count == 0 and relation_count == 0:
        return None

    return {
        "engine_type": "EXTRACTION",
        "details": {
            "statement_count": statement_count,
            "entity_count": entity_count,
            "relation_count": relation_count,
        },
    }


def _build_cross_modal_event(result: Any) -> Optional[Dict[str, Any]]:
    """组装跨模态记忆关联联想引擎事件。"""
    perceptual_edges = result.perceptual_edges if result.perceptual_edges else []
    perceptual_nodes = result.perceptual_nodes if result.perceptual_nodes else []

    if not perceptual_edges:
        return None

    # 分类边
    chunk_edges = [e for e in perceptual_edges if getattr(e, "source_type", "") == "chunk"]
    entity_edges = [e for e in perceptual_edges if getattr(e, "source_type", "") == "entity"]

    if not chunk_edges and not entity_edges:
        return None

    # 统计模态数量（按 target perceptual_node ID 去重）
    all_edge_targets = set()
    for e in chunk_edges + entity_edges:
        all_edge_targets.add(e.target)

    # 构建 node_id -> node 映射
    node_map = {n.id: n for n in perceptual_nodes}

    modality_counts: Dict[str, int] = defaultdict(int)
    for target_id in all_edge_targets:
        node = node_map.get(target_id)
        if node:
            ptype = getattr(node, "perceptual_type", None)
            # perceptual_type 是 int: 101=image, 102=video, 103=audio, 104=document
            if ptype == 101:
                modality_counts["image"] += 1
            elif ptype == 102:
                modality_counts["video"] += 1
            elif ptype == 103:
                modality_counts["audio"] += 1
            elif ptype == 104:
                modality_counts["document"] += 1
            else:
                # 尝试从 file_type 推断
                file_type = getattr(node, "file_type", "")
                if file_type:
                    modality_counts[file_type] += 1

    chunk_association_count = len(chunk_edges)
    entity_association_count = len(entity_edges)

    # 提取实体主题（仅 entity 边）
    topics: List[str] = []
    if entity_edges and result.entity_nodes:
        entity_map = {e.id: e for e in result.entity_nodes}
        topic_counter: Dict[str, int] = defaultdict(int)
        for edge in entity_edges:
            entity = entity_map.get(edge.source)
            if entity and entity.name:
                name = entity.name.strip()
                if name.lower() not in _ROLE_ENTITY_NAMES:
                    topic_counter[name] += 1

        # 按出现次数降序、名称升序展示全部主题
        sorted_topics = sorted(topic_counter.items(), key=lambda x: (-x[1], x[0]))
        topics = [t[0] for t in sorted_topics]

    details: Dict[str, Any] = {
        "modality_counts": dict(modality_counts),
        "chunk_association_count": chunk_association_count,
        "entity_association_count": entity_association_count,
    }
    if topics:
        details["topics"] = topics

    return {
        "engine_type": "CROSS_MODAL",
        "details": details,
    }


def _build_emotion_event(result: Any) -> Optional[Dict[str, Any]]:
    """组装情感引擎事件。"""
    statement_nodes = result.statement_nodes if result.statement_nodes else []

    # 只统计用户 Statement
    user_statements = [
        s for s in statement_nodes
        if getattr(s, "speaker", None) == "user"
    ]

    # 按 emotion_type 汇总，排除空和 neutral
    emotion_stats: Dict[str, Dict[str, Any]] = {}
    for stmt in user_statements:
        etype = getattr(stmt, "emotion_type", None)
        if not etype or etype.lower().strip() == "neutral":
            continue

        etype_key = etype.lower().strip()
        if etype_key not in emotion_stats:
            emotion_stats[etype_key] = {"count": 0, "intensity_sum": 0.0}
        emotion_stats[etype_key]["count"] += 1

        intensity = getattr(stmt, "emotion_intensity", None)
        if intensity is not None:
            emotion_stats[etype_key]["intensity_sum"] += float(intensity)

    if not emotion_stats:
        return None

    return {
        "engine_type": "EMOTION",
        "details": {
            "emotion_stats": emotion_stats,
        },
    }


# 反思引擎五类成果的 details key，写入侧和聚合侧共用
_REFLECTION_COUNT_KEYS = (
    "entity_merged_count",
    "alias_merged_count",
    "description_merged_count",
    "metadata_extracted_count",
    "unresolved_resolved_count",
)


def _build_forgetting_event(summary: Any) -> Optional[Dict[str, Any]]:
    """组装遗忘引擎事件。

    released_count 取 ForgetService 逐批累加的实际软删除数
    （summary["deleted"]），不使用 initial_count - final_count。
    被跳过或本轮没有实际软删除时不生成事件。
    """
    if not isinstance(summary, dict) or summary.get("skipped"):
        return None

    released = _as_count(summary.get("deleted"))
    if released <= 0:
        return None

    scanned = _as_count(summary.get("scanned_count"))
    node_type_counts = summary.get("node_type_counts")
    if not isinstance(node_type_counts, dict):
        node_type_counts = {}

    return {
        "engine_type": "FORGETTING",
        "details": {
            # 老版本 ForgetService 不返回 scanned_count 时退化为 released
            "scanned_count": scanned or released,
            "released_count": released,
            "node_type_counts": {
                str(k): _as_count(v) for k, v in node_type_counts.items()
            },
        },
    }


def _build_reflection_event(
    result: Any,
    scan_type: str,
) -> Optional[Dict[str, Any]]:
    """组装反思引擎事件。五类成果全为 0 时不生成。

    高频巡检返回按子问题分组的嵌套结构，低频全量去重返回扁平结构，
    因此 entity_merged_count 按 scan_type 分两种取法。两条链路对外的
    merged_count 都已包含确定性直接归并，不再叠加 direct_merged_count。

    子问题 status 为 skipped / timeout / error 时相关 key 缺失，
    按 0 计入，不影响其他子问题成果展示。
    """
    if not isinstance(result, dict) or result.get("status") == "skipped":
        return None

    if scan_type == "dedup_full_scan":
        entity_merged = _pick(result, "merged_count")
    else:
        entity_merged = _pick(result.get("entity_dedup"), "merged_count")

    details: Dict[str, Any] = {
        "scan_type": scan_type,
        "entity_merged_count": entity_merged,
        "alias_merged_count": _pick(result.get("alias_merge"), "merge_count"),
        "description_merged_count": _pick(result.get("description_merge"), "merged_count"),
        "metadata_extracted_count": _pick(result.get("metadata_extraction"), "extracted"),
        "unresolved_resolved_count": _pick(
            result.get("unresolved_entity"), "resolved", "forced"
        ),
    }

    if all(details[key] == 0 for key in _REFLECTION_COUNT_KEYS):
        return None
    return {"engine_type": "REFLECTION", "details": details}


def _as_count(value: Any) -> int:
    """把任意来源的计数安全转为非负 int；无法转换按 0。"""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _pick(sub: Any, *keys: str) -> int:
    """从子问题结果里安全累加计数；非 dict 或 key 缺失都按 0。"""
    if not isinstance(sub, dict):
        return 0
    return sum(_as_count(sub.get(k)) for k in keys)


# ──────────────────────────────────────────────
# 内部函数：查询聚合
# ──────────────────────────────────────────────


def _generate_card_id(
    end_user_id: str,
    engine_type: str,
    timezone: str,
    local_date: date,
) -> str:
    """生成确定性聚合卡片 ID（UUID v5）。

    聚合卡片不落库，ID 由聚合键 SHA-1 推导而来，
    保证同一 (用户, 引擎, 时区, 本地日期) 每次查询得到相同 ID，
    前端可直接用作列表 key 和去重依据。
    """
    name = f"{end_user_id}:{engine_type}:{timezone}:{local_date.isoformat()}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, name))


def _merge_event_details(
    engine_type: str,
    events: list,
) -> Dict[str, Any]:
    """合并同一聚合组内所有事件的 details。"""
    if engine_type == "EXTRACTION":
        total_statements = 0
        total_entities = 0
        total_relations = 0
        for e in events:
            d = e.details or {}
            total_statements += d.get("statement_count", 0)
            total_entities += d.get("entity_count", 0)
            total_relations += d.get("relation_count", 0)
        return {
            "statement_count": total_statements,
            "entity_count": total_entities,
            "relation_count": total_relations,
        }

    elif engine_type == "CROSS_MODAL":
        merged_modality: Dict[str, int] = defaultdict(int)
        total_chunk_assoc = 0
        total_entity_assoc = 0
        # topic → {count, last_occurred_at}
        topic_info: Dict[str, Dict[str, Any]] = {}

        for e in events:
            d = e.details or {}
            mc = d.get("modality_counts", {})
            for k, v in mc.items():
                merged_modality[k] += v
            total_chunk_assoc += d.get("chunk_association_count", 0)
            total_entity_assoc += d.get("entity_association_count", 0)

            for topic in d.get("topics", []):
                normalized = topic.strip()
                if normalized:
                    if normalized not in topic_info:
                        topic_info[normalized] = {"count": 0, "last_at": e.occurred_at}
                    topic_info[normalized]["count"] += 1
                    if e.occurred_at and e.occurred_at > topic_info[normalized]["last_at"]:
                        topic_info[normalized]["last_at"] = e.occurred_at

        # 主题排序：事件数降序 > 最后出现时间降序 > 名称升序，完整展示
        sorted_topics = sorted(
            topic_info.items(),
            key=lambda x: (-x[1]["count"], -(x[1]["last_at"].timestamp() if x[1]["last_at"] else 0), x[0]),
        )
        topics = [t[0] for t in sorted_topics]

        return {
            "modality_counts": dict(merged_modality),
            "chunk_association_count": total_chunk_assoc,
            "entity_association_count": total_entity_assoc,
            "topics": topics,
        }

    elif engine_type == "EMOTION":
        # emotion_type → {count, intensity_sum, last_at}
        merged_emotions: Dict[str, Dict[str, Any]] = {}

        for e in events:
            d = e.details or {}
            stats = d.get("emotion_stats", {})
            for etype, info in stats.items():
                if etype not in merged_emotions:
                    merged_emotions[etype] = {"count": 0, "intensity_sum": 0.0, "last_at": e.occurred_at}
                merged_emotions[etype]["count"] += info.get("count", 0)
                merged_emotions[etype]["intensity_sum"] += info.get("intensity_sum", 0.0)
                if e.occurred_at and e.occurred_at > merged_emotions[etype]["last_at"]:
                    merged_emotions[etype]["last_at"] = e.occurred_at

        # 排序：出现次数降序 > 平均强度降序 > 最后出现时间降序 > emotion_type 升序
        sorted_emotions = sorted(
            merged_emotions.items(),
            key=lambda x: (
                -x[1]["count"],
                -(x[1]["intensity_sum"] / x[1]["count"] if x[1]["count"] > 0 else 0),
                -(x[1]["last_at"].timestamp() if x[1]["last_at"] else 0),
                x[0],
            ),
        )
        top_emotions = sorted_emotions[:3]

        return {
            "emotions": [{"type": t[0], "count": t[1]["count"]} for t in top_emotions],
        }

    elif engine_type == "FORGETTING":
        scanned = 0
        released = 0
        node_types: Dict[str, int] = defaultdict(int)
        for e in events:
            d = e.details or {}
            scanned += _as_count(d.get("scanned_count"))
            released += _as_count(d.get("released_count"))
            for k, v in (d.get("node_type_counts") or {}).items():
                node_types[str(k)] += _as_count(v)
        return {
            "scanned_count": scanned,
            "released_count": released,
            "node_type_counts": dict(node_types),
        }

    elif engine_type == "REFLECTION":
        # 高频巡检和每日全量去重的成果在同一天相加，卡片不区分 scan_type
        merged_counts = {key: 0 for key in _REFLECTION_COUNT_KEYS}
        for e in events:
            d = e.details or {}
            for key in _REFLECTION_COUNT_KEYS:
                merged_counts[key] += _as_count(d.get(key))
        return merged_counts

    return {}


def _generate_card_text(
    engine_type: str,
    merged: Dict[str, Any],
    language: str,
) -> tuple:
    """根据聚合后的 details 生成本地化 name 和 content。"""
    if language == "en":
        return _generate_card_text_en(engine_type, merged)

    return _generate_card_text_zh(engine_type, merged)


def _generate_card_text_zh(
    engine_type: str,
    merged: Dict[str, Any],
) -> tuple[str, str]:
    """生成中文卡片文案。"""
    if engine_type == "EXTRACTION":
        name = "我整理了交流中的重要信息"
        stmt_count = merged.get("statement_count", 0)
        entity_count = merged.get("entity_count", 0)
        relation_count = merged.get("relation_count", 0)

        parts = []
        if stmt_count > 0:
            parts.append(f"提炼了 {stmt_count} 条信息")
        if entity_count > 0:
            parts.append(f"识别了 {entity_count} 个关键内容")
        if relation_count > 0:
            parts.append(f"建立了 {relation_count} 组内容关系")

        content = "这一天从交流中" + "，并".join(parts) + "。" if parts else ""
        return name, content

    elif engine_type == "CROSS_MODAL":
        modality_counts = merged.get("modality_counts", {})
        entity_assoc = merged.get("entity_association_count", 0)
        topics = merged.get("topics", [])

        # 构建模态描述
        modality_parts = []
        modality_zh = {"image": "图片", "video": "视频", "audio": "音频", "document": "文档"}
        for mtype, count in modality_counts.items():
            label = modality_zh.get(mtype, mtype)
            modality_parts.append(f"{count} {'张' if mtype == 'image' else '份'}{label}")

        modality_desc = "和".join(modality_parts) if modality_parts else "跨模态内容"

        if entity_assoc > 0 and topics:
            name = "我把不同形式的记忆联系了起来"
            topic_str = "和".join(f"「{t}」" for t in topics)
            content = f"识别了你分享的{modality_desc}，并进一步将它们与{topic_str}等内容建立了关联。"
        else:
            name = "我把跨模态内容和交流联系了起来"
            content = f"识别了你分享的{modality_desc}，并将它们与相关交流内容建立了联系。"

        return name, content

    elif engine_type == "EMOTION":
        name = "我留意了交流中的情绪变化"
        emotions = merged.get("emotions", [])

        if not emotions:
            content = "这一天的交流中，我留意到了情绪表达。"
        else:
            emotion_names = []
            for em in emotions:
                etype = em["type"]
                zh_name = _EMOTION_TYPE_LABELS["zh"].get(etype, etype)
                emotion_names.append(zh_name)
            emotion_str = "和".join(emotion_names)
            content = f"这一天的交流中，我留意到了{emotion_str}的情绪表达。"

        return name, content

    elif engine_type == "FORGETTING":
        name = "我整理了不再活跃的记忆"
        scanned = merged.get("scanned_count", 0)
        released = merged.get("released_count", 0)
        if scanned > released:
            head = f"评估了 {scanned} 条较早写入的记录，将其中 {released} 条移出了活跃记忆"
        else:
            head = f"将 {released} 条记录移出了活跃记忆"
        content = f"{head}；情景摘要不参与整理，高频引用的实体受到保护。"
        return name, content

    elif engine_type == "REFLECTION":
        name = "我梳理了记忆之间的重复和缺口"
        labels = {
            "entity_merged_count": "完成了 {n} 次重复实体合并",
            "alias_merged_count": "归并了 {n} 组实体别名",
            "description_merged_count": "整合了 {n} 个实体的零散描述",
            "metadata_extracted_count": "补全了 {n} 个实体的结构化信息",
            "unresolved_resolved_count": "解析了 {n} 条此前无法识别的陈述",
        }
        parts = [
            labels[key].format(n=count)
            for key, count in _rank_reflection_counts(merged)
        ]
        if not parts:
            content = ""
        elif len(parts) == 1:
            content = parts[0] + "。"
        else:
            content = "，".join(parts[:-1]) + "，并" + parts[-1] + "。"
        return name, content

    return ("", "")


def _generate_card_text_en(
    engine_type: str,
    merged: Dict[str, Any],
) -> tuple[str, str]:
    """Generate English card copy."""
    if engine_type == "EXTRACTION":
        name = "I organized important information from our conversations"
        stmt_count = merged.get("statement_count", 0)
        entity_count = merged.get("entity_count", 0)
        relation_count = merged.get("relation_count", 0)

        parts = []
        if stmt_count > 0:
            parts.append(
                f"extracted {stmt_count} {_pluralize('piece', stmt_count)} of information"
            )
        if entity_count > 0:
            parts.append(
                f"identified {entity_count} key {_pluralize('item', entity_count)}"
            )
        if relation_count > 0:
            parts.append(
                f"established {relation_count} content {_pluralize('relationship', relation_count)}"
            )

        content = (
            "From the conversations that day, I " + _join_english(parts) + "."
            if parts
            else ""
        )
        return name, content

    if engine_type == "CROSS_MODAL":
        modality_counts = merged.get("modality_counts", {})
        entity_assoc = merged.get("entity_association_count", 0)
        topics = merged.get("topics", [])

        modality_parts = [
            _format_english_modality(modality_type, count)
            for modality_type, count in modality_counts.items()
        ]
        modality_desc = _join_english(modality_parts) or "cross-modal content"

        if entity_assoc > 0 and topics:
            name = "I connected memories across different formats"
            topic_desc = _join_english([f'\"{topic}\"' for topic in topics])
            content = (
                f"I recognized the {modality_desc} you shared and connected it "
                f"with topics such as {topic_desc}."
            )
        else:
            name = "I connected cross-modal content with our conversations"
            content = (
                f"I recognized the {modality_desc} you shared and connected it "
                "with relevant conversation content."
            )
        return name, content

    if engine_type == "EMOTION":
        name = "I noticed emotional changes in our conversations"
        emotions = merged.get("emotions", [])
        if not emotions:
            content = "In the conversations that day, I noticed emotional expressions."
        else:
            emotion_names = [
                _EMOTION_TYPE_LABELS["en"].get(emotion["type"], emotion["type"])
                for emotion in emotions
            ]
            content = (
                "In the conversations that day, I noticed expressions of "
                f"{_join_english(emotion_names)}."
            )
        return name, content

    if engine_type == "FORGETTING":
        name = "I tidied up memories that are no longer active"
        scanned = merged.get("scanned_count", 0)
        released = merged.get("released_count", 0)
        if scanned > released:
            head = (
                f"I reviewed {scanned} older {_pluralize('record', scanned)} and moved "
                f"{released} of them out of active memory"
            )
        else:
            head = (
                f"I moved {released} {_pluralize('record', released)} "
                "out of active memory"
            )
        content = (
            f"{head}. Episodic summaries are never touched, and frequently "
            "referenced entities are protected."
        )
        return name, content

    if engine_type == "REFLECTION":
        name = "I reviewed duplicates and gaps across memories"
        labels = {
            "entity_merged_count": lambda n: (
                f"merged {n} duplicate {_pluralize('entity', n, 'entities')}"
            ),
            "alias_merged_count": lambda n: (
                f"consolidated {n} {_pluralize('group', n)} of entity aliases"
            ),
            "description_merged_count": lambda n: (
                f"consolidated scattered descriptions for {n} "
                f"{_pluralize('entity', n, 'entities')}"
            ),
            "metadata_extracted_count": lambda n: (
                f"filled in structured information for {n} "
                f"{_pluralize('entity', n, 'entities')}"
            ),
            "unresolved_resolved_count": lambda n: (
                f"resolved {n} previously unrecognized "
                f"{_pluralize('statement', n)}"
            ),
        }
        parts = [
            labels[key](count) for key, count in _rank_reflection_counts(merged)
        ]
        content = f"I {_join_english(parts)}." if parts else ""
        return name, content

    return ("", "")


def _rank_reflection_counts(merged: Dict[str, Any]) -> List[tuple]:
    """按（数量降序、字段名升序）返回全部非零成果。

    过滤非 int 值，避免把 scan_type 这类字符串当成计数
    （_merge_event_details 的 REFLECTION 分支只输出五个计数键，这里是防御）。
    """
    return sorted(
        (
            (key, value)
            for key, value in merged.items()
            if key in _REFLECTION_COUNT_KEYS
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
        ),
        key=lambda item: (-item[1], item[0]),
    )


def _pluralize(noun: str, count: int, plural: str | None = None) -> str:
    """Apply the English plural form used by card metrics."""
    if count == 1:
        return noun
    return plural if plural is not None else f"{noun}s"


def _join_english(parts: List[str]) -> str:
    """Join English list items with a final conjunction."""
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _format_english_modality(modality_type: str, count: int) -> str:
    """Format one modality count for English card copy."""
    labels = {
        "image": "image",
        "video": "video",
        "audio": "audio file",
        "document": "document",
    }
    label = labels.get(modality_type, modality_type)
    return f"{count} {_pluralize(label, count)}"
