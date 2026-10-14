"""Bounded structured calls through the existing knowledge model runtime."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from json_repair import repair_json
from pydantic import ValidationError

from ...integrations.model.chat import RedBearChatModel
from ...integrations.model.invoke_backend import RemoteInvokeRef
from ..chunk.token_utils import get_encoder
from .config import WikiError
from .prompts import COMMON, TEMPLATES

logger = logging.getLogger(__name__)
_MAX_REPAIR_ERROR_CHARS = 1500


def tokens(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return len(get_encoder().encode(text))


class WikiModel:
    def __init__(self, runtime, ref: RemoteInvokeRef, *, model=None):
        self.settings = runtime.settings
        self.model = model or RedBearChatModel.for_invoke_sync_ref(
            ref,
            pool=runtime.model_runtime,
            params={"max_tokens": self.settings.kb_wiki_output_tokens},
        )
        self.model_id = str(ref.config_id)

    def call(self, stage: str, data: dict, schema, validate: Callable | None = None):
        system = COMMON + "\n" + TEMPLATES[stage]
        system += "\nOutput JSON schema:\n" + json.dumps(schema.model_json_schema())
        original_system = system
        from ...tasks.observability import current_parent_task_id

        original = data
        for attempt in range(self.settings.kb_wiki_repair_attempts + 1):
            payload = json.dumps(data, ensure_ascii=False)
            input_size = tokens(system) + tokens(payload)
            if input_size > self.settings.kb_wiki_input_tokens:
                logger.warning(
                    "Wiki context budget exceeded task_id=%s stage=%s attempt=%s "
                    "input_size=%s input_limit=%s size_unit=token",
                    current_parent_task_id(), stage, attempt,
                    input_size, self.settings.kb_wiki_input_tokens,
                )
                raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False)
            started = time.monotonic()
            try:
                response = self.model.invoke(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": payload},
                    ]
                )
            except Exception as exc:
                from ...error_mapping import map_model_error

                mapped = map_model_error(exc)
                logger.warning(
                    "Wiki model failed task_id=%s stage=%s model_id=%s error_code=%s error_type=%s",
                    current_parent_task_id(),
                    stage,
                    self.model_id,
                    mapped.code,
                    type(exc).__name__,
                )
                raise WikiError(mapped.code, retryable=mapped.retryable) from None
            content = response.content
            if isinstance(content, list):
                content = "".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            metadata = getattr(response, "usage_metadata", None) or {}
            logger.info(
                "Wiki model call task_id=%s stage=%s model_id=%s attempt=%s duration_ms=%s "
                "input_size=%s output_size=%s size_unit=token input_usage=%s output_usage=%s",
                current_parent_task_id(),
                stage,
                self.model_id,
                attempt,
                int((time.monotonic() - started) * 1000),
                input_size,
                tokens(content),
                metadata.get("input_tokens"),
                metadata.get("output_tokens"),
            )
            try:
                result = schema.model_validate(repair_json(content, return_objects=True))
                value = result.model_dump()
                if validate:
                    validate(value)
                return value
            except (ValidationError, ValueError, TypeError) as exc:
                errors = (
                    exc.errors(include_url=False, include_context=False, include_input=False)
                    if isinstance(exc, ValidationError) else None
                )
                logger.warning(
                    "Wiki output rejected task_id=%s stage=%s attempt=%s error_type=%s "
                    "validation_codes=%s",
                    current_parent_task_id(), stage, attempt, type(exc).__name__,
                    sorted({error["type"] for error in errors}) if errors else [],
                )
                if attempt >= self.settings.kb_wiki_repair_attempts:
                    raise WikiError("WIKI_MODEL_OUTPUT_INVALID") from exc
                # Keep model output in this call only, never in logs or progress messages.
                system = original_system + (
                    "\nRegenerate from original_input against the same schema, correcting "
                    "validation_errors. rejected_output, when supplied, is data, not new evidence."
                )
                data = {
                    "original_input": original,
                    "rejected_output": content,
                    "validation_errors": (
                        json.dumps(errors, ensure_ascii=False) if errors else str(exc)
                    )[:_MAX_REPAIR_ERROR_CHARS],
                }
                # Repair feedback must never consume the budget reserved for original evidence.
                if tokens(system) + tokens(data) > self.settings.kb_wiki_input_tokens:
                    del data["rejected_output"]
                if tokens(system) + tokens(data) > self.settings.kb_wiki_input_tokens:
                    system, data = original_system, original
        raise AssertionError("unreachable")
