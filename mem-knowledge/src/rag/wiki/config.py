"""Wiki configuration, bounded execution and stable content fingerprints."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

TEMPLATE_VERSION = "wiki_raw_evidence_v2"
MAP_TASK = "mem_knowledge.tasks.sync_wiki_document"
REDUCE_TASK = "mem_knowledge.tasks.reduce_wiki_page"
BACKFILL_TASK = "mem_knowledge.tasks.enqueue_unprocessed_wiki_documents"
WIKI_QUEUE = "wiki_tasks"
ACTIVE_STATUSES = frozenset({"pending", "running", "retry_wait"})
TERMINAL_TASK_STATUSES = frozenset({"succeeded", "failed", "cancelled", "superseded"})
MAX_LANGUAGE_LENGTH = 64
MAX_EXTRACT_PROMPT_LENGTH = 12000
TYPE_GROUP_NAMES = frozenset({"知识", "摘要", "knowledge", "summary", "summaries"})
# Opportunistic cleanup bounds diagnostic history without a periodic scheduler.
TASK_RETENTION_DAYS = 30
TASK_CLEANUP_BATCH_SIZE = 1000


class WikiError(RuntimeError):
    """Bounded worker failure with a safe, stable public code."""

    def __init__(self, code: str, *, retryable: bool = True):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class WikiDeferred(WikiError):
    """Contention or an incomplete source; separate from model failure budget."""


class WikiSuperseded(WikiError):
    """An old delivery has no authority over the current processing round."""


@dataclass(frozen=True)
class WikiConfig:
    enabled: bool = False
    language: str = "zh-CN"
    extract_prompt: str = ""


def read_wiki_config(parser_config: Mapping[str, Any] | None) -> WikiConfig:
    value = (parser_config or {}).get("wiki", {})
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("wiki must be an object")
    enabled = value.get("enabled", False)
    language = value.get("language", "zh-CN")
    prompt = value.get("extract_prompt", "")
    if not isinstance(enabled, bool):
        raise ValueError("wiki.enabled must be a boolean")
    if not isinstance(language, str) or not 0 < len(language.strip()) <= MAX_LANGUAGE_LENGTH:
        raise ValueError("wiki.language must be a non-empty language identifier")
    if not isinstance(prompt, str) or len(prompt) > MAX_EXTRACT_PROMPT_LENGTH:
        raise ValueError("wiki.extract_prompt is invalid")
    return WikiConfig(enabled, language.strip(), prompt.strip())


def merge_wiki_config(current: Mapping | None, incoming: Mapping | None) -> dict:
    if incoming is not None and not isinstance(incoming, Mapping):
        raise ValueError("wiki update must be an object")
    value = asdict(read_wiki_config(current))
    value.update({key: v for key, v in (incoming or {}).items() if key in value})
    return asdict(read_wiki_config({"wiki": value}))


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def generation_config_hash(llm_id: Any, parser_config: Mapping | None) -> str:
    config = read_wiki_config(parser_config)
    return fingerprint(
        {
            "llm_id": str(llm_id),
            "language": config.language,
            "extract_prompt": config.extract_prompt,
            "template": TEMPLATE_VERSION,
        }
    )


def normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def source_reference_key(ref: str):
    """Numeric S handles preserve the original document order beyond nine pieces."""
    return (0, int(ref[1:])) if ref.startswith("S") and ref[1:].isdigit() else (1, ref)
