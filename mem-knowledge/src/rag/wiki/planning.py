"""Reuse known page identities and plan one coherent directory before persistence."""

from difflib import SequenceMatcher

from .batching import batches, optional_items
from .config import TYPE_GROUP_NAMES, normalize_name
from .contracts import FolderAssignments, IdentityMatches
from .model import request_tokens, tokens
from .pipeline import require_refs

IDENTITY_CANDIDATE_LIMIT = 5
IDENTITY_SIMILARITY_THRESHOLD = 0.5


def _names(topic):
    return {normalize_name(topic.get("name") or topic.get("title")),
            *(normalize_name(alias) for alias in topic.get("aliases", []))}


def reconcile(model, snapshot, topics, context):
    pages = context["pages"]
    cases, shortlists = [], {}
    for topic in topics:
        names = _names(topic)
        available = [p for p in pages if p["kind"] == topic["kind"]]
        exact = [p for p in available if names & _names(p)
                 and normalize_name(p["scope"]) == normalize_name(topic["scope"])]
        if len(exact) == 1:
            topic["existing_page_id"] = exact[0]["page_id"]
            continue
        scored = []
        for page in available:
            score = max(SequenceMatcher(None, name, old).ratio()
                        for name in names for old in _names(page))
            if score >= IDENTITY_SIMILARITY_THRESHOLD:
                scored.append((score, page))
        selected = [p for _, p in sorted(scored, key=lambda pair: -pair[0])[
            :IDENTITY_CANDIDATE_LIMIT
        ]]
        if not selected:
            continue
        choices = {f"E{index + 1}": page for index, page in enumerate(selected)}
        shortlists[topic["topic_ref"]] = choices
        cases.append({"candidate_ref": topic["topic_ref"], "name": topic["name"],
                      "kind": topic["kind"], "aliases": topic["aliases"], "scope": topic["scope"],
                      "candidates": [{"page_ref": ref, "title": p["name"],
                                      "aliases": p["aliases"], "scope": p["scope"]}
                                     for ref, p in choices.items()]})
    by_ref = {topic["topic_ref"]: topic for topic in topics}
    def build(values):
        return {"language": snapshot["language"], "items": values}
    for group in batches(cases, stage="wiki_identity_v2", build=build,
                         schema=IdentityMatches, settings=model.settings) if cases else []:
        refs = {case["candidate_ref"] for case in group}

        def validate(value, refs=refs):
            actual = [match["candidate_ref"] for match in value["matches"]]
            require_refs(actual, refs, complete=True)
            if len(actual) != len(set(actual)):
                raise ValueError("Duplicate identity decisions")
            for match in value["matches"]:
                if match["page_ref"] is not None:
                    require_refs([match["page_ref"]], shortlists[match["candidate_ref"]])

        result = model.call("wiki_identity_v2", build(group), IdentityMatches, validate)
        for match in result["matches"]:
            if match["page_ref"] is not None:
                by_ref[match["candidate_ref"]]["existing_page_id"] = (
                    shortlists[match["candidate_ref"]][match["page_ref"]]["page_id"]
                )
    return topics


TAXONOMY_DESCRIPTION_CHARS = 400
TAXONOMY_CONTEXT_MARGIN_TOKENS = 128
TAXONOMY_TYPE_LABELS = TYPE_GROUP_NAMES | {"entity", "concept", "实体", "概念"}


def _taxonomy_subject(topic):
    return {
        "name": topic.get("name") or topic.get("title"), "kind": topic["kind"],
        "scope": topic.get("scope", ""), "aliases": topic.get("aliases", []),
        "description": topic.get("description", "")[:TAXONOMY_DESCRIPTION_CHARS],
    }


def validate_category_path(path):
    if any(not name.strip() or len(name) > 200 or "/" in name or "\n" in name or "\r" in name
           or normalize_name(name) in TAXONOMY_TYPE_LABELS for name in path):
        raise ValueError("Invalid navigation category")


def taxonomy(model, snapshot, topics, context):
    known = {page["page_id"]: page for page in context["pages"]}
    pending = [topic for topic in topics
               if not known.get(topic.get("existing_page_id"), {}).get("folder_id")]
    if not pending:
        return topics
    by_ref = {topic["topic_ref"]: topic for topic in pending}
    items = [{"topic_ref": topic["topic_ref"], **_taxonomy_subject(topic)} for topic in pending]
    pending_ids = {topic.get("existing_page_id") for topic in pending}
    references = [
        {**_taxonomy_subject(page), "category_path": page.get("folder_path", [])}
        for page in context["pages"]
        if page["page_id"] not in pending_ids
        and page.get("status") not in {"failed", "unavailable"}
    ]
    peers = [_taxonomy_subject(topic) for topic in topics]
    paths = sorted(context["folders"], key=lambda path: (len(path), path))
    base = {"language": snapshot["language"], "topics": [], "existing_paths": [],
            "reference_topics": [], "peer_topics": []}
    remaining = items
    while remaining:
        # 先为一个完整待分类对象留出预算，参考目录和兄弟主题只能占用剩余空间。
        largest = max(remaining, key=tokens)
        available = model.settings.kb_wiki_input_tokens - request_tokens(
            "wiki_taxonomy_v2", {**base, "topics": [largest]}, FolderAssignments
        ) - TAXONOMY_CONTEXT_MARGIN_TOKENS
        budget = max(0, min(model.settings.kb_wiki_input_tokens // 2, available)) // 3
        navigation = {
            "existing_paths": optional_items(paths, budget),
            "reference_topics": optional_items(references, budget),
            "peer_topics": optional_items(peers, budget),
        }

        def build(values, navigation=navigation):
            return {**base, **navigation, "topics": values}

        # 每批结束后重新计算上下文，后批必须能参考前批已确认的分类。
        group = next(batches(remaining, stage="wiki_taxonomy_v2", build=build,
                             schema=FolderAssignments, settings=model.settings))
        refs = {item["topic_ref"] for item in group}

        def validate(value, refs=refs):
            actual = [item["topic_ref"] for item in value["assignments"]]
            require_refs(actual, refs, complete=True)
            if len(actual) != len(set(actual)):
                raise ValueError("Duplicate category decisions")
            for item in value["assignments"]:
                validate_category_path(item["category_path"])

        result = model.call("wiki_taxonomy_v2", build(group), FolderAssignments, validate)
        for item in result["assignments"]:
            path = item["category_path"]
            topic = by_ref[item["topic_ref"]]
            topic["category_path"] = path
            # 优先保留本轮前批的决定，不能被大量历史参考挤出预算。
            references.insert(0, {**_taxonomy_subject(topic), "category_path": path})
            if path and path not in paths:
                paths.append(path)
        paths.sort(key=lambda path: (len(path), path))
        remaining = remaining[len(group):]
    return topics
