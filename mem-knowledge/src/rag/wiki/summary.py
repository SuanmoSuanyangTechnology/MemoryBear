"""Generate Markdown summaries while keeping source provenance program-owned."""

import json
import re

from .batching import optional_items, parallel
from .config import WikiError
from .model import tokens
from .pipeline import model_sources, model_topics, validate_markdown
from .prompts import TEMPLATES, TEXT_COMMON

SUMMARY_STAGE = "wiki_summary_markdown_v1"
SUMMARY_LINE_CHARS = 400
MAX_SUMMARY_LEVELS = 8


def _request_size(data):
    return tokens(TEXT_COMMON + "\n" + TEMPLATES[SUMMARY_STAGE]) + tokens(data)


def _groups(items, build, budget):
    group = []
    for item in items:
        if _request_size(build([*group, item])) > budget:
            if group:
                yield group
                group = []
            if _request_size(build([item])) > budget:
                raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False)
        group.append(item)
    if group:
        yield group


def summarize_document(model, snapshot, topics, segments, catalog):
    budget = model.settings.kb_wiki_input_tokens
    topic_context = optional_items(model_topics(topics), budget // 8)
    base = {"language": snapshot["language"], "document_title": snapshot["title"],
            "confirmed_topics": topic_context}
    # 摘要读取正文，不让模型重述分块编号；原始来源仍完整保存在 catalog。
    sources = [{"heading": s["heading"], "text": s["text"]}
               for segment in segments for s in model_sources(segment)]
    if not sources:
        raise WikiError("WIKI_SOURCE_INCOMPLETE", retryable=False)
    if any(_request_size({**base, "sources": [source]}) > budget for source in sources):
        base["confirmed_topics"] = []
    allowed_topics = {topic["topic_ref"] for topic in base["confirmed_topics"]}

    def generate(data):
        content = model.text(SUMMARY_STAGE, data)
        # 文本安全和链接校验独立于编号覆盖；坏链接或空内容不能被当成正常摘要。
        try:
            validate_markdown(content, catalog, allowed_topics)
        except ValueError as exc:
            raise WikiError("WIKI_MODEL_OUTPUT_INVALID", diagnostics={
                "stage": SUMMARY_STAGE, "error_message": str(exc), "raw_output": content,
            }) from exc
        return content

    def build(values):
        return {**base, "sources": values}
    groups = list(_groups(sources, build, budget))
    outputs = parallel([lambda group=group: generate(build(group)) for group in groups],
                       model.settings)
    for _ in range(MAX_SUMMARY_LEVELS):
        if len(outputs) == 1:
            break
        def build_summaries(values):
            return {**base, "summaries": values}
        groups = list(_groups(outputs, build_summaries, budget))
        previous_size = tokens(json.dumps(outputs, ensure_ascii=False))
        merged = parallel(
            [lambda group=group, build=build_summaries: generate(build(group)) for group in groups],
            model.settings,
        )
        if len(merged) >= len(outputs) and tokens(json.dumps(merged, ensure_ascii=False)) >= (
            previous_size
        ):
            raise WikiError("WIKI_COMPACTION_BUDGET_EXCEEDED", retryable=False)
        outputs = merged
    if len(outputs) != 1:
        raise WikiError("WIKI_COMPACTION_BUDGET_EXCEEDED", retryable=False)

    lines = outputs[0].strip().splitlines()
    if lines[0].upper().startswith("SUMMARY:"):
        summary_line = lines[0].split(":", 1)[1].strip()
        body = "\n".join(lines[1:]).strip()
    else:
        summary_line = next((line.lstrip("# ").strip() for line in lines
                             if re.search(r"\w", line)), snapshot["title"])
        body = outputs[0].strip()
    if not body:
        raise WikiError("WIKI_MODEL_OUTPUT_INVALID", diagnostics={
            "stage": SUMMARY_STAGE, "error_message": "模型摘要正文为空",
            "raw_output": outputs[0],
        })
    return {
        "summary_line": (summary_line or snapshot["title"])[:SUMMARY_LINE_CHARS],
        "summary_markdown": body,
        # 兼容已有素材与 Reduce 的来源关联字段；这不是模型逐条覆盖的证明。
        "source_refs": list(catalog),
    }
