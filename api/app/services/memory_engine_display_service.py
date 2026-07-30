"""记忆引擎展示 Service

负责：
- 从 ExtractionResult 组装 0~3 个有效引擎事件
- 查询时按用户时区日期聚合事件并生成卡片文案
- 异常隔离（PG 写入失败不影响主流程）
"""

import logging
import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

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
    def query_cards(
        db: Session,
        end_user_id: str,
        workspace_id: uuid.UUID,
        timezone: str,
        language: str,
        page: int,
        pagesize: int,
    ) -> tuple[List[Dict[str, Any]], int] | None:
        """查询聚合事件并组装引擎展示卡片。

        返回 None 表示终端用户不属于当前工作空间。
        """
        end_user_uuid = uuid.UUID(end_user_id)
        end_user_repo = EndUserRepository(db)
        if end_user_repo.get_active_end_user_in_workspace(
            end_user_uuid,
            workspace_id,
        ) is None:
            return None

        repo = MemoryEngineDisplayEventRepository(db)
        groups, total = repo.query_aggregated_paginated(
            end_user_id=end_user_id,
            workspace_id=str(workspace_id),
            timezone=timezone,
            page=page,
            pagesize=pagesize,
        )
        cards = MemoryEngineDisplayService.build_cards_from_groups(
            groups=groups,
            end_user_id=end_user_id,
            timezone=timezone,
            language=language,
        )
        return cards, total

    # ──────────────────────────────────────────────
    # 写入侧：从 ExtractionResult 组装事件
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
        from app.db import get_db_context
        from app.models.memory_engine_display_event_model import MemoryEngineDisplayEvent

        try:
            # 组装事件
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

            # 生成共享标识
            operation_id = uuid.uuid4()
            occurred_at = utcnow_naive()

            # 转换为 ORM 实例
            # end_user_id 需要转为 UUID 对象（PG 外键是 UUID 类型）
            try:
                user_uuid = uuid.UUID(end_user_id)
            except (ValueError, AttributeError):
                logger.warning(
                    f"[EngineDisplay] 无法将 end_user_id 转为 UUID: {end_user_id}"
                )
                return

            records = []
            for data in events_data:
                record = MemoryEngineDisplayEvent(
                    id=uuid.uuid4(),
                    end_user_id=user_uuid,
                    operation_id=operation_id,
                    engine_type=data["engine_type"],
                    details=data["details"],
                    occurred_at=occurred_at,
                )
                records.append(record)

            # PG 写入（一次，不重试）
            with get_db_context() as db:
                repo = MemoryEngineDisplayEventRepository(db)
                repo.bulk_insert_events(records)

            logger.info(
                f"[EngineDisplay] PG 写入成功: end_user_id={end_user_id}, "
                f"operation_id={operation_id}, engines={[d['engine_type'] for d in events_data]}"
            )

        except Exception as e:
            logger.error(
                f"[EngineDisplay] PG 写入失败: end_user_id={end_user_id}, error={e}",
                exc_info=True,
            )

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

        # 按出现次数排序，取前3
        sorted_topics = sorted(topic_counter.items(), key=lambda x: (-x[1], x[0]))
        topics = [t[0] for t in sorted_topics[:3]]

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

        # 主题排序：事件数降序 > 最后出现时间降序 > 名称升序
        sorted_topics = sorted(
            topic_info.items(),
            key=lambda x: (-x[1]["count"], -(x[1]["last_at"].timestamp() if x[1]["last_at"] else 0), x[0]),
        )
        topics = [t[0] for t in sorted_topics[:3]]

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

    return ("", "")


def _pluralize(noun: str, count: int) -> str:
    """Apply the regular English plural form used by card metrics."""
    return noun if count == 1 else f"{noun}s"


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
