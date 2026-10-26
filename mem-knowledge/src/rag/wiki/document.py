"""Document Map keeps valid citations and independently generates a Markdown summary."""

import logging
from collections import defaultdict

from ...integrations.model.errors import ModelInvokeClientError
from ...tasks.observability import current_parent_task_id
from ..chunk.token_utils import split_by_token_limit
from .batching import batches, parallel
from .config import WikiError, normalize_name, source_reference_key
from .contracts import Citations
from .diagnostics import diagnostic_json, exception_details
from .model import request_tokens, tokens
from .pipeline import (
    PAGE_LINK,
    link_mentions,
    model_sources,
    model_topics,
    rewrite_markdown_text,
)
from .summary import summarize_document

logger = logging.getLogger(__name__)
_CITATION_OUTPUT_ERRORS = frozenset({"WIKI_MODEL_OUTPUT_INVALID", "WIKI_MODEL_OUTPUT_TRUNCATED"})


def _citation_log(snapshot, event, **details):
    logger.warning("Wiki 引用降级 %s", diagnostic_json({
        "event": event, "task_id": current_parent_task_id(),
        "document_id": snapshot.get("document_id"), "stage": "wiki_chunk_citations_v2",
        **details,
    }))


def _filter_citations(value, handles, refs, new_allowed, snapshot, *, batch_index, model_id):
    cited = defaultdict(set)
    unknown_topics, unknown_sources = set(), set()
    for topic in value["topics"]:
        handle = topic["topic_ref"]
        if handle not in handles:
            unknown_topics.add(handle)
            continue
        selected = set(topic["source_refs"])
        unknown_sources.update(selected - refs)
        cited[handle].update(selected & refs)
    candidates = []
    for candidate in value["new_candidates"]:
        selected = set(candidate["source_refs"])
        unknown_sources.update(selected - refs)
        valid = sorted(selected & refs, key=source_reference_key)
        if new_allowed and valid:
            candidates.append({**candidate, "source_refs": valid})
    if unknown_topics or unknown_sources or (value["new_candidates"] and not new_allowed):
        _citation_log(snapshot, "wiki_citation_handles_filtered",
                      unknown_topics=sorted(unknown_topics),
                      unknown_sources=sorted(unknown_sources),
                      new_candidates_allowed=new_allowed, batch_index=batch_index,
                      model_id=model_id)
    return {"failed": False, "dropped_handles": len(unknown_topics) + len(unknown_sources),
            "dropped_candidates": len(value["new_candidates"]) - len(candidates),
            "topics": [{"topic_ref": key, "source_refs": sorted(ids, key=source_reference_key)}
                       for key, ids in cited.items()], "new_candidates": candidates}


def _classification_context(snapshot, allow_new):
    return {
        "language": snapshot["language"], "document_title": snapshot.get("title", ""),
        "extraction_focus": snapshot.get("extract_prompt", ""), "allow_new": allow_new,
    }


def _classify(model, snapshot, topics, segment, allow_new):
    largest = ([max(topics, key=lambda topic: tokens(model_topics([topic])))] if topics else [])
    fixed = {**_classification_context(snapshot, allow_new),
             "topics": model_topics(largest), "sources": []}
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
        return {**_classification_context(snapshot, allow_new), "topics": model_topics(values),
                "sources": model_sources(segment)}

    groups = batches(topics, stage="wiki_chunk_citations_v2", build=build,
                     schema=Citations, settings=model.settings)
    results = []
    for index, group in enumerate(groups):
        handles = {topic["topic_ref"] for topic in group}
        new_allowed = allow_new and index == 0

        try:
            value = model.call(
                "wiki_chunk_citations_v2", {**build(group), "allow_new": new_allowed},
                Citations, repair_attempts=0,
            )
        except WikiError as exc:
            if (exc.code not in _CITATION_OUTPUT_ERRORS
                    and not isinstance(exc.__cause__, ModelInvokeClientError)):
                raise
            _citation_log(snapshot, "wiki_citation_batch_failed", batch_index=index,
                          topic_handles=sorted(handles), source_handles=sorted(refs),
                          model_id=model.model_id, error_code=exc.code,
                          failure_details=exception_details(exc))
            results.append({"topics": [], "new_candidates": [], "failed": True,
                            "dropped_handles": 0, "dropped_candidates": 0})
            continue
        results.append(_filter_citations(
            value, handles, refs, new_allowed, snapshot, batch_index=index, model_id=model.model_id
        ))
    return results


def prepare_document(model, snapshot, candidates, segments, catalog, *, fixed_plan=False):
    topics = [{**candidate, "topic_ref": candidate.get("topic_ref", f"T{i + 1}"),
               "title": candidate.get("title", candidate.get("name"))}
              for i, candidate in enumerate(candidates)]
    operations = [lambda: summarize_document(model, snapshot, topics, segments, catalog)]
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
    retained, skipped_pages = [], []
    fallback_count = 0
    for topic in topics:
        refs = cited[topic["topic_ref"]]
        if not refs:
            refs = set(topic.get("source_refs", [])) & catalog.keys()
            fallback_count += bool(refs)
            _citation_log(snapshot, "wiki_citation_fallback", topic_ref=topic["topic_ref"],
                          fallback_count=len(refs), page_id=topic.get("page_id"),
                          model_id=model.model_id)
        if not refs:
            if fixed_plan and topic.get("page_id"):
                skipped_pages.append(topic["page_id"])
            continue
        retained.append({**topic, "source_refs": sorted(refs, key=source_reference_key)})
    batches_done = [batch for result in results[1:] for batch in result]
    logger.info("Wiki 引用汇总 %s", diagnostic_json({
        "event": "wiki_citation_summary", "task_id": current_parent_task_id(),
        "document_id": snapshot.get("document_id"), "model_id": model.model_id,
        "batches_succeeded": sum(not batch["failed"] for batch in batches_done),
        "batches_failed": sum(batch["failed"] for batch in batches_done),
        "dropped_handles": sum(batch["dropped_handles"] for batch in batches_done),
        "topics_retained": len(retained), "topics_fallback": fallback_count,
        "topics_skipped": len(topics) - len(retained) + sum(
            batch["dropped_candidates"] for batch in batches_done
        ),
    }))
    return {"topics": retained, "summary": results[0], "skipped_pages": skipped_pages}


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
    valid_handles = {link["topic_ref"] for link in links}
    summary["summary_markdown"] = rewrite_markdown_text(
        summary["summary_markdown"],
        lambda part: PAGE_LINK.sub(
            lambda match: match[0] if match[1] in valid_handles else match[2], part
        ),
    )
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
    materials.extend({"page_id": page_id, "preserve_existing": True}
                     for page_id in sorted(set(draft.get("skipped_pages", [])) - grouped.keys()))
    materials.append({"page_id": plan["summary_id"], "facts": [],
                      "chunk_refs": list(catalog.values()), "source_context": context})
    return materials
