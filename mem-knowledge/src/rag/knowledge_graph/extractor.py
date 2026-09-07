"""LLM-backed entity and relation extraction for Evidence Graph."""

import asyncio
import unicodedata
from typing import Any, Protocol

from redbear_model.errors import is_provider_rate_limit_error

from .models import ExtractionBatch, ExtractionResult
from .prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_prompt
from .structured_output import unwrap_structured_result


class EntityRelationExtractor(Protocol):
    async def extract(self, batch: ExtractionBatch) -> ExtractionResult: ...


class LLMEntityRelationExtractor:
    def __init__(
        self,
        llm: Any,
        entity_types: tuple[str, ...],
        scene_name: str,
    ) -> None:
        self._llm = llm
        self._entity_types = entity_types
        self._scene_name = scene_name

    async def extract(self, batch: ExtractionBatch) -> ExtractionResult:
        for attempt in range(2):
            try:
                raw_result = await self._llm.call_structured(
                    [
                        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": build_extraction_prompt(
                                batch.text,
                                self._entity_types,
                                self._scene_name,
                            ),
                        },
                    ],
                    ExtractionResult,
                    include_raw=True,
                )
                return self._validate_result(
                    unwrap_structured_result(raw_result, ExtractionResult),
                    batch,
                )
            except Exception as exc:
                if is_provider_rate_limit_error(exc) or attempt == 1:
                    raise
                await asyncio.sleep(0.25)
        raise RuntimeError("unreachable extraction retry state")

    @staticmethod
    def _validate_result(raw_result: Any, batch: ExtractionBatch) -> ExtractionResult:
        if isinstance(raw_result, ExtractionResult):
            result = ExtractionResult.model_validate(raw_result.model_dump())
        else:
            result = ExtractionResult.model_validate(raw_result)
        if len(batch.source_chunk_ids) != 1:
            raise ValueError("extraction batch must contain exactly one source chunk")
        current_source_id = batch.source_chunk_ids[0]
        refs = [entity.ref for entity in result.entities]
        if len(refs) != len(set(refs)):
            raise ValueError("entity refs must be unique within a batch")
        known_refs = set(refs)
        entities_by_ref = {entity.ref: entity for entity in result.entities}
        for entity in result.entities:
            entity.source_chunk_ids = [current_source_id]
        valid_relations = []
        for relation in result.relations:
            if relation.from_ref not in known_refs or relation.to_ref not in known_refs:
                continue
            relation.source_chunk_ids = [current_source_id]
            from_entity = entities_by_ref[relation.from_ref]
            to_entity = entities_by_ref[relation.to_ref]
            relation.predicate = LLMEntityRelationExtractor._clean_text(relation.predicate)
            relation.description = LLMEntityRelationExtractor._clean_text(relation.description)
            if (
                relation.from_ref == relation.to_ref
                or LLMEntityRelationExtractor._identity_text(from_entity.name)
                == LLMEntityRelationExtractor._identity_text(to_entity.name)
                or not relation.predicate
                or not relation.description
            ):
                continue
            relation.keywords = LLMEntityRelationExtractor._clean_keywords(relation.keywords)
            valid_relations.append(relation)
        result.relations = valid_relations
        return result

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", str(value)).split()).strip()

    @staticmethod
    def _identity_text(value: str) -> str:
        return "".join(LLMEntityRelationExtractor._clean_text(value).casefold().split())

    @staticmethod
    def _clean_keywords(values: list[str]) -> list[str]:
        return [
            keyword
            for value in values
            if (keyword := LLMEntityRelationExtractor._clean_text(value))
        ]


__all__ = ["EntityRelationExtractor", "LLMEntityRelationExtractor"]
