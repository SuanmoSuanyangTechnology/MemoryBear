"""Render and execute the P1/P2/P3 preference prompts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Template
from pydantic import ValidationError

from .models import (
    PreferenceIdentification,
    PreferenceItem,
    PreferenceOperations,
    PreferenceRecallClassification,
    PreferenceRecallTarget,
)
from .ontology_registry import PreferenceOntology

_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_ONTOLOGY_ATTEMPTS = 3


class PreferencePromptRunner:
    def __init__(self, llm_client: Any, ontology: PreferenceOntology):
        self.llm_client = llm_client
        self.ontology = ontology
        self._p1 = Template(
            (_PROMPT_DIR / "p1_identify_preferences.jinja2").read_text(encoding="utf-8")
        )
        self._p2 = Template(
            (_PROMPT_DIR / "p2_plan_preference_update.jinja2").read_text(encoding="utf-8")
        )
        self._p3 = Template(
            (_PROMPT_DIR / "p3_route_preference_recall.jinja2").read_text(
                encoding="utf-8"
            )
        )

    async def identify(
        self,
        target_message: dict,
        history_messages: list[dict],
    ) -> list:
        prompt = self._p1.render(
            subjects_json=self.ontology.subjects_json,
            situation_keys_json=self.ontology.situation_keys_json,
            context_messages_json=json.dumps(
                [
                    {"role": item["role"], "content": item.get("content", "")}
                    for item in history_messages
                ],
                ensure_ascii=False,
            ),
            current_user_message_json=json.dumps(
                {"role": target_message["role"], "content": target_message.get("content", "")},
                ensure_ascii=False,
            ),
        )
        # 传输异常由模型客户端按 max_retries=2 处理；这里只重试已经成功返回、
        # 但值超出动态本体的 P1 结果，避免两层重试相乘。
        best_valid: list = []
        for _ in range(_MAX_ONTOLOGY_ATTEMPTS):
            result = await self.llm_client.call_structured(
                prompt,
                PreferenceIdentification,
                strict=True,
            )
            parsed = (
                result
                if isinstance(result, PreferenceIdentification)
                else PreferenceIdentification.model_validate(result)
            )
            valid = [
                item
                for item in parsed.preferences
                if item.subject in self.ontology.subjects
                and item.situation_key in self.ontology.situation_keys
            ]
            if len(valid) == len(parsed.preferences):
                return valid
            if len(valid) > len(best_valid):
                best_valid = valid
        if best_valid:
            return best_valid
        raise ValueError("P1 output contains values outside the preference ontology")

    async def plan_update(
        self,
        existing_items: list[PreferenceItem],
        incoming: PreferenceItem,
    ) -> PreferenceOperations:
        prompt = self._p2.render(
            existing_node_json=json.dumps(
                {"preference_items": [item.model_dump() for item in existing_items]},
                ensure_ascii=False,
            ),
            incoming_preference_json=json.dumps(
                {"preference_items": [incoming.model_dump()]}, ensure_ascii=False
            ),
        )
        result = await self.llm_client.call_structured(
            prompt,
            PreferenceOperations,
            strict=True,
        )
        return (
            result
            if isinstance(result, PreferenceOperations)
            else PreferenceOperations.model_validate(result)
        )

    async def route_recall(
        self,
        *,
        current_user_message: str,
        context_messages: list[dict],
    ) -> list[PreferenceRecallTarget]:
        """Classify a task into validated, closed-set preference buckets."""
        prompt = self._p3.render(
            current_user_message_json=json.dumps(
                current_user_message, ensure_ascii=False
            ),
            context_messages_json=json.dumps(
                context_messages, ensure_ascii=False
            ),
            subjects_json=self.ontology.subjects_json,
            situation_keys_json=self.ontology.situation_keys_json,
        )

        best_valid: list[PreferenceRecallTarget] = []
        last_validation_error: Exception | None = None
        for _ in range(_MAX_ONTOLOGY_ATTEMPTS):
            # Provider/transport failures are already retried by the model client.
            # Only a successfully returned but invalid P3 payload is retried here.
            result = await self.llm_client.call_structured(
                prompt,
                PreferenceRecallClassification,
                strict=True,
            )
            try:
                parsed = (
                    result
                    if isinstance(result, PreferenceRecallClassification)
                    else PreferenceRecallClassification.model_validate(result)
                )
                seen: set[tuple[str, str]] = set()
                valid: list[PreferenceRecallTarget] = []
                has_invalid = False
                for target in parsed.targets:
                    key = (target.subject, target.situation_key)
                    if key in seen:
                        has_invalid = True
                        continue
                    seen.add(key)
                    if (
                        target.subject not in self.ontology.subjects
                        or target.situation_key not in self.ontology.situation_keys
                    ):
                        has_invalid = True
                        continue
                    valid.append(target)

                if not has_invalid:
                    return valid
                if len(valid) > len(best_valid):
                    best_valid = valid
                last_validation_error = ValueError(
                    "P3 output contains duplicate or out-of-ontology targets"
                )
            except (ValidationError, ValueError, TypeError) as exc:
                last_validation_error = exc

        if best_valid:
            return best_valid
        raise ValueError("P3 output is invalid") from last_validation_error
