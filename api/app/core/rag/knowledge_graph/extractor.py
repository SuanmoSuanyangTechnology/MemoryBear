import asyncio
from typing import Any, Protocol

from app.core.rag.knowledge_graph.models import ExtractionBatch, ExtractionResult
from app.core.rag.knowledge_graph.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_prompt,
)


class EntityRelationExtractor(Protocol):
    async def extract(self, batch: ExtractionBatch) -> ExtractionResult:
        ...


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
                        {
                            "role": "system",
                            "content": EXTRACTION_SYSTEM_PROMPT,
                        },
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
                )
                result = self._validate_result(raw_result, batch)
                return result
            except Exception:
                if attempt == 1:
                    raise
                await asyncio.sleep(0.25)

        raise RuntimeError("unreachable extraction retry state")

    @staticmethod
    def _validate_result(
        raw_result: Any,
        batch: ExtractionBatch,
    ) -> ExtractionResult:
        if isinstance(raw_result, ExtractionResult):
            result = ExtractionResult.model_validate(raw_result.model_dump())
        else:
            result = ExtractionResult.model_validate(raw_result)

        allowed_sources = set(batch.source_chunk_ids)
        refs = [entity.ref for entity in result.entities]
        if len(refs) != len(set(refs)):
            raise ValueError("entity refs must be unique within a batch")
        known_refs = set(refs)

        for entity in result.entities:
            entity_sources = set(entity.source_chunk_ids)
            if not entity_sources or not entity_sources <= allowed_sources:
                raise ValueError("unknown source chunk in entity")

        for relation in result.relations:
            if (
                relation.from_ref not in known_refs
                or relation.to_ref not in known_refs
            ):
                raise ValueError("relation endpoint is not present in entities")
            relation_sources = set(relation.source_chunk_ids)
            if not relation_sources or not relation_sources <= allowed_sources:
                raise ValueError("unknown source chunk in relation")

        return result
