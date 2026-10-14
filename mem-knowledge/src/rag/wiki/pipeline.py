"""Evidence-bounded Map extraction, compression and page rendering."""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from .config import WikiError, fingerprint, normalize_name
from .contracts import Candidates, Compacted, DocumentSummary, Materials, PageContent
from .model import tokens

logger = logging.getLogger(__name__)

CITATION = re.compile(r"\[\^([A-Za-z0-9_-]+)\]")
PAGE_LINK = re.compile(r"\[\[([^\]|]+)\|([^\]\n]+)\]\]")
HTML = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
UNSAFE_LINK = re.compile(r"\]\(\s*(?:[a-z][a-z0-9+.-]*:|//)", re.I)
CODE_ISLANDS = re.compile(
    r"^ {0,3}(?P<backticks>`{3,})[^`\n]*\n.*?^ {0,3}(?P=backticks)`*[ \t]*$"
    r"|^ {0,3}(?P<tildes>~{3,})[^\n]*\n.*?^ {0,3}(?P=tildes)~*[ \t]*$"
    r"|(?<!`)(?P<inline>`+)(?!`)[^\n]*?(?<!`)(?P=inline)(?!`)",
    re.M | re.S,
)


def markdown_parts(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    offset = 0
    for match in CODE_ISLANDS.finditer(text):
        yield False, text[offset : match.start()]
        yield True, match[0]
        offset = match.end()
    yield False, text[offset:]


def rewrite_markdown_text(text, transform):
    return "".join(part if code else transform(part) for code, part in markdown_parts(text))


def require_refs(actual, allowed, *, complete=False):
    actual, allowed = set(actual), set(allowed)
    if not actual.issubset(allowed) or (complete and actual != allowed):
        raise ValueError("Unknown or missing evidence handles")


def validate_markdown(text, source_refs, links):
    text = "".join(part for code, part in markdown_parts(text) if not code)
    if HTML.search(text) or UNSAFE_LINK.search(text):
        raise ValueError("Raw HTML and external or executable links are not allowed")
    cited = set(CITATION.findall(text))
    require_refs(cited, source_refs, complete=True)
    require_refs([match[0] for match in PAGE_LINK.findall(text)], links)


def model_sources(sources):
    return [{key: item[key] for key in ("ref", "heading", "text")} for item in sources]


def model_topics(topics):
    return [{key: item[key] for key in ("topic_ref", "kind", "title", "scope")} for item in topics]


def extract_candidates(model, snapshot, segments):
    candidates = {}
    for segment_index, segment in enumerate(segments, start=1):
        source_ids = {s["ref"] for s in segment}

        def validate(value, source_ids=source_ids):
            for candidate in value["candidates"]:
                require_refs(candidate["source_refs"], source_ids)
                if any(not name.strip() or len(name) > 200 for name in candidate["category_path"]):
                    raise ValueError("Invalid category name")

        result = model.call(
            "wiki_candidates_v1",
            {
                "language": snapshot["language"],
                "document_title": snapshot["title"],
                "extraction_focus": snapshot["extract_prompt"],
                "sources": model_sources(segment),
            },
            Candidates,
            validate,
        )
        for item in result["candidates"]:
            key = (item["kind"], normalize_name(item["name"]), normalize_name(item["scope"]))
            if key in candidates:
                previous = candidates[key]
                previous["source_refs"] = sorted(set(previous["source_refs"] + item["source_refs"]))
                previous["aliases"] = sorted(set(previous["aliases"] + item["aliases"]))
            else:
                candidates[key] = item
        logger.info(
            "Wiki candidate accumulation document_id=%s segment=%s/%s "
            "segment_candidates=%s unique_candidates=%s named_candidates=%s topic_limit=%s",
            snapshot.get("document_id"),
            segment_index,
            len(segments),
            len(result["candidates"]),
            len(candidates),
            len({key[:2] for key in candidates}),
            model.settings.kb_wiki_max_topics,
        )
        if len(candidates) > model.settings.kb_wiki_max_topics:
            raise WikiError("WIKI_TOPIC_BUDGET_EXCEEDED", retryable=False)
    return list(candidates.values())


def compress(model, items, *, target, language):
    """Bounded recursive compaction with original identity and source conservation."""
    limit = model.settings.kb_wiki_input_tokens // 3
    original_sources = defaultdict(set)
    for item in items:
        for ref in item["source_item_refs"]:
            original_sources[ref].update(item["source_refs"])
    for _ in range(model.settings.kb_wiki_compact_depth):
        if tokens(items) <= limit:
            return items
        groups, group = [], []
        for item in items:
            if tokens(item) > limit:
                raise WikiError("WIKI_EVIDENCE_ITEM_TOO_LARGE", retryable=False)
            if group and tokens([*group, item]) > limit:
                groups.append(group)
                group = []
            group.append(item)
        if group:
            groups.append(group)
        compressed = []
        for group in groups:
            original = {ref for item in group for ref in item["source_item_refs"]}
            sources = {ref for item in group for ref in item["source_refs"]}

            def validate(value, original=original, sources=sources):
                require_refs(
                    value["covered_item_refs"] + value["omitted_item_refs"], original, complete=True
                )
                if value["omitted_item_refs"]:
                    raise ValueError("Evidence was omitted during compaction")
                require_refs(
                    [r for i in value["items"] for r in i["source_item_refs"]],
                    original,
                    complete=True,
                )
                require_refs(
                    [r for i in value["items"] for r in i["source_refs"]], sources, complete=True
                )
                for item in value["items"]:
                    require_refs(item["source_item_refs"], original)
                    support = {
                        ref
                        for original_ref in item["source_item_refs"]
                        for ref in original_sources[original_ref]
                    }
                    require_refs(item["source_refs"], support, complete=True)

            result = model.call(
                "wiki_evidence_compact_v1",
                {
                    "language": language,
                    "target": target,
                    "budget": max(128, limit // 2),
                    "items": [{**i, "item_ref": f"I{index + 1}"} for index, i in enumerate(group)],
                },
                Compacted,
                validate,
            )
            compressed.extend(result["items"])
        items = compressed
    if tokens(items) > limit:
        raise WikiError("WIKI_COMPACTION_BUDGET_EXCEEDED", retryable=False)
    return items


def extract_materials(model, snapshot, plan, segments, source_catalog):
    topics = model_topics(plan["topics"])
    topic_ids = {item["topic_ref"] for item in topics}
    facts = defaultdict(list)
    points, summary = [], None
    full_document = len(segments) == 1
    for segment in segments:
        source_ids = {s["ref"] for s in segment}

        def validate(value, source_ids=source_ids):
            handles = [item["topic_ref"] for item in value["topics"]]
            if len(handles) != len(set(handles)):
                raise ValueError("Duplicate topic handles")
            require_refs(handles, topic_ids, complete=True)
            for topic in value["topics"]:
                for fact in topic["facts"]:
                    require_refs(fact["source_refs"], source_ids)
            require_refs(
                [r for p in value["summary_points"] for r in p["source_refs"]],
                source_ids,
                complete=True,
            )
            result_summary = value["document_summary"]
            if full_document:
                if result_summary is None:
                    raise ValueError("Whole-document summary missing")
                require_refs(result_summary["source_refs"], source_ids, complete=True)
                validate_markdown(
                    result_summary["summary_markdown"], result_summary["source_refs"], topic_ids
                )
            elif result_summary is not None:
                raise ValueError("A segment is not a whole document")

        value = model.call(
            "wiki_materials_v1",
            {
                "language": snapshot["language"],
                "mode": "full_document" if full_document else "segment",
                "document_title": snapshot["title"],
                "extraction_focus": snapshot["extract_prompt"],
                "confirmed_topics": topics,
                "sources": model_sources(segment),
            },
            Materials,
            validate,
        )
        for item in value["topics"]:
            facts[item["topic_ref"]].extend(item["facts"])
        points.extend(value["summary_points"])
        summary = value["document_summary"] or summary
    if not full_document:
        items = [
            {**point, "qualifiers": [], "source_item_refs": [f"P{i + 1}"]}
            for i, point in enumerate(points)
        ]
        points = compress(model, items, target="document_summary", language=snapshot["language"])

        def validate_summary(value):
            require_refs(value["source_refs"], source_catalog, complete=True)
            validate_markdown(value["summary_markdown"], value["source_refs"], topic_ids)

        summary = model.call(
            "wiki_document_summary_v1",
            {
                "language": snapshot["language"],
                "document_title": snapshot["title"],
                "summary_points": points,
                "confirmed_topics": topics,
            },
            DocumentSummary,
            validate_summary,
        )
    materials = []
    for topic in plan["topics"]:
        values = facts[topic["topic_ref"]]
        if not values:
            raise WikiError("WIKI_TOPIC_WITHOUT_EVIDENCE")
        related = {ref for fact in values for ref in fact["source_refs"]}
        materials.append(
            {
                "page_id": topic["page_id"],
                "facts": values,
                "chunk_refs": [source_catalog[ref] for ref in sorted(related)],
                "source_context": {"title": snapshot["title"], "topics": plan["topics"]},
            }
        )
    if summary is not None:
        materials.append(
            {
                "page_id": plan["summary_id"],
                "facts": [],
                "chunk_refs": list(source_catalog.values()),
                "source_context": {
                    "title": snapshot["title"],
                    "summary": summary,
                    "topics": plan["topics"],
                },
            }
        )
    return materials


def reduce_input(snapshot):
    catalog, evidence, source_mapping = [], [], {}
    for source in snapshot["sources"]:
        mapping = {}
        for chunk in sorted(source["chunk_refs"], key=lambda c: c["ref"]):
            ref = f"R{len(catalog) + 1}"
            mapping[chunk["ref"]] = ref
            catalog.append(
                {
                    "ref": ref,
                    "document_id": source["document_id"],
                    "document_title": source["title"],
                    "chunk_id": chunk["chunk_id"],
                    "quote": chunk["text"],
                }
            )
        source_mapping[source["document_id"]] = mapping
        for index, fact in enumerate(source["facts"]):
            fact_ref = "F" + fingerprint([source["document_id"], source["version"], index])[:20]
            evidence.append(
                {
                    "source_item_refs": [fact_ref],
                    "text": fact["text"],
                    "qualifiers": fact["qualifiers"],
                    "source_refs": [mapping[ref] for ref in fact["source_refs"]],
                }
            )
    return catalog, evidence, source_mapping


def render_links(text, mapping):
    links = []

    def substitute(match):
        target = mapping[match[1]]
        links.append(target)
        return f"[[{target}|{match[2]}]]"

    return rewrite_markdown_text(text, lambda part: PAGE_LINK.sub(substitute, part)), links


def generate_page(model, snapshot):
    if not snapshot["sources"]:
        return {"content": "", "summary": "", "links": []}
    catalog, evidence, mapping = reduce_input(snapshot)
    if snapshot["page_type"] == "summary":
        source = snapshot["sources"][0]
        summary = source["source_context"]["summary"]
        topics = {t["topic_ref"]: t["page_id"] for t in source["source_context"]["topics"]}
        validate_markdown(summary["summary_markdown"], summary["source_refs"], topics)
        text, links = render_links(summary["summary_markdown"], topics)
        text = rewrite_markdown_text(
            text,
            lambda part: CITATION.sub(lambda m: f"[^{mapping[source['document_id']][m[1]]}]", part),
        )
        return {"content": text, "summary": summary["summary_line"], "links": links}
    items = compress(model, evidence, target=snapshot["title"], language=snapshot["language"])
    all_facts = {r for i in evidence for r in i["source_item_refs"]}
    links = {f"L{i + 1}": p["page_id"] for i, p in enumerate(snapshot["allowed_links"])}
    allowed_refs = {item["ref"] for item in catalog}

    def validate(value):
        require_refs(value["used_fact_refs"], all_facts, complete=True)
        require_refs(value["source_refs"], allowed_refs, complete=True)
        validate_markdown(value["content_markdown"], value["source_refs"], links)

    result = model.call(
        "wiki_page_reduce_v1",
        {
            "language": snapshot["language"],
            "page": {"title": snapshot["title"], "type": snapshot["page_type"]},
            "previous_outline": [
                line for line in snapshot["content"].splitlines() if line.startswith("#")
            ][:20],
            "evidence": [
                {
                    "fact_refs": i["source_item_refs"],
                    "text": i["text"],
                    "qualifiers": i["qualifiers"],
                    "source_refs": i["source_refs"],
                }
                for i in items
            ],
            # Raw quotes remain in persisted source records; facts carry the model evidence.
            "source_catalog": [
                {"ref": c["ref"], "document_title": c["document_title"]} for c in catalog
            ],
            "allowed_links": [
                {"link_ref": f"L{i + 1}", "title": p["title"], "type": p["type"]}
                for i, p in enumerate(snapshot["allowed_links"])
            ],
        },
        PageContent,
        validate,
    )
    content, targets = render_links(result["content_markdown"], links)
    return {"content": content, "summary": result["summary"], "links": targets}
