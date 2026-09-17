"""Preference Processor used by the WritePipeline preference branch."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app.repositories.neo4j.preference_repository import PreferenceRepository

from .keyword_gate import match_preference_keywords
from .models import (
    IdentifiedPreference,
    PreferenceItem,
    PreferenceOperations,
    PreferenceProcessorResult,
)
from .ontology_registry import load_preference_ontology
from .prompt_runner import PreferencePromptRunner

logger = logging.getLogger(__name__)


class InvalidPreferenceOperations(ValueError):
    pass


class PreferenceProcessor:
    def __init__(
        self,
        *,
        memory_config: Any,
        end_user_id: str,
        llm_client: Any,
        connector: Any,
    ):
        self.memory_config = memory_config
        self.end_user_id = end_user_id
        self.ontology = load_preference_ontology("coding")
        self.runner = PreferencePromptRunner(llm_client, self.ontology)
        self.repository = PreferenceRepository(connector)

    async def run(
        self,
        *,
        target_message: dict,
        history_messages: list[dict],
    ) -> PreferenceProcessorResult:
        gate = match_preference_keywords(
            target_message.get("content", ""),
            list(getattr(self.memory_config, "preference_custom_keywords", ())),
        )
        logger.info(
            "[PreferenceGate] message_id=%s matched=%s terms=%s sources=%s",
            target_message.get("memory_message_id"),
            gate.matched,
            list(gate.matched_terms),
            gate.matched_sources,
        )
        if not gate.matched:
            return PreferenceProcessorResult(status="skipped", reason="keyword_gate_miss")

        identified = await self.runner.identify(
            target_message,
            history_messages,
        )
        identified = self._deduplicate_identified(identified)
        if not identified:
            return PreferenceProcessorResult(status="skipped", reason="no_preferences")

        for index, item in enumerate(identified):
            logger.info(
                "[PreferenceTrace] trace_id=%s:%s domain=%s subject=%s situation_key=%s source_message=%r",
                target_message.get("memory_message_id"),
                index,
                self.ontology.domain,
                item.subject,
                item.situation_key,
                target_message.get("content", ""),
            )

        groups: dict[tuple[str, str], list[IdentifiedPreference]] = defaultdict(list)
        for item in identified:
            groups[(item.subject, item.situation_key)].append(item)

        created = updated = noop_items = 0
        for (subject, situation_key), incoming in groups.items():
            outcome = await self._write_group(subject, situation_key, incoming)
            created += outcome[0]
            updated += outcome[1]
            noop_items += outcome[2]

        return PreferenceProcessorResult(
            status="success",
            identified_count=len(identified),
            created_count=created,
            updated_count=updated,
            noop_item_count=noop_items,
        )

    async def _write_group(
        self,
        subject: str,
        situation_key: str,
        incoming: list[IdentifiedPreference],
    ) -> tuple[int, int, int]:
        incoming_items = [
            PreferenceItem(mode=item.mode, preference_text=item.preference_text)
            for item in incoming
        ]
        node = await self.repository.get(
            end_user_id=self.end_user_id,
            domain=self.ontology.domain,
            subject=subject,
            situation_key=situation_key,
        )
        if node is None:
            node, created = await self.repository.create_if_absent(
                end_user_id=self.end_user_id,
                domain=self.ontology.domain,
                subject=subject,
                situation_key=situation_key,
                items=incoming_items,
            )
            if created:
                return 1, 0, 0

        working = [
            PreferenceItem(mode=mode, preference_text=text)
            for mode, text in zip(node.mode, node.preference_text, strict=True)
        ]
        changed = False
        noop_item_count = 0
        for incoming_item in incoming_items:
            operations = await self.runner.plan_update(
                working,
                incoming_item,
            )
            if operations.is_noop:
                noop_item_count += 1
                continue
            working = apply_preference_operations(working, incoming_item, operations)
            changed = True

        if not changed:
            return 0, 0, noop_item_count
        saved = await self.repository.update(node, working)
        if saved is None:
            logger.warning(
                "[Preference] node disappeared before update end_user_id=%s "
                "domain=%s subject=%s situation_key=%s",
                self.end_user_id,
                self.ontology.domain,
                subject,
                situation_key,
            )
            return 0, 0, noop_item_count
        return 0, 1, noop_item_count

    @staticmethod
    def _deduplicate_identified(
        items: list[IdentifiedPreference],
    ) -> list[IdentifiedPreference]:
        result: list[IdentifiedPreference] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            key = (item.subject, item.situation_key, item.preference_text)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result


def apply_preference_operations(
    existing: list[PreferenceItem],
    incoming: PreferenceItem,
    operations: PreferenceOperations,
) -> list[PreferenceItem]:
    """Validate a complete P2 batch, then apply it against the pre-operation snapshot."""
    positions: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(existing):
        positions[item.preference_text].append(index)

    targets: set[int] = set()
    updates: dict[int, PreferenceItem] = {}
    deletes: set[int] = set()

    for operation in operations.update:
        indices = positions.get(operation.old_text, [])
        if len(indices) != 1:
            raise InvalidPreferenceOperations(
                "update.old_text must uniquely match the snapshot"
            )
        index = indices[0]
        if index in targets:
            raise InvalidPreferenceOperations("an existing item cannot be targeted twice")
        if operation.new_item != incoming:
            raise InvalidPreferenceOperations("update.new_item must exactly equal incoming")
        targets.add(index)
        updates[index] = operation.new_item

    for operation in operations.delete:
        indices = positions.get(operation.old_text, [])
        if len(indices) != 1:
            raise InvalidPreferenceOperations(
                "delete.old_text must uniquely match the snapshot"
            )
        index = indices[0]
        if index in targets:
            raise InvalidPreferenceOperations("an existing item cannot be targeted twice")
        targets.add(index)
        deletes.add(index)

    additions: list[PreferenceItem] = []
    for operation in operations.add:
        if operation.new_item != incoming:
            raise InvalidPreferenceOperations("add.new_item must exactly equal incoming")
        additions.append(operation.new_item)

    result: list[PreferenceItem] = []
    for index, item in enumerate(existing):
        if index in deletes:
            continue
        result.append(updates.get(index, item))
    result.extend(additions)
    texts = [item.preference_text for item in result]
    if len(texts) != len(set(texts)):
        raise InvalidPreferenceOperations(
            "operations would create duplicate preference_text values"
        )
    return result
