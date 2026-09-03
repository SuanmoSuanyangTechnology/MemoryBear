"""TripletExtractionStep — critical step for extracting entities and triplets.

Replaces the legacy ``TripletExtractor`` with the unified ExtractionStep paradigm.
Predicate filtering against the ontology whitelist is performed in ``parse_response``.
"""

import logging
from typing import Any

from app.core.memory.enums import TripletPredicate
from app.core.memory.models.triplet_models import TripletExtractionResponse
from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
    _USER_PLACEHOLDER_NAMES,
)
from app.core.memory.utils.data.ontology import PREDICATE_DEFINITIONS
from app.core.memory.utils.prompt.prompt_utils import render_triplet_extraction_prompt

from .base import ExtractionStep, StepContext
from .schema import EntityItem, TripletItem, TripletStepInput, TripletStepOutput

logger = logging.getLogger(__name__)

# 「别名属于」谓词的 ID 与 canonical 名称（与 extract_triplet.jinja2 本体 ID 映射一致）
_ALIAS_OF_ID = TripletPredicate.ALIAS_OF.predicate_id
_ALIAS_OF_NAME = TripletPredicate.ALIAS_OF.predicate


def _flip_user_alias_direction(triplet: TripletItem) -> TripletItem:
    """当用户实体被放在「别名属于」的别名端时，手动反转三元组方向。

    「别名属于」的方向约束是 ``alias -> 别名属于 -> canonical entity``，
    用户永远是规范实体，不应作为其它实体的别名。若 LLM 输出
    ``用户 -> 别名属于 -> X``，视为方向错误，反转为 ``X -> 别名属于 -> 用户``。
    两端都是用户实体时不反转（同名两端不满足别名关系约束，交由下游处理）。

    Args:
        triplet: 解析后的三元组

    Returns:
        反转后的三元组；无需反转时原样返回
    """
    if triplet.predicate_id != _ALIAS_OF_ID and triplet.predicate != _ALIAS_OF_NAME:
        return triplet

    subject_is_user = (triplet.subject_name or "").strip().lower() in _USER_PLACEHOLDER_NAMES
    object_is_user = (triplet.object_name or "").strip().lower() in _USER_PLACEHOLDER_NAMES
    if not (subject_is_user and not object_is_user):
        return triplet

    logger.info(
        f"[TripletExtractionStep] 反转「别名属于」方向（用户不能作为别名）: "
        f"({triplet.subject_name}) -[{triplet.predicate}]-> ({triplet.object_name}) "
        f"==> ({triplet.object_name}) -[{triplet.predicate}]-> ({triplet.subject_name})"
    )
    return TripletItem(
        subject_name=triplet.object_name,
        subject_id=triplet.object_id,
        predicate=triplet.predicate,
        predicate_id=triplet.predicate_id,
        predicate_surface=triplet.predicate_surface,
        predicate_description=triplet.predicate_description,
        object_name=triplet.subject_name,
        object_id=triplet.subject_id,
    )


class TripletExtractionStep(ExtractionStep[TripletStepInput, TripletStepOutput]):
    """Extract knowledge triplets and entities from a single statement.

    This is a **critical** step — failure aborts the pipeline after retries.

    Config params bound at init (from ``StepContext.config``):
        * ``ontology_types`` — predefined ontology types for entity classification
        * ``predicate_instructions`` — predicate definition guidance for the LLM
        * ``json_schema`` — JSON schema for the expected LLM output
    """

    def __init__(
        self,
        context: StepContext,
        ontology_types: Any = None,
    ) -> None:
        super().__init__(context)
        self.ontology_types = ontology_types
        self.predicate_instructions = PREDICATE_DEFINITIONS
        self.json_schema = TripletExtractionResponse.model_json_schema()

    # ── Identity ──

    @property
    def name(self) -> str:
        return "triplet_extraction"

    @property
    def is_critical(self) -> bool:
        return True

    # ── Lifecycle ──

    async def render_prompt(self, input_data: TripletStepInput) -> str:
        ctx = input_data.supporting_context
        # Build chunk_content from supporting_context for pronoun resolution.
        # Preserve before → after ordering so the LLM sees the natural temporal
        # flow with the statement_text sitting logically between the two halves.
        chunk_parts: list[str] = []
        if ctx.before_msgs:
            chunk_parts.append(
                "\n".join(f"{m.role}: {m.msg}" for m in ctx.before_msgs)
            )
        if ctx.after_msgs:
            chunk_parts.append(
                "\n".join(f"{m.role}: {m.msg}" for m in ctx.after_msgs)
            )
        chunk_content = "\n---\n".join(chunk_parts)

        input_json = {
            "statement_id": input_data.statement_id,
            "statement_text": input_data.statement_text,
            "statement_type": input_data.statement_type,
            "temporal_type": input_data.temporal_type,
            "supporting_context": {
                "before_msgs": [
                    {"role": m.role, "msg": m.msg} for m in ctx.before_msgs
                ],
                "after_msgs": [
                    {"role": m.role, "msg": m.msg} for m in ctx.after_msgs
                ],
            },
            "speaker": input_data.speaker,
            "dialog_at": input_data.dialog_at or "",
            "valid_at": input_data.valid_at,
            "invalid_at": input_data.invalid_at,
            "has_unsolved_reference": input_data.has_unsolved_reference,
        }

        return await render_triplet_extraction_prompt(
            statement=input_data.statement_text,
            chunk_content=chunk_content,
            json_schema=self.json_schema,
            predicate_instructions=self.predicate_instructions,
            language=self.language,
            ontology_types=self.ontology_types,
            speaker=input_data.speaker,
            input_json=input_json,
            has_unsolved_reference=input_data.has_unsolved_reference,
        )

    async def call_llm(self, prompt: Any) -> Any:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert at extracting knowledge triplets and entities "
                    "from text. Follow the provided instructions carefully and return valid JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        return await self.llm_client.call_structured(
            messages, TripletExtractionResponse
        )

    async def parse_response(
        self, raw_response: Any, input_data: TripletStepInput
    ) -> TripletStepOutput:
        if not hasattr(raw_response, "triplets"):
            return self.get_default_output()

        # Keep raw triplets from LLM output (no predicate whitelist filtering).
        # 逐条校正：用户被误放在「别名属于」别名端时反转方向。
        parsed_triplets = []
        for t in raw_response.triplets:
            triplet = TripletItem(
                subject_name=t.subject_name,
                subject_id=t.subject_id,
                predicate=t.predicate,
                predicate_id=t.predicate_id,
                predicate_surface=t.predicate_surface,
                predicate_description=getattr(t, "predicate_description", ""),
                object_name=t.object_name,
                object_id=t.object_id,
            )
            parsed_triplets.append(_flip_user_alias_direction(triplet))

        entities = [
            EntityItem(
                entity_idx=e.entity_idx,
                name=e.name,
                type=e.type,
                type_id=e.type_id,
                type_description=getattr(e, "type_description", ""),
                description=e.description,
                is_explicit_memory=getattr(e, "is_explicit_memory", False),
            )
            for e in (raw_response.entities or [])
        ]

        return TripletStepOutput(entities=entities, triplets=parsed_triplets)

    def get_default_output(self) -> TripletStepOutput:
        return TripletStepOutput(entities=[], triplets=[])
