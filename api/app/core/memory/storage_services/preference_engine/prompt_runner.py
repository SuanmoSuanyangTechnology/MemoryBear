"""Render and execute the P1/P2 preference prompts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Template

from .models import PreferenceIdentification, PreferenceItem, PreferenceOperations
from .ontology_registry import PreferenceOntology

_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_ONTOLOGY_ATTEMPTS = 3


class PreferencePromptRunner:
    def __init__(self, llm_client: Any, ontology: PreferenceOntology):
        self.llm_client = llm_client
        self.ontology = ontology
        self._p1 = Template((_PROMPT_DIR / "p1_v0.5.0.jinja2").read_text(encoding="utf-8"))
        self._p2 = Template((_PROMPT_DIR / "p2_v0.4.1.jinja2").read_text(encoding="utf-8"))

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
