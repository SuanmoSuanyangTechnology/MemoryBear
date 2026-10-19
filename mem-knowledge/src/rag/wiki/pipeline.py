"""Evidence-bounded Map extraction, compression and page rendering."""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from ..chunk.token_utils import split_by_token_limit
from .batching import optional_items, parallel
from .config import WikiError, fingerprint, normalize_name, source_reference_key
from .contracts import Candidates, Compacted, EvidencePage
from .model import request_tokens, tokens

logger = logging.getLogger(__name__)

CITATION = re.compile(r"\[\^([A-Za-z0-9_-]+)\]")
VISIBLE_SOURCE_MARKER = re.compile(rf"{CITATION.pattern}|\[\[[SR][0-9]+\]\]")
CITATION_DEFINITION = re.compile(
    r"^ {0,3}\[\^([A-Za-z0-9_-]+)\]:[^\n]*(?:\n(?: {4}|\t)[^\n]*)*(?:\n|$)", re.M
)
PAGE_LINK = re.compile(r"\[\[([^\]|]+)\|([^\]\n]+)\]\]")
HTML = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
UNSAFE_LINK = re.compile(r"\]\(\s*(?:[a-z][a-z0-9+.-]*:|//)", re.I)
CODE_ISLANDS = re.compile(
    r"^ {0,3}(?P<backticks>`{3,})[^`\n]*\n.*?^ {0,3}(?P=backticks)`*[ \t]*$"
    r"|^ {0,3}(?P<tildes>~{3,})[^\n]*\n.*?^ {0,3}(?P=tildes)~*[ \t]*$"
    r"|(?<!`)(?P<inline>`+)(?!`)[^\n]*?(?<!`)(?P=inline)(?!`)",
    re.M | re.S,
)
INDENTED_CODE = re.compile(r"^(?: {4}|\t)[^\n]*", re.M)


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
        raise ValueError(
            "Unknown or missing evidence handles: "
            f"unknown={sorted(actual - allowed)}, "
            f"missing={sorted(allowed - actual) if complete else []}"
        )


def validate_markdown(text, source_refs, links):
    text = "".join(part for code, part in markdown_parts(text) if not code)
    if HTML.search(text) or UNSAFE_LINK.search(text):
        raise ValueError("Raw HTML and external or executable links are not allowed")
    cited = set(CITATION.findall(text))
    # Structured source_refs owns coverage; visible footnotes are optional legacy input.
    require_refs(cited, source_refs)
    require_refs([match[0] for match in PAGE_LINK.findall(text)], links)


def readable_markdown(text, source_refs):
    """Hide evidence markers without changing prose, Wiki links or code examples."""
    allowed = set(source_refs)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    code_spans = [match.span() for match in CODE_ISLANDS.finditer(text)]

    def remove_definition(match):
        in_code = any(start <= match.start() < end for start, end in code_spans)
        return "" if match[1] in allowed and not in_code else match[0]

    # Remove whole definitions, including any inline code within their source descriptions.
    text = CITATION_DEFINITION.sub(remove_definition, text)

    code_spans = [match.span() for pattern in (CODE_ISLANDS, INDENTED_CODE)
                  for match in pattern.finditer(text)]

    def remove_marker(match):
        if any(start <= match.start() < end for start, end in code_spans):
            return match[0]
        # S/R 数字编号为来源标记；S 在读取时可能已重映射为 R，不能仅按当前目录匹配。
        # 真正的 Wiki 页面引用含有 |，不属于此匹配；未知自定义脚注仍原样保留。
        return "" if match[1] is None or match[1] in allowed else match[0]

    return VISIBLE_SOURCE_MARKER.sub(remove_marker, text)


def model_sources(sources):
    return [{key: item[key] for key in ("ref", "heading", "text")} for item in sources]


def model_topics(topics):
    return [
        {**{key: item[key] for key in ("topic_ref", "kind", "title", "scope")},
         "aliases": item.get("aliases", [])}
        for item in topics
    ]


def extract_candidates(model, snapshot, segments):
    def extract(segment):
        source_ids = {s["ref"] for s in segment}

        def validate(value, source_ids=source_ids):
            for candidate in value["candidates"]:
                require_refs(candidate["source_refs"], source_ids)

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
        return result

    results = parallel([lambda segment=segment: extract(segment) for segment in segments],
                       model.settings)
    candidates = {}
    for segment_index, result in enumerate(results, start=1):
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
        if (model.settings.kb_wiki_max_topics
                and len(candidates) > model.settings.kb_wiki_max_topics):
            raise WikiError("WIKI_TOPIC_BUDGET_EXCEEDED", retryable=False)
    return list(candidates.values())


def compress(model, items, *, target, language, limit=None):
    """Bounded recursive compaction with original identity and source conservation."""
    limit = limit or model.settings.kb_wiki_input_tokens // 3
    if tokens(items) <= limit:
        return items
    limit = min(limit, model.settings.kb_wiki_input_tokens - request_tokens(
        "wiki_evidence_compact_v1",
        {"language": language, "target": target, "budget": 128, "items": []}, Compacted
    ) - 256)
    if limit < 128:
        raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False)
    expanded, identities = [], {}
    for index, item in enumerate(items):
        if tokens(item) <= limit:
            expanded.append(item)
            continue
        available = limit - tokens({**item, "text": ""}) - 64
        if available < 1:
            raise WikiError("WIKI_EVIDENCE_ITEM_TOO_LARGE", retryable=False)
        for part_index, text in enumerate(split_by_token_limit(
            item["text"], available, disallowed_special=()
        )):
            ref = f"PART_{index}_{part_index}"
            identities[ref] = item["source_item_refs"]
            expanded.append({**item, "text": text, "source_item_refs": [ref]})
    items = expanded

    def restore(values):
        return [{**item, "source_item_refs": sorted({
            original for ref in item["source_item_refs"] for original in identities.get(ref, [ref])
        })} for item in values]

    original_sources = defaultdict(set)
    for item in items:
        for ref in item["source_item_refs"]:
            original_sources[ref].update(item["source_refs"])

    def compaction_input(values):
        return {"language": language, "target": target, "budget": max(128, limit // 2),
                "items": [{**item, "item_ref": f"I{i + 1}"} for i, item in enumerate(values)]}

    for _ in range(model.settings.kb_wiki_compact_depth):
        if tokens(items) <= limit:
            return restore(items)
        groups, group = [], []
        for item in items:
            if tokens(item) > limit:
                raise WikiError("WIKI_EVIDENCE_ITEM_TOO_LARGE", retryable=False)
            if group and (tokens([*group, item]) > limit or request_tokens(
                "wiki_evidence_compact_v1", compaction_input([*group, item]), Compacted
            ) > model.settings.kb_wiki_input_tokens):
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
                compaction_input(group),
                Compacted,
                validate,
            )
            compressed.extend(result["items"])
        items = compressed
    if tokens(items) > limit:
        raise WikiError("WIKI_COMPACTION_BUDGET_EXCEEDED", retryable=False)
    return restore(items)


def reduce_input(snapshot):
    catalog, evidence, source_mapping = [], [], {}
    for source in snapshot["sources"]:
        mapping = {}
        original_order = source.get("source_context", {}).get("format") == "original_evidence_v2"
        for chunk in sorted(source["chunk_refs"], key=lambda c: (
            source_reference_key(c["ref"]) if original_order else c["ref"]
        )):
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


def link_mentions(text, choices):
    """Link unambiguous literal mentions, preserving code and existing links/citations."""
    names = defaultdict(set)
    for choice in choices:
        for label in [choice["title"], *choice.get("aliases", [])]:
            if len(label) >= 2 and not any(char in label for char in "[]|\n"):
                names[label].add(choice["page_id"])
    names = {name: next(iter(targets)) for name, targets in names.items() if len(targets) == 1}
    if not names:
        return text, []
    pattern = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(
        re.escape(name) for name in sorted(names, key=lambda name: -len(name))
    ) + r")(?![A-Za-z0-9_])")
    targets = []

    def transform(part):
        offset, result = 0, []

        def replace(match):
            target = names[match[0]]
            targets.append(target)
            return f"[[{target}|{match[0]}]]"

        for match in re.finditer(r"\[\[.*?\]\]|\[\^[^\]]+\]", part):
            result.extend([pattern.sub(replace, part[offset:match.start()]), match[0]])
            offset = match.end()
        result.append(pattern.sub(replace, part[offset:]))
        return "".join(result)

    return rewrite_markdown_text(text, transform), targets


def generate_page(model, snapshot):
    if not snapshot["sources"]:
        return {"content": "", "summary": "", "links": []}
    catalog, _, _ = reduce_input(snapshot)
    if snapshot["page_type"] == "summary":
        source = snapshot["sources"][0]
        summary = source["source_context"]["summary"]
        topics = {t["topic_ref"]: t["page_id"] for t in source["source_context"]["topics"]}
        require_refs(
            summary["source_refs"], [c["ref"] for c in source["chunk_refs"]], complete=True
        )
        validate_markdown(summary["summary_markdown"], summary["source_refs"], topics)
        text, links = render_links(summary["summary_markdown"], topics)
        text = readable_markdown(text, summary["source_refs"])
        return {"content": text, "summary": summary["summary_line"], "links": links}
    items = [{"source_item_refs": [c["ref"]], "text": c["quote"], "qualifiers": [],
              "source_refs": [c["ref"]]} for c in catalog]
    selected_links = optional_items(snapshot["allowed_links"],
                                    model.settings.kb_wiki_input_tokens // 8)
    links = {f"L{i + 1}": p["page_id"] for i, p in enumerate(selected_links)}
    allowed_refs = {item["ref"] for item in catalog}

    def validate(value):
        require_refs(value["source_refs"], allowed_refs, complete=True)
        validate_markdown(value["content_markdown"], value["source_refs"], links)

    contexts = []
    for source in snapshot["sources"]:
        context = source.get("source_context", {}).get("summary", {})
        contexts.append({
            "document_title": source["title"],
            "context": rewrite_markdown_text(
                context.get("summary_markdown", context.get("summary_line", "")),
                lambda part: CITATION.sub("", PAGE_LINK.sub(lambda m: m[2], part)),
            ),
        })
    prior_links = {target: ref for ref, target in links.items()}
    previous = rewrite_markdown_text(snapshot["content"], lambda part: CITATION.sub(
        "", PAGE_LINK.sub(lambda match: (
            f"[[{prior_links[match[1]]}|{match[2]}]]" if match[1] in prior_links else match[2]
        ), part)
    ))
    if tokens(previous) > model.settings.kb_wiki_input_tokens // 8:
        previous = "\n".join(line for line in previous.splitlines() if line.startswith("#"))
    data = {
            "language": snapshot["language"],
            "page": {"title": snapshot["title"], "type": snapshot["page_type"]},
            "previous_content": previous,
            "document_context": optional_items(contexts, model.settings.kb_wiki_input_tokens // 8),
            "evidence": [],
            "source_catalog": [
                {"ref": c["ref"], "document_title": c["document_title"]} for c in catalog
            ],
            "allowed_links": [
                {"link_ref": f"L{i + 1}", "title": p["title"], "type": p["type"]}
                for i, p in enumerate(selected_links)
            ],
    }
    available = model.settings.kb_wiki_input_tokens - request_tokens(
        "wiki_page_original_v2", data, EvidencePage
    ) - 256
    if available < 128:
        # Keep every required source; optional presentation metadata yields first.
        data.update(previous_content="", document_context=[], allowed_links=[])
        links = {}
        available = model.settings.kb_wiki_input_tokens - request_tokens(
            "wiki_page_original_v2", data, EvidencePage
        ) - 256
    if available < 128:
        raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False)
    def page_evidence(values):
        return [{"text": item["text"], "source_refs": item["source_refs"]} for item in values]

    if request_tokens("wiki_page_original_v2", {**data, "evidence": page_evidence(items)},
                      EvidencePage) > model.settings.kb_wiki_input_tokens:
        items = compress(model, items, target=snapshot["title"],
                         language=snapshot["language"], limit=available)
    data["evidence"] = page_evidence(items)
    result = model.call("wiki_page_original_v2", data, EvidencePage, validate)
    content, targets = render_links(result["content_markdown"], links)
    content = readable_markdown(content, allowed_refs)
    content, added = link_mentions(content, snapshot["allowed_links"])
    targets.extend(added)
    return {"content": content, "summary": result["summary"], "links": targets}
