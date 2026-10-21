"""Pure card formatting and aggregation logic for memory engine events."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from src.common.normalization import datetime_to_epoch_ms

_REFLECTION_COUNT_KEYS = (
    "entity_merged_count",
    "alias_merged_count",
    "description_merged_count",
    "metadata_extracted_count",
    "unresolved_resolved_count",
)

_EMOTION_TYPE_LABELS: dict[str, dict[str, str]] = {
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


def _as_count(value: Any) -> int:
    try:
        count = int(value or 0)
        return max(count, 0)
    except (TypeError, ValueError):
        return 0


def _generate_card_id(
    end_user_id: str | None,
    engine_type: str,
    timezone: str,
    local_date: date,
) -> str:
    """Generate a deterministic UUIDv5 for an aggregated card."""
    name = f"{end_user_id or ''}:{engine_type}:{timezone}:{local_date.isoformat()}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, name))


def _merge_event_details(
    engine_type: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Merge details of events belonging to the same aggregation group."""
    if engine_type == "EXTRACTION":
        total_statements = 0
        total_entities = 0
        total_relations = 0
        for e in events:
            d = e.get("details") or {}
            total_statements += d.get("statement_count", 0)
            total_entities += d.get("entity_count", 0)
            total_relations += d.get("relation_count", 0)
        return {
            "statement_count": total_statements,
            "entity_count": total_entities,
            "relation_count": total_relations,
        }

    if engine_type == "CROSS_MODAL":
        merged_modality: dict[str, int] = defaultdict(int)
        total_chunk_assoc = 0
        total_entity_assoc = 0
        topic_info: dict[str, dict[str, Any]] = {}

        for e in events:
            d = e.get("details") or {}
            occurred_at = e.get("occurred_at")
            mc = d.get("modality_counts", {})
            for k, v in mc.items():
                merged_modality[k] += v
            total_chunk_assoc += d.get("chunk_association_count", 0)
            total_entity_assoc += d.get("entity_association_count", 0)

            for topic in d.get("topics", []):
                normalized = topic.strip()
                if normalized:
                    if normalized not in topic_info:
                        topic_info[normalized] = {"count": 0, "last_at": occurred_at}
                    topic_info[normalized]["count"] += 1
                    if occurred_at and occurred_at > topic_info[normalized]["last_at"]:
                        topic_info[normalized]["last_at"] = occurred_at

        sorted_topics = sorted(
            topic_info.items(),
            key=lambda x: (
                -x[1]["count"],
                -(x[1]["last_at"].timestamp() if x[1]["last_at"] else 0),
                x[0],
            ),
        )
        topics = [t[0] for t in sorted_topics]

        return {
            "modality_counts": dict(merged_modality),
            "chunk_association_count": total_chunk_assoc,
            "entity_association_count": total_entity_assoc,
            "topics": topics,
        }

    if engine_type == "EMOTION":
        merged_emotions: dict[str, dict[str, Any]] = {}

        for e in events:
            d = e.get("details") or {}
            occurred_at = e.get("occurred_at")
            stats = d.get("emotion_stats", {})
            for etype, info in stats.items():
                if etype not in merged_emotions:
                    merged_emotions[etype] = {
                        "count": 0,
                        "intensity_sum": 0.0,
                        "last_at": occurred_at,
                    }
                merged_emotions[etype]["count"] += info.get("count", 0)
                merged_emotions[etype]["intensity_sum"] += info.get("intensity_sum", 0.0)
                if occurred_at and occurred_at > merged_emotions[etype]["last_at"]:
                    merged_emotions[etype]["last_at"] = occurred_at

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

    if engine_type == "FORGETTING":
        scanned = 0
        released = 0
        node_types: dict[str, int] = defaultdict(int)
        for e in events:
            d = e.get("details") or {}
            scanned += _as_count(d.get("scanned_count"))
            released += _as_count(d.get("released_count"))
            for k, v in (d.get("node_type_counts") or {}).items():
                node_types[str(k)] += _as_count(v)
        return {
            "scanned_count": scanned,
            "released_count": released,
            "node_type_counts": dict(node_types),
        }

    if engine_type == "REFLECTION":
        merged_counts = {key: 0 for key in _REFLECTION_COUNT_KEYS}
        for e in events:
            d = e.get("details") or {}
            for key in _REFLECTION_COUNT_KEYS:
                merged_counts[key] += _as_count(d.get(key))
        return merged_counts

    if engine_type == "SCENE_SUMMARY":
        latest_by_scene: dict[str, dict[str, Any]] = {}
        for e in events:
            d = e.get("details") or {}
            occurred_at = e.get("occurred_at")
            scene_id = d.get("scene_summary_id")
            if not scene_id:
                continue
            current = latest_by_scene.get(str(scene_id))
            if current is None or (
                occurred_at is not None and occurred_at > current["_last_at"]
            ):
                latest_by_scene[str(scene_id)] = {
                    "turn_count": _as_count(d.get("turn_count")),
                    "source_message_count": _as_count(d.get("source_message_count")),
                    "_last_at": occurred_at,
                }

        return {
            "scene_count": len(latest_by_scene),
            "turn_count": sum(v["turn_count"] for v in latest_by_scene.values()),
            "source_message_count": sum(
                v["source_message_count"] for v in latest_by_scene.values()
            ),
        }

    if engine_type == "MEMORY_VALUE":
        permanent_added_count = 0
        evaluation_count = 0
        evaluated_node_count = 0
        statement_count = 0
        for e in sorted(
            events,
            key=lambda item: item.get("occurred_at") or datetime.min,
            reverse=True,
        ):
            d = e.get("details") or {}
            activity_type = d.get("activity_type")
            if activity_type == "PERMANENT_ADDED":
                permanent_added_count += _as_count(d.get("permanent_added_count"))
            elif activity_type == "VALUE_EVALUATED":
                evaluation_count += 1
                if evaluation_count == 1:
                    evaluated_node_count = _as_count(d.get("evaluated_node_count"))
                    statement_count = _as_count(d.get("statement_count"))

        return {
            "permanent_added_count": permanent_added_count,
            "evaluation_count": evaluation_count,
            "evaluated_node_count": evaluated_node_count,
            "statement_count": statement_count,
        }

    return {}


def _pluralize(noun: str, count: int, plural: str | None = None) -> str:
    if count == 1:
        return noun
    return plural if plural is not None else f"{noun}s"


def _join_english(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _format_english_modality(modality_type: str, count: int) -> str:
    labels = {
        "image": "image",
        "video": "video",
        "audio": "audio file",
        "document": "document",
    }
    label = labels.get(modality_type, modality_type)
    return f"{count} {_pluralize(label, count)}"


def _rank_reflection_counts(merged: dict[str, Any]) -> list[tuple[str, int]]:
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


def _generate_card_text_zh(engine_type: str, merged: dict[str, Any]) -> tuple[str, str]:
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

    if engine_type == "CROSS_MODAL":
        modality_counts = merged.get("modality_counts", {})
        entity_assoc = merged.get("entity_association_count", 0)
        topics = merged.get("topics", [])

        modality_parts = []
        modality_zh = {"image": "图片", "video": "视频", "audio": "音频", "document": "文档"}
        for mtype, count in modality_counts.items():
            label = modality_zh.get(mtype, mtype)
            modality_parts.append(f"{count} {'张' if mtype == 'image' else '份'}{label}")

        modality_desc = "和".join(modality_parts) if modality_parts else "跨模态内容"

        if entity_assoc > 0 and topics:
            name = "我把不同形式的记忆联系了起来"
            topic_str = "和".join(f"「{t}」" for t in topics)
            content = (
                f"识别了你分享的{modality_desc}，"
                f"并进一步将它们与{topic_str}等内容建立了关联。"
            )
        else:
            name = "我把跨模态内容和交流联系了起来"
            content = f"识别了你分享的{modality_desc}，并将它们与相关交流内容建立了联系。"

        return name, content

    if engine_type == "EMOTION":
        name = "我留意了交流中的情绪变化"
        emotions = merged.get("emotions", [])
        if not emotions:
            content = "这一天的交流中，我留意到了情绪表达。"
        else:
            emotion_names = [
                _EMOTION_TYPE_LABELS["zh"].get(em["type"], em["type"])
                for em in emotions
            ]
            emotion_str = "和".join(emotion_names)
            content = f"这一天的交流中，我留意到了{emotion_str}的情绪表达。"
        return name, content

    if engine_type == "FORGETTING":
        name = "我整理了不再活跃的记忆"
        scanned = merged.get("scanned_count", 0)
        released = merged.get("released_count", 0)
        if scanned > released:
            head = f"评估了 {scanned} 条较早写入的记录，将其中 {released} 条移出了活跃记忆"
        else:
            head = f"将 {released} 条记录移出了活跃记忆"
        content = f"{head}；情景摘要不参与整理，高频引用的实体受到保护。"
        return name, content

    if engine_type == "REFLECTION":
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

    if engine_type == "SCENE_SUMMARY":
        name = "我把完整的交流整理成了长期记忆"
        scene_count = _as_count(merged.get("scene_count"))
        turn_count = _as_count(merged.get("turn_count"))
        if scene_count <= 0:
            return ("", "")
        if scene_count == 1:
            content = "这一天的 1 个完整交流场景已整理成了长期记忆，方便以后更连贯地回忆。"
        else:
            content = (
                f"这一天整理了 {scene_count} 个完整交流场景，"
                f"覆盖 {turn_count} 轮交流，方便以后更连贯地回忆。"
            )
        return name, content

    if engine_type == "MEMORY_VALUE":
        added = _as_count(merged.get("permanent_added_count"))
        evaluations = _as_count(merged.get("evaluation_count"))
        covered = _as_count(merged.get("evaluated_node_count"))
        if added <= 0 and evaluations <= 0:
            return ("", "")

        if added > 0 and evaluations <= 0:
            name = "我保留了值得长期记住的信息"
            content = f"这一天新增了 {added} 条永久记忆，我会长期保留这些重要信息。"
        elif added <= 0:
            name = "我重新整理了记忆的重要程度"
            content = f"这一天完成了 {evaluations} 次价值评估；最近一次覆盖 {covered} 项记忆内容。"
        else:
            name = "我重新整理了记忆的重要程度"
            if evaluations == 1:
                content = (
                    f"这一天新增了 {added} 条永久记忆，"
                    f"并重新评估了 {covered} 项记忆内容的重要程度。"
                )
            else:
                content = (
                    f"这一天新增了 {added} 条永久记忆，"
                    f"完成了 {evaluations} 次价值评估；最近一次覆盖 {covered} 项记忆内容。"
                )
        return name, content

    return ("", "")


def _generate_card_text_en(engine_type: str, merged: dict[str, Any]) -> tuple[str, str]:
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
            topic_desc = _join_english([f'"{topic}"' for topic in topics])
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

    if engine_type == "SCENE_SUMMARY":
        name = "I turned complete conversations into long-term memories"
        scene_count = _as_count(merged.get("scene_count"))
        turn_count = _as_count(merged.get("turn_count"))
        if scene_count <= 0:
            return ("", "")
        if scene_count == 1:
            content = (
                "I turned the 1 complete conversation from that day into a "
                "long-term memory so I can recall it more coherently later."
            )
        else:
            content = (
                f"I organized {scene_count} complete conversations that day, "
                f"covering {turn_count} conversation "
                f"{_pluralize('turn', turn_count)}, so I can recall them more "
                "coherently later."
            )
        return name, content

    if engine_type == "MEMORY_VALUE":
        added = _as_count(merged.get("permanent_added_count"))
        evaluations = _as_count(merged.get("evaluation_count"))
        covered = _as_count(merged.get("evaluated_node_count"))
        if added <= 0 and evaluations <= 0:
            return ("", "")

        added_phrase = (
            f"added {added} permanent "
            f"{_pluralize('memory', added, 'memories')}"
        )
        if added > 0 and evaluations <= 0:
            name = "I kept information worth remembering long-term"
            content = (
                f"I {added_phrase} that day, and I will keep this important "
                "information for the long term."
            )
        elif added <= 0:
            name = "I re-evaluated how important my memories are"
            content = (
                f"I completed {evaluations} value "
                f"{_pluralize('evaluation', evaluations)} that day; the most "
                f"recent one covered {covered} memory "
                f"{_pluralize('item', covered)}."
            )
        else:
            name = "I re-evaluated how important my memories are"
            if evaluations == 1:
                content = (
                    f"I {added_phrase} that day and re-evaluated how important "
                    f"{covered} memory {_pluralize('item', covered)} are."
                )
            else:
                content = (
                    f"I {added_phrase} that day and completed {evaluations} value "
                    f"{_pluralize('evaluation', evaluations)}; the most recent one "
                    f"covered {covered} memory {_pluralize('item', covered)}."
                )
        return name, content

    return ("", "")


def generate_card_text(
    engine_type: str,
    merged: dict[str, Any],
    language: str,
) -> tuple[str, str]:
    if language == "en":
        return _generate_card_text_en(engine_type, merged)
    return _generate_card_text_zh(engine_type, merged)


def build_cards_from_groups(
    groups: list[dict[str, Any]],
    end_user_id: str | None,
    timezone: str,
    language: str,
) -> list[dict[str, Any]]:
    cards = []
    for group in groups:
        engine_type = group["engine_type"]
        local_date = group["local_date"]
        max_occurred_at = group["max_occurred_at"]
        events = group["events"]

        group_end_user_id = group.get("end_user_id")
        card_end_user_id = (
            str(group_end_user_id) if group_end_user_id is not None else end_user_id
        )

        card_id = _generate_card_id(card_end_user_id, engine_type, timezone, local_date)
        merged = _merge_event_details(engine_type, events)
        name, content = generate_card_text(engine_type, merged, language)

        if max_occurred_at is not None:
            ms = datetime_to_epoch_ms(max_occurred_at)
        else:
            ms = 0
        cards.append({
            "id": card_id,
            "end_user_id": card_end_user_id,
            "engine_type": engine_type,
            "name": name,
            "content": content,
            "occurred_at": ms,
        })

    return cards
