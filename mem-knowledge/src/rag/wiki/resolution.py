"""同名或明确别名的 Wiki 类型冲突只比较当前两组证据。"""

import logging

from ...tasks.observability import current_parent_task_id
from .config import normalize_name, source_reference_key
from .contracts import LocalIdentityDecision
from .identity import merge_candidate
from .ontology import ONTOLOGY_VERSION

logger = logging.getLogger(__name__)
LOCAL_IDENTITY_STAGE = "wiki_local_identity_v1"


def names_of(topic):
    return {
        normalize_name(name) for name in [topic["name"], *topic["aliases"]] if name.strip()
    }


def types_conflict(left, right):
    left_type, right_type = left.get("semantic_type"), right.get("semantic_type")
    if left_type and right_type:
        return left_type != right_type
    return left["kind"] != right["kind"]


def _conflict_side(topic):
    evidence = list(dict.fromkeys(
        item.get("context") or item["quote"] for item in topic.get("type_evidence", [])
    ))[:4]
    return {
        "name": topic["name"],
        "aliases": topic["aliases"][:8],
        "type": topic.get("semantic_type") or topic["kind"],
        "existing_page": "page_id" in topic,
        "context": topic.get("description", "")[:300],
        "evidence": evidence,
    }


def _qualifier(label, side):
    if not label:
        return None
    label = label.strip()
    if not label or any(char in label for char in "\r\n()（）"):
        return None
    return label if any(label in quote for quote in side["evidence"]) else None


def decide(model, snapshot, left, right):
    sides = {"left": _conflict_side(left), "right": _conflict_side(right)}
    result = model.call(
        LOCAL_IDENTITY_STAGE, {**sides, "language": snapshot["language"]},
        LocalIdentityDecision, repair_attempts=0,
    )
    if result["decision"] == "different_objects":
        for key in ("left", "right"):
            result[f"{key}_label"] = _qualifier(result.get(f"{key}_label"), sides[key])
        if (
            not result["right_label"]
            or ("page_id" not in left and not result["left_label"])
            or (result["left_label"] and result["left_label"] == result["right_label"])
        ):
            result["decision"] = "insufficient"
    logger.info(
        "Wiki 局部消歧 task_id=%s kb_id=%s document_id=%s left=%s right=%s page_id=%s "
        "decision=%s preferred=%s",
        current_parent_task_id(), snapshot.get("kb_id"), snapshot.get("document_id"),
        left["name"], right["name"], left.get("page_id"), result["decision"],
        result.get("preferred"),
    )
    return result


def qualify(topic, label):
    base = topic.setdefault("original_name", topic["name"])
    topic["aliases"] = sorted(set([*topic["aliases"], base]))
    topic["name"] = topic["title"] = f"{base}（{label}）"
    topic["disambiguated"] = True


def resolve_candidates(model, snapshot, topics):
    resolved = []
    # S 编号由原文顺序分配，模型返回的并行完成顺序不参与取舍。
    for topic in sorted(
        topics, key=lambda candidate: min(map(source_reference_key, candidate["source_refs"])),
    ):
        for previous in resolved:
            if not types_conflict(previous, topic) or not (names_of(previous) & names_of(topic)):
                continue
            decision = decide(model, snapshot, previous, topic)
            if decision["decision"] == "different_objects":
                qualify(previous, decision["left_label"])
                qualify(topic, decision["right_label"])
                continue
            if decision["decision"] == "same_object" and decision.get("preferred") == "right":
                previous["semantic_type"] = topic["semantic_type"]
                previous["kind"] = topic["kind"]
            merge_candidate(previous, topic)
            previous.setdefault("merged_topic_refs", []).append(topic["topic_ref"])
            break
        else:
            resolved.append(topic)
    return resolved


def resolve_against_pages(model, snapshot, topic, pages):
    name = normalize_name(topic["name"])
    names = names_of(topic)
    exact = [page for page in pages if names & names_of(page)]
    direct = [page for page in exact if normalize_name(page["name"]) == name]
    typed = [page for page in exact if page.get("semantic_type") == topic["semantic_type"]]
    direct_typed = [page for page in direct if page in typed]
    if len(direct_typed) == 1 or len(typed) == 1:
        topic["existing_page_id"] = (direct_typed or typed)[0]["page_id"]
        return True
    if len(direct_typed) > 1 or len(typed) > 1:
        # 现有多页歧义规则负责定位旧页，避免任取一页。
        return False

    for page in direct or exact:
        if not types_conflict(page, topic):
            continue
        result = decide(model, snapshot, page, topic)
        if result["decision"] == "different_objects":
            qualify(topic, result["right_label"])
            continue
        topic["existing_page_id"] = page["page_id"]
        if result["decision"] == "same_object" and result.get("preferred") == "right":
            topic["correct_type"] = True
        else:
            topic["semantic_type"] = page.get("semantic_type")
            topic["kind"] = page["kind"]
        return True
    return topic.get("disambiguated", False)


def planned_ontology(candidate):
    if "semantic_type" not in candidate:
        return None
    return {
        "semantic_type": candidate["semantic_type"],
        "ontology_version": ONTOLOGY_VERSION if candidate["semantic_type"] else None,
        "kind": candidate["kind"],
        "correct_type": candidate.get("correct_type", False),
        "type_evidence": list(candidate.get("type_evidence", [])),
    }
