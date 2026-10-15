"""Bounded structured calls through the existing knowledge model runtime."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from pydantic import ValidationError

from ...integrations.model.chat import RedBearChatModel
from ...integrations.model.invoke_backend import RemoteInvokeRef
from ..chunk.token_utils import get_encoder
from .config import WikiError
from .diagnostics import diagnostic_json, exception_details
from .prompts import COMMON, TEMPLATES

logger = logging.getLogger(__name__)
_MAX_REPAIR_ERROR_CHARS = 1500


def tokens(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return len(get_encoder().encode(text, disallowed_special=()))


def system_prompt(stage, schema):
    return (COMMON + "\n" + TEMPLATES[stage] + "\nOutput JSON schema:\n"
            + json.dumps(schema.model_json_schema()))


def request_tokens(stage, data, schema):
    return tokens(system_prompt(stage, schema)) + tokens(data)


class WikiModel:
    def __init__(self, runtime, ref: RemoteInvokeRef, *, model=None):
        self.settings = runtime.settings
        if model is None:
            # Initialize the existing process-owned client before parallel first calls.
            try:
                _ = runtime.model_runtime.invoke_sync_client
            except Exception as exc:
                from ...error_mapping import map_model_error

                mapped = map_model_error(exc)
                raise WikiError(
                    mapped.code, retryable=mapped.retryable,
                    diagnostics={"stage": "model_init", "model_id": str(ref.config_id)},
                ) from exc
        self.model = model or RedBearChatModel.for_invoke_sync_ref(
            ref,
            pool=runtime.model_runtime,
            params={"max_tokens": self.settings.kb_wiki_output_tokens,
                    "temperature": 0.3, "deep_thinking": False},
        )
        self.model_id = str(ref.config_id)

    def _invoke(self, stage, messages, attempt, continuation=0):
        from ...tasks.observability import current_parent_task_id

        input_size = sum(tokens(m["content"]) for m in messages)
        if input_size > self.settings.kb_wiki_input_tokens:
            raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False, diagnostics={
                "stage": stage, "model_id": self.model_id, "model_attempt": attempt,
                "continuation": continuation, "input_size": input_size,
                "input_limit": self.settings.kb_wiki_input_tokens, "size_unit": "token",
            })
        started = time.monotonic()
        try:
            response = self.model.invoke(messages)
        except Exception as exc:
            from ...error_mapping import map_model_error

            mapped = map_model_error(exc)
            details = {
                **exception_details(exc), "stage": stage, "model_id": self.model_id,
                "model_attempt": attempt, "continuation": continuation,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
            logger.warning(
                "Wiki 模型调用失败 task_id=%s error_code=%s 失败详情=%s",
                current_parent_task_id(), mapped.code, diagnostic_json(details),
            )
            raise WikiError(mapped.code, retryable=mapped.retryable, diagnostics=details) from exc
        content = response.content
        if isinstance(content, list):
            content = "".join(block.get("text", "") for block in content
                              if isinstance(block, dict) and block.get("type") == "text")
        metadata = getattr(response, "usage_metadata", None) or {}
        response_metadata = getattr(response, "response_metadata", None) or {}
        finish = response_metadata.get("finish_reason") or response_metadata.get("stop_reason")
        logger.info(
            "Wiki 模型调用完成 task_id=%s stage=%s model_id=%s attempt=%s continuation=%s "
            "duration_ms=%s input_size=%s output_size=%s size_unit=token "
            "input_usage=%s output_usage=%s finish_reason=%s",
            current_parent_task_id(), stage, self.model_id, attempt, continuation,
            int((time.monotonic() - started) * 1000), input_size, tokens(content),
            metadata.get("input_tokens"), metadata.get("output_tokens"),
            str(finish)[:64] if finish else None,
        )
        return content, str(finish or "").strip().lower()

    def _complete(self, stage, system, payload, attempt):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": payload}]
        content, finish = self._invoke(stage, messages, attempt)
        result = content
        limit = (getattr(self.settings, "kb_wiki_continuations", 3)
                 if stage in {"wiki_page_original_v2", "wiki_page_reduce_v1"} else 0)
        round_index = 0
        while finish in {"length", "max_tokens", "max_output_tokens"}:
            if round_index >= limit:
                raise WikiError("WIKI_MODEL_OUTPUT_TRUNCATED", diagnostics={
                    "error_message": "模型输出被截断，已达到当前阶段的续写次数上限",
                    "stage": stage, "model_id": self.model_id, "model_attempt": attempt,
                    "continuation": round_index, "continuation_limit": limit,
                    "finish_reason": finish, "raw_output": result,
                })
            round_index += 1
            messages.extend([
                {"role": "assistant", "content": content},
                {"role": "user", "content": (
                    "Continue exactly from the interrupted JSON byte sequence. Return only its "
                    "remaining tail, no repetition, Markdown fences or restart. If the preceding "
                    "JSON was already complete, return __WIKI_COMPLETE__ only."
                )},
            ])
            content, finish = self._invoke(stage, messages, attempt, round_index)
            if content.strip() == "__WIKI_COMPLETE__" and finish not in {
                "length", "max_tokens", "max_output_tokens"
            }:
                break
            result += content
        return result

    def call(self, stage: str, data: dict, schema, validate: Callable | None = None):
        system = original_system = system_prompt(stage, schema)
        from ...tasks.observability import current_parent_task_id

        original = data
        for attempt in range(self.settings.kb_wiki_repair_attempts + 1):
            payload = json.dumps(data, ensure_ascii=False)
            input_size = tokens(system) + tokens(payload)
            if input_size > self.settings.kb_wiki_input_tokens:
                logger.warning(
                    "Wiki 上下文超出预算 task_id=%s stage=%s attempt=%s "
                    "input_size=%s input_limit=%s size_unit=token",
                    current_parent_task_id(), stage, attempt,
                    input_size, self.settings.kb_wiki_input_tokens,
                )
                raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False, diagnostics={
                    "stage": stage, "model_id": self.model_id, "model_attempt": attempt,
                    "input_size": input_size, "input_limit": self.settings.kb_wiki_input_tokens,
                    "size_unit": "token",
                })
            content = self._complete(stage, system, payload, attempt)
            try:
                result = schema.model_validate(json.loads(content))
                value = result.model_dump()
                if validate:
                    validate(value)
                return value
            except (ValidationError, ValueError, TypeError) as exc:
                errors = (
                    exc.errors(include_url=False, include_context=False, include_input=False)
                    if isinstance(exc, ValidationError) else None
                )
                details = {
                    **exception_details(exc), "stage": stage, "model_id": self.model_id,
                    "model_attempt": attempt, "repair_limit": self.settings.kb_wiki_repair_attempts,
                    "schema": schema.__name__, "raw_output": content,
                }
                logger.warning(
                    "Wiki 模型输出校验失败 task_id=%s 失败详情=%s",
                    current_parent_task_id(), diagnostic_json(details),
                )
                if attempt >= self.settings.kb_wiki_repair_attempts:
                    raise WikiError("WIKI_MODEL_OUTPUT_INVALID", diagnostics=details) from exc
                # 修复仍使用完整输出；日志中的凭据脱敏不改变模型收到的修复材料。
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
