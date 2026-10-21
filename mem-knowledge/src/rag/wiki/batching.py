"""Partition complete inputs and run independent model work with bounded concurrency."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from .config import WikiError
from .model import request_tokens, tokens


def optional_items(items, budget):
    """Optional navigation/context may be omitted; original evidence is never passed here."""
    selected = []
    for item in items:
        if tokens([*selected, item]) <= budget:
            selected.append(item)
    return selected


def batches(items, *, stage, build, schema, settings):
    group, emitted = [], False
    for item in items:
        proposed = [*group, item]
        if request_tokens(stage, build(proposed), schema) > settings.kb_wiki_input_tokens:
            if group:
                yield group
                emitted = True
                group = []
            if request_tokens(stage, build([item]), schema) > settings.kb_wiki_input_tokens:
                raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False)
        group.append(item)
    if group or not emitted:
        yield group


def parallel(operations, settings):
    """Only dict/scalar model inputs cross threads; preserve attribution ContextVars."""
    if not operations:
        return []
    workers = min(len(operations), getattr(settings, "kb_wiki_parallelism", 4))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wiki-model") as pool:
        futures = [pool.submit(copy_context().run, operation) for operation in operations]
        try:
            return [future.result() for future in futures]
        except Exception:
            for future in futures:
                future.cancel()
            raise
