"""Document Map selects original evidence while an independent summary covers all text."""

from collections import defaultdict

from ..chunk.token_utils import split_by_token_limit
from .batching import batches, optional_items, parallel
from .config import WikiError, normalize_name, source_reference_key
from .contracts import Citations, DocumentSummary, SummaryPoints
from .model import request_tokens, tokens
from .pipeline import (
    compress,
    link_mentions,
    model_sources,
    model_topics,
    require_refs,
    validate_markdown,
)


def _summary(model, snapshot, topics, segments, catalog):
    base = {"language": snapshot["language"], "document_title": snapshot["title"],
            "confirmed_topics": optional_items(model_topics(topics),
                                               model.settings.kb_wiki_input_tokens // 8)}

    def validate(value):
        require_refs(value["source_refs"], catalog, complete=True)
        validate_markdown(value["summary_markdown"], value["source_refs"],
                          {topic["topic_ref"] for topic in base["confirmed_topics"]})

    if len(segments) == 1:
        remaining = model.settings.kb_wiki_input_tokens - request_tokens(
            "wiki_summary_full_v2", {**base, "confirmed_topics": [],
                                     "sources": model_sources(segments[0])}, DocumentSummary
        ) - 128
        base["confirmed_topics"] = optional_items(base["confirmed_topics"], max(0, remaining))
        return model.call("wiki_summary_full_v2", {**base, "sources": model_sources(segments[0])},
                          DocumentSummary, validate)
    points = []
    for segment in segments:
        refs = {source["ref"] for source in segment}

        def validate_points(value, refs=refs):
            require_refs([r for p in value["summary_points"] for r in p["source_refs"]],
                         refs, complete=True)

        value = model.call("wiki_summary_points_v2",
                           {"language": snapshot["language"], "sources": model_sources(segment)},
                           SummaryPoints, validate_points)
        points.extend(value["summary_points"])
    data = {**base, "summary_points": points}
    if request_tokens("wiki_document_summary_v1", data, DocumentSummary) > (
        model.settings.kb_wiki_input_tokens
    ):
        available = model.settings.kb_wiki_input_tokens - request_tokens(
            "wiki_document_summary_v1", {**base, "summary_points": []}, DocumentSummary
        ) - 256
        if available < 128:
            raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False)
        points = compress(model, [{**point, "qualifiers": [], "source_item_refs": [f"P{i}"]}
                                 for i, point in enumerate(points)], target="document_summary",
                          language=snapshot["language"], limit=available)
    points = [{"text": point["text"], "source_refs": point["source_refs"]} for point in points]
    return model.call("wiki_document_summary_v1", {**base, "summary_points": points},
                      DocumentSummary, validate)


def _classify(model, snapshot, topics, segment, allow_new):
    largest = ([max(topics, key=lambda topic: tokens(model_topics([topic])))] if topics else [])
    fixed = {"language": snapshot["language"], "topics": model_topics(largest),
             "sources": [], "allow_new": allow_new}
    available = model.settings.kb_wiki_input_tokens - request_tokens(
        "wiki_chunk_citations_v2", fixed, Citations
    ) - 128
    pieces = []
    for source in segment:
        size = available - tokens(model_sources([{**source, "text": ""}]))
        if size < 1:
            raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False)
        pieces.extend({**source, "text": text}
                      for text in split_by_token_limit(source["text"], size, disallowed_special=()))

    source_groups = batches(pieces, stage="wiki_chunk_citations_v2",
                            build=lambda values: {**fixed, "sources": model_sources(values)},
                            schema=Citations, settings=model.settings)
    results = []
    for subsegment in source_groups:
        results.extend(_classify_slice(model, snapshot, topics, subsegment, allow_new))
    return results


def _classify_slice(model, snapshot, topics, segment, allow_new):
    refs = {source["ref"] for source in segment}

    def build(values):
        return {"language": snapshot["language"], "topics": model_topics(values),
                "sources": model_sources(segment), "allow_new": allow_new}

    groups = batches(topics, stage="wiki_chunk_citations_v2", build=build,
                     schema=Citations, settings=model.settings)
    results = []
    for index, group in enumerate(groups):
        handles = {topic["topic_ref"] for topic in group}
        new_allowed = allow_new and index == 0

        def validate(value, handles=handles, new_allowed=new_allowed):
            actual = [t["topic_ref"] for t in value["topics"]]
            require_refs(actual, handles, complete=True)
            if len(actual) != len(set(actual)):
                raise ValueError("Duplicate topic handles")
            for topic in value["topics"]:
                require_refs(topic["source_refs"], refs)
            if value["new_candidates"] and not new_allowed:
                raise ValueError("New topics are not allowed for this fixed plan")
            for topic in value["new_candidates"]:
                require_refs(topic["source_refs"], refs)

        results.append(model.call("wiki_chunk_citations_v2",
                                  {**build(group), "allow_new": new_allowed}, Citations, validate))
    return results


def prepare_document(model, snapshot, candidates, segments, catalog, *, fixed_plan=False):
    topics = [{**candidate, "topic_ref": candidate.get("topic_ref", f"T{i + 1}"),
               "title": candidate.get("title", candidate.get("name"))}
              for i, candidate in enumerate(candidates)]
    operations = [lambda: _summary(model, snapshot, topics, segments, catalog)]
    operations.extend(lambda segment=segment:
                      _classify(model, snapshot, topics, segment, not fixed_plan)
                      for segment in segments)
    results = parallel(operations, model.settings)
    cited = defaultdict(set)
    identities = {(t["kind"], normalize_name(t["title"]), normalize_name(t["scope"])):
                  t["topic_ref"] for t in topics}
    for result in results[1:]:
        for batch in result:
            for topic in batch["topics"]:
                cited[topic["topic_ref"]].update(topic["source_refs"])
            for candidate in batch["new_candidates"]:
                key = (candidate["kind"], normalize_name(candidate["name"]),
                       normalize_name(candidate["scope"]))
                handle = identities.get(key)
                if handle is None:
                    handle = f"T{len(topics) + 1}"
                    topics.append({**candidate, "topic_ref": handle, "title": candidate["name"]})
                    identities[key] = handle
                cited[handle].update(candidate["source_refs"])
    limit = model.settings.kb_wiki_max_topics
    if limit and len(topics) > limit:
        raise WikiError("WIKI_TOPIC_BUDGET_EXCEEDED", retryable=False)
    for topic in topics:
        if not cited[topic["topic_ref"]]:
            raise WikiError("WIKI_TOPIC_WITHOUT_EVIDENCE")
        topic["source_refs"] = sorted(cited[topic["topic_ref"]])
    return {"topics": topics, "summary": results[0]}


def bind_materials(draft, plan, catalog, title):
    mappings = plan.get("topic_pages") or {t["topic_ref"]: t["page_id"] for t in plan["topics"]}
    links = [{"topic_ref": topic["topic_ref"], "page_id": mappings[topic["topic_ref"]]}
             for topic in draft["topics"]]
    # Citation-discovered topics can be linked after the parallel summary has completed.
    canonical_refs = {}
    for topic in draft["topics"]:
        canonical_refs.setdefault(mappings[topic["topic_ref"]], topic["topic_ref"])
    choices = [{"title": topic["title"], "aliases": topic.get("aliases", []),
                "page_id": canonical_refs[mappings[topic["topic_ref"]]]}
               for topic in draft["topics"]]
    summary = {**draft["summary"]}
    summary["summary_markdown"], _ = link_mentions(summary["summary_markdown"], choices)
    grouped = defaultdict(set)
    metadata = {}
    for topic in draft["topics"]:
        page_id = mappings[topic["topic_ref"]]
        grouped[page_id].update(topic["source_refs"])
        item = metadata.setdefault(page_id, {"aliases": [], "category_path": []})
        item["aliases"] = sorted(set([*item["aliases"], *topic.get("aliases", []),
                                      topic.get("name", topic["title"])]))
        if topic.get("category_path"):
            item["category_path"] = topic["category_path"]
    context = {"title": title, "summary": summary, "topics": links,
               "format": "original_evidence_v2"}
    materials = [{"page_id": page_id, "facts": [],
                  "chunk_refs": [catalog[ref] for ref in sorted(refs, key=source_reference_key)],
                  "source_context": {"title": title, "format": "original_evidence_v2",
                                     "page_metadata": metadata[page_id]}}
                 for page_id, refs in grouped.items()]
    materials.append({"page_id": plan["summary_id"], "facts": [],
                      "chunk_refs": list(catalog.values()), "source_context": context})
    return materials
