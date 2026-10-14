"""Reuse known page identities and plan one coherent directory before persistence."""

from difflib import SequenceMatcher

from .batching import batches, optional_items
from .config import TYPE_GROUP_NAMES, normalize_name
from .contracts import FolderAssignments, IdentityMatches
from .pipeline import require_refs


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
            if score >= 0.5:
                scored.append((score, page))
        selected = [p for _, p in sorted(scored, key=lambda pair: -pair[0])[:5]]
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


def taxonomy(model, snapshot, topics, context):
    hints = {normalize_name(value) for topic in topics
             for value in [topic["name"], *topic.get("category_path", [])]}

    def relevance(path):
        return max((SequenceMatcher(None, normalize_name(part), hint).ratio()
                    for part in path for hint in hints), default=0.0)

    paths = sorted(context["folders"], key=lambda path: (len(path) > 1, -relevance(path), path))
    existing = optional_items(paths, model.settings.kb_wiki_input_tokens // 4)
    known = {page["page_id"]: page for page in context["pages"]}
    pending = [topic for topic in topics
               if not known.get(topic.get("existing_page_id"), {}).get("folder_id")]
    by_ref = {topic["topic_ref"]: topic for topic in pending}
    items = [{"topic_ref": topic["topic_ref"], "name": topic["name"], "kind": topic["kind"],
              "description": topic.get("description", "")[:160]} for topic in pending]

    def build(values):
        return {"language": snapshot["language"], "topics": values, "existing_paths": existing}

    for group in batches(items, stage="wiki_taxonomy_v2", build=build,
                         schema=FolderAssignments, settings=model.settings) if items else []:
        refs = {item["topic_ref"] for item in group}

        def validate(value, refs=refs):
            actual = [item["topic_ref"] for item in value["assignments"]]
            require_refs(actual, refs, complete=True)
            if len(actual) != len(set(actual)):
                raise ValueError("Duplicate category decisions")
            for item in value["assignments"]:
                if any(not name.strip() or len(name) > 200
                       or normalize_name(name) in TYPE_GROUP_NAMES
                       for name in item["category_path"]):
                    raise ValueError("Invalid navigation category")

        result = model.call("wiki_taxonomy_v2", build(group), FolderAssignments, validate)
        for item in result["assignments"]:
            path = item["category_path"]
            by_ref[item["topic_ref"]]["category_path"] = path
            if path and path not in existing:
                existing = optional_items([*existing, path],
                                          model.settings.kb_wiki_input_tokens // 4)
    return topics
