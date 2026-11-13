"""Pure API-compatible aggregation and localization of persisted engine events."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from src.common.normalization import datetime_to_epoch_ms
from src.services.display.emotions import (
    EMOTION_NAMES_EN,
    EMOTION_NAMES_ZH,
    normalize_emotion_type_or_raw,
)

_REFLECTION_COUNT_KEYS = (
    "entity_merged_count",
    "alias_merged_count",
    "description_merged_count",
    "metadata_extracted_count",
    "unresolved_resolved_count",
)


def _emotion_label(code: str, lang: str) -> str:
    """引擎卡片情绪文案：zh 用中文名，en 用小写英文名；未知 code 原样返回。"""
    if lang == "en":
        name = EMOTION_NAMES_EN.get(code)
        return name.lower() if name is not None else code
    return EMOTION_NAMES_ZH.get(code, code)


def _as_count(value: Any) -> int:
    """把任意来源的计数安全转为非负 int；无法转换按 0。"""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


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
) -> dict[str, Any]:
    """合并同一聚合组内所有事件的 details。"""
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

    elif engine_type == "CROSS_MODAL":
        merged_modality: dict[str, int] = defaultdict(int)
        total_chunk_assoc = 0
        total_entity_assoc = 0
        # topic → {count, last_occurred_at}
        topic_info: dict[str, dict[str, Any]] = {}

        for e in events:
            d = e.get("details") or {}
            mc = d.get("modality_counts", {})
            for k, v in mc.items():
                merged_modality[k] += v
            total_chunk_assoc += d.get("chunk_association_count", 0)
            total_entity_assoc += d.get("entity_association_count", 0)

            for topic in d.get("topics", []):
                normalized = topic.strip()
                if normalized:
                    if normalized not in topic_info:
                        topic_info[normalized] = {"count": 0, "last_at": e.get("occurred_at")}
                    topic_info[normalized]["count"] += 1
                    if (
                        e.get("occurred_at")
                        and e.get("occurred_at") > topic_info[normalized]["last_at"]
                    ):
                        topic_info[normalized]["last_at"] = e.get("occurred_at")

        # 主题排序：事件数降序 > 最后出现时间降序 > 名称升序，完整展示
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

    elif engine_type == "EMOTION":
        # emotion_type → {count, intensity_sum, last_at}
        merged_emotions: dict[str, dict[str, Any]] = {}

        for e in events:
            d = e.get("details") or {}
            stats = d.get("emotion_stats", {})
            for raw_etype, info in stats.items():
                # 存量旧值归一化（fear→anxiety、surprise→neutral）后同 code 合并；
                # 归一化为 neutral 的跳过（卡片排除中性），未知原值保留
                etype = normalize_emotion_type_or_raw(raw_etype)
                if etype == "neutral":
                    continue
                if etype not in merged_emotions:
                    merged_emotions[etype] = {
                        "count": 0,
                        "intensity_sum": 0.0,
                        "last_at": e.get("occurred_at"),
                    }
                merged_emotions[etype]["count"] += info.get("count", 0)
                merged_emotions[etype]["intensity_sum"] += info.get("intensity_sum", 0.0)
                if (
                    e.get("occurred_at")
                    and e.get("occurred_at") > merged_emotions[etype]["last_at"]
                ):
                    merged_emotions[etype]["last_at"] = e.get("occurred_at")

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

    elif engine_type == "REFLECTION":
        # 高频巡检和每日全量去重的成果在同一天相加，卡片不区分 scan_type
        merged_counts = {key: 0 for key in _REFLECTION_COUNT_KEYS}
        for e in events:
            d = e.get("details") or {}
            for key in _REFLECTION_COUNT_KEYS:
                merged_counts[key] += _as_count(d.get(key))
        return merged_counts

    elif engine_type == "SCENE_SUMMARY":
        # 按 scene_summary_id 去重，只取当天 occurred_at 最新的版本；
        # scene_count 为不同场景数，turn_count 对各场景当天最后版本求和。
        latest_by_scene: dict[str, dict[str, Any]] = {}
        for e in events:
            d = e.get("details") or {}
            scene_id = d.get("scene_summary_id")
            if not scene_id:
                continue
            current = latest_by_scene.get(str(scene_id))
            if current is None or (
                e.get("occurred_at") is not None and e.get("occurred_at") > current["_last_at"]
            ):
                latest_by_scene[str(scene_id)] = {
                    "turn_count": _as_count(d.get("turn_count")),
                    "source_message_count": _as_count(d.get("source_message_count")),
                    "_last_at": e.get("occurred_at"),
                }

        return {
            "scene_count": len(latest_by_scene),
            "turn_count": sum(v["turn_count"] for v in latest_by_scene.values()),
            "source_message_count": sum(
                v["source_message_count"] for v in latest_by_scene.values()
            ),
        }

    elif engine_type == "MEMORY_VALUE":
        # 两种子类型按 activity_type 分流：
        # 永久记忆增量求和，评估次数计数，覆盖数只取最近一次（不求和）。
        # 事件按 occurred_at 倒序迭代，第一条 VALUE_EVALUATED 即最近一次。
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

    elif engine_type == "PREFERENCE":
        # 原子项级计数对当天所有事件求和；合计为 0 的事件在组装侧已被过滤。
        added = 0
        updated = 0
        deleted = 0
        for e in events:
            d = e.get("details") or {}
            added += _as_count(d.get("added_item_count"))
            updated += _as_count(d.get("updated_item_count"))
            deleted += _as_count(d.get("deleted_item_count"))
        return {
            "added_item_count": added,
            "updated_item_count": updated,
            "deleted_item_count": deleted,
        }

    elif engine_type == "SCENE_RECONSTRUCTION":
        # 计数对当天所有成功批次求和；同一已有社区当天多批更新按次数累计。
        processed = 0
        new_communities = 0
        existing_updates = 0
        boundary_established = 0
        summary_refreshed = 0
        for e in events:
            d = e.get("details") or {}
            processed += _as_count(d.get("processed_scene_count"))
            new_communities += _as_count(d.get("new_community_count"))
            existing_updates += _as_count(d.get("existing_community_update_count"))
            boundary_established += _as_count(d.get("boundary_established_count"))
            summary_refreshed += _as_count(d.get("summary_refreshed_count"))
        return {
            "processed_scene_count": processed,
            "new_community_count": new_communities,
            "existing_community_update_count": existing_updates,
            "boundary_established_count": boundary_established,
            "summary_refreshed_count": summary_refreshed,
        }

    elif engine_type == "PREDICTION":
        # 推演次数计数、预测条数求和；轮次/Agent 数/依据记忆数只取最近一次
        # （事件按 occurred_at 倒序迭代，第一条即最近一次）——每次推演是
        # 独立完整的模拟，覆盖面不能跨运行累加。
        prediction_runs = 0
        prediction_count = 0
        round_count = 0
        agent_count = 0
        grounded_memory_count = 0
        for e in sorted(
            events,
            key=lambda item: item.get("occurred_at") or datetime.min,
            reverse=True,
        ):
            prediction_runs += 1
            d = e.get("details") or {}
            prediction_count += _as_count(d.get("prediction_count"))
            if prediction_runs == 1:
                round_count = _as_count(d.get("round_count"))
                agent_count = _as_count(d.get("agent_count"))
                grounded_memory_count = _as_count(d.get("grounded_memory_count"))

        return {
            "prediction_runs": prediction_runs,
            "prediction_count": prediction_count,
            "round_count": round_count,
            "agent_count": agent_count,
            "grounded_memory_count": grounded_memory_count,
        }

    elif engine_type == "PERSONALITY_ANCHOR":
        return {
            "candidate_count": sum(
                _as_count((event.get("details") or {}).get("candidate_count")) for event in events
            )
        }

    return {}


def generate_card_text(
    engine_type: str,
    merged: dict[str, Any],
    language: str,
) -> tuple:
    """根据聚合后的 details 生成本地化 name 和 content。"""
    if language == "en":
        return _generate_card_text_en(engine_type, merged)

    return _generate_card_text_zh(engine_type, merged)


def _generate_card_text_zh(
    engine_type: str,
    merged: dict[str, Any],
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
            content = (
                f"识别了你分享的{modality_desc}，并进一步将它们与{topic_str}等内容建立了关联。"
            )
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
                zh_name = _emotion_label(etype, "zh")
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
        parts = [labels[key].format(n=count) for key, count in _rank_reflection_counts(merged)]
        if not parts:
            content = ""
        elif len(parts) == 1:
            content = parts[0] + "。"
        else:
            content = "，".join(parts[:-1]) + "，并" + parts[-1] + "。"
        return name, content

    elif engine_type == "SCENE_SUMMARY":
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

    elif engine_type == "MEMORY_VALUE":
        added = _as_count(merged.get("permanent_added_count"))
        evaluations = _as_count(merged.get("evaluation_count"))
        covered = _as_count(merged.get("evaluated_node_count"))
        if added <= 0 and evaluations <= 0:
            return ("", "")

        if added > 0 and evaluations <= 0:
            # 只有新增永久记忆，不编造覆盖数量
            name = "我保留了值得长期记住的信息"
            content = f"这一天新增了 {added} 条永久记忆，我会长期保留这些重要信息。"
        elif added <= 0:
            # 只有价值评估，省略新增分句
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

    elif engine_type == "PREFERENCE":
        added = _as_count(merged.get("added_item_count"))
        updated = _as_count(merged.get("updated_item_count"))
        deleted = _as_count(merged.get("deleted_item_count"))
        if added <= 0 and updated <= 0 and deleted <= 0:
            return ("", "")

        if added > 0 and updated <= 0 and deleted <= 0:
            # 只有新增
            name = "我记住了新的使用偏好"
            content = f"这一天新记住了 {added} 条长期偏好，之后会在适用场景中作为参考。"
            return name, content

        name = "我更新了对你偏好的理解"
        if added <= 0 and deleted <= 0:
            # 只有调整
            content = f"这一天调整了 {updated} 条长期偏好，让偏好记录更符合你当前表达的要求。"
        elif added <= 0 and updated <= 0:
            # 只有移除
            content = f"这一天移除了 {deleted} 条不再适用的长期偏好记录。"
        else:
            # 多种变化并存：按非零项动态拼接，不输出“0 条”，也不漏报任何变化
            parts = []
            if added > 0:
                parts.append(f"新记住了 {added} 条长期偏好")
            if updated > 0:
                parts.append(f"调整了 {updated} 条")
            if deleted > 0:
                parts.append(f"移除了 {deleted} 条不再适用的记录")
            if len(parts) == 2:
                content = "这一天" + parts[0] + "，并" + parts[1] + "。"
            else:
                content = "这一天" + "，".join(parts[:-1]) + "，并" + parts[-1] + "。"
        return name, content

    elif engine_type == "SCENE_RECONSTRUCTION":
        processed = _as_count(merged.get("processed_scene_count"))
        new_communities = _as_count(merged.get("new_community_count"))
        existing_updates = _as_count(merged.get("existing_community_update_count"))
        if processed <= 0 or (new_communities <= 0 and existing_updates <= 0):
            return ("", "")

        name = "我把分散的情景记忆重新串联了起来"
        if new_communities > 0 and existing_updates <= 0:
            # 只有新建事项脉络
            content = (
                f"这一天将 {processed} 段完整情景整理为 {new_communities} 个新的事项脉络，"
                "方便以后按同一件事连贯回忆。"
            )
        elif new_communities <= 0:
            # 只有已有脉络更新，按次数表述（同一天同一脉络可多次更新）
            content = (
                f"这一天整理了 {processed} 段完整情景，"
                f"并完成了 {existing_updates} 次已有事项脉络更新，让相关记忆更连贯。"
            )
        else:
            content = (
                f"这一天整理了 {processed} 段完整情景，"
                f"形成 {new_communities} 个新事项脉络，"
                f"并完成 {existing_updates} 次已有脉络更新。"
            )
        return name, content

    elif engine_type == "PREDICTION":
        runs = _as_count(merged.get("prediction_runs"))
        predictions = _as_count(merged.get("prediction_count"))
        rounds = _as_count(merged.get("round_count"))
        grounded = _as_count(merged.get("grounded_memory_count"))
        if runs <= 0 or predictions <= 0:
            return ("", "")

        name = "我基于记忆推演了未来可能的走向"
        if runs == 1:
            # 单次推演
            content = (
                f"这一天完成了 1 次预测推演，形成了 {predictions} 条预测，"
                f"模拟了 {rounds} 轮情景演化。"
            )
        else:
            # 多次推演：预测条数求和；轮次与依据记忆数只取最近一次
            content = (
                f"这一天完成了 {runs} 次预测推演，共形成 {predictions} 条预测；"
                f"最近一次模拟了 {rounds} 轮情景、结合了 {grounded} 条历史记忆。"
            )
        return name, content

    elif engine_type == "PERSONALITY_ANCHOR":
        count = merged["candidate_count"]
        return "我生成了待确认的人格候选", f"这一天共生成了 {count} 条人格候选，供你确认。"

    return ("", "")


def _generate_card_text_en(
    engine_type: str,
    merged: dict[str, Any],
) -> tuple[str, str]:
    """Generate English card copy."""
    if engine_type == "EXTRACTION":
        name = "I organized important information from our conversations"
        stmt_count = merged.get("statement_count", 0)
        entity_count = merged.get("entity_count", 0)
        relation_count = merged.get("relation_count", 0)

        parts = []
        if stmt_count > 0:
            parts.append(f"extracted {stmt_count} {_pluralize('piece', stmt_count)} of information")
        if entity_count > 0:
            parts.append(f"identified {entity_count} key {_pluralize('item', entity_count)}")
        if relation_count > 0:
            parts.append(
                f"established {relation_count} content {_pluralize('relationship', relation_count)}"
            )

        content = (
            "From the conversations that day, I " + _join_english(parts) + "." if parts else ""
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
            emotion_names = [_emotion_label(emotion["type"], "en") for emotion in emotions]
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
            head = f"I moved {released} {_pluralize('record', released)} out of active memory"
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
                f"consolidated scattered descriptions for {n} {_pluralize('entity', n, 'entities')}"
            ),
            "metadata_extracted_count": lambda n: (
                f"filled in structured information for {n} {_pluralize('entity', n, 'entities')}"
            ),
            "unresolved_resolved_count": lambda n: (
                f"resolved {n} previously unrecognized {_pluralize('statement', n)}"
            ),
        }
        parts = [labels[key](count) for key, count in _rank_reflection_counts(merged)]
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

        added_phrase = f"added {added} permanent {_pluralize('memory', added, 'memories')}"
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

    if engine_type == "PREFERENCE":
        added = _as_count(merged.get("added_item_count"))
        updated = _as_count(merged.get("updated_item_count"))
        deleted = _as_count(merged.get("deleted_item_count"))
        if added <= 0 and updated <= 0 and deleted <= 0:
            return ("", "")

        if added > 0 and updated <= 0 and deleted <= 0:
            name = "I learned new preferences about you"
            content = (
                f"I noted {added} new long-term "
                f"{_pluralize('preference', added, 'preferences')} that day, and I "
                "will use them as a reference in relevant situations."
            )
            return name, content

        name = "I updated my understanding of your preferences"
        if added <= 0 and deleted <= 0:
            content = (
                f"I adjusted {updated} long-term "
                f"{_pluralize('preference', updated, 'preferences')} that day to "
                "better match what you have expressed."
            )
        elif added <= 0 and updated <= 0:
            removed_verb = "applies" if deleted == 1 else "apply"
            content = (
                f"I removed {deleted} long-term preference "
                f"{_pluralize('record', deleted)} that no longer {removed_verb}."
            )
        else:
            # Multiple changes: join only the non-zero parts to avoid "0" counts
            parts = []
            if added > 0:
                parts.append(
                    f"noted {added} new long-term {_pluralize('preference', added, 'preferences')}"
                )
            if updated > 0:
                parts.append(f"adjusted {updated} of them")
            if deleted > 0:
                removed_verb = "applies" if deleted == 1 else "apply"
                parts.append(f"removed {deleted} that no longer {removed_verb}")
            content = f"I {_join_english(parts)} that day."
        return name, content

    if engine_type == "SCENE_RECONSTRUCTION":
        processed = _as_count(merged.get("processed_scene_count"))
        new_communities = _as_count(merged.get("new_community_count"))
        existing_updates = _as_count(merged.get("existing_community_update_count"))
        if processed <= 0 or (new_communities <= 0 and existing_updates <= 0):
            return ("", "")

        name = "I reconnected scattered episodic memories"
        if new_communities > 0 and existing_updates <= 0:
            content = (
                f"I organized {processed} complete "
                f"{_pluralize('scene', processed)} into {new_communities} new "
                f"{_pluralize('topic thread', new_communities, 'topic threads')} "
                "that day, so related memories stay connected."
            )
        elif new_communities <= 0:
            content = (
                f"I organized {processed} complete "
                f"{_pluralize('scene', processed)} that day and made "
                f"{existing_updates} "
                f"{_pluralize('update', existing_updates)} to existing topic "
                "threads to keep related memories connected."
            )
        else:
            content = (
                f"I organized {processed} complete "
                f"{_pluralize('scene', processed)} that day, forming "
                f"{new_communities} new "
                f"{_pluralize('topic thread', new_communities, 'topic threads')} "
                f"and making {existing_updates} "
                f"{_pluralize('update', existing_updates)} to existing ones."
            )
        return name, content

    if engine_type == "PREDICTION":
        runs = _as_count(merged.get("prediction_runs"))
        predictions = _as_count(merged.get("prediction_count"))
        rounds = _as_count(merged.get("round_count"))
        grounded = _as_count(merged.get("grounded_memory_count"))
        if runs <= 0 or predictions <= 0:
            return ("", "")

        name = "I explored where your memories might lead"
        if runs == 1:
            content = (
                f"I completed 1 prediction run that day, forming "
                f"{predictions} {_pluralize('prediction', predictions)} from "
                f"{rounds} {_pluralize('round', rounds)} of simulated "
                "scenarios."
            )
        else:
            content = (
                f"I completed {runs} prediction "
                f"{_pluralize('run', runs)} that day, forming {predictions} "
                f"{_pluralize('prediction', predictions)} in total; the most "
                f"recent one simulated {rounds} "
                f"{_pluralize('round', rounds)} and drew on {grounded} "
                f"{_pluralize('memory', grounded, 'memories')}."
            )
        return name, content

    if engine_type == "PERSONALITY_ANCHOR":
        count = merged["candidate_count"]
        return (
            "I generated personality candidates for review",
            f"I generated {count} personality {_pluralize('candidate', count)}"
            " for review that day.",
        )

    return ("", "")


def _rank_reflection_counts(merged: dict[str, Any]) -> list[tuple]:
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


def _join_english(parts: list[str]) -> str:
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
        card_end_user_id = str(group_end_user_id) if group_end_user_id is not None else end_user_id

        card_id = _generate_card_id(card_end_user_id, engine_type, timezone, local_date)
        merged = _merge_event_details(engine_type, events)
        name, content = generate_card_text(engine_type, merged, language)

        if max_occurred_at is not None:
            ms = datetime_to_epoch_ms(max_occurred_at)
        else:
            ms = 0
        cards.append(
            {
                "id": card_id,
                "end_user_id": card_end_user_id,
                "engine_type": engine_type,
                "name": name,
                "content": content,
                "occurred_at": ms,
            }
        )

    return cards
