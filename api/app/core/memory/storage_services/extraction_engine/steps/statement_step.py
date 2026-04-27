"""StatementExtractionStep — critical step for extracting statements from chunks.

Replaces the legacy ``StatementExtractor`` with the unified ExtractionStep paradigm.
Temporal extraction logic (valid_at / invalid_at) is merged into this step,
eliminating the need for a separate ``TemporalExtractor`` call.
"""

import logging
import uuid
from typing import Any, List

from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.core.memory.utils.data.ontology import LABEL_DEFINITIONS
from app.core.memory.utils.prompt.prompt_utils import render_statement_extraction_prompt

from .base import ExtractionStep, StepContext
from .schema import StatementStepInput, StatementStepOutput

logger = logging.getLogger(__name__)


# ── LLM response schemas (internal) ──


class _ExtractedStatement(BaseModel):
    """Raw statement returned by the LLM (before enrichment)."""

    statement: str = Field(
        ...,
        validation_alias=AliasChoices("statement", "statement_text"),
        description="The extracted statement text",
    )
    statement_type: str = Field(..., description="FACT / OPINION / OTHER")
    temporal_type: str = Field(..., description="STATIC / DYNAMIC / ATEMPORAL")
    # relevance: str = Field("RELEVANT", description="RELEVANT / IRRELEVANT")
    has_emotional_state: bool = Field(
        False,
        description="Whether the statement reflects user's emotional state",
    )
    valid_at: str = Field("NULL", description="ISO 8601 or NULL")
    invalid_at: str = Field("NULL", description="ISO 8601 or NULL")
    has_unsolved_reference: bool = Field(False, description="Whether the statement has unresolved references")


class _StatementExtractionResponse(BaseModel):
    """Structured LLM response containing a list of extracted statements."""

    statements: List[_ExtractedStatement] = Field(default_factory=list)

    @field_validator("statements", mode="before")
    @classmethod
    def filter_empty(cls, v: Any) -> Any:
        """Drop empty / malformed dicts that the LLM occasionally produces."""
        if isinstance(v, list):
            return [
                s
                for s in v
                if isinstance(s, dict)
                and (s.get("statement") or s.get("statement_text"))
            ]
        return v


class StatementExtractionStep(ExtractionStep[StatementStepInput, List[StatementStepOutput]]):
    """Extract atomic statements (with temporal info) from a dialogue chunk.

    This is a **critical** step — failure aborts the pipeline after retries.

    Config params bound at init (from ``StepContext.config.statement_extraction``):
        * ``definitions`` — label definitions for statement classification
        * ``json_schema`` — JSON schema for the expected LLM output
        * ``granularity`` — extraction granularity level (1-3)
        * ``include_dialogue_context`` — whether to include full dialogue context
    """

    def __init__(self, context: StepContext) -> None:
        super().__init__(context)
        stmt_cfg = getattr(self.config, "statement_extraction", None)
        self.definitions = LABEL_DEFINITIONS
        self.json_schema = _ExtractedStatement.model_json_schema()
        self.granularity = getattr(stmt_cfg, "statement_granularity", None)
        self.include_dialogue_context = getattr(stmt_cfg, "include_dialogue_context", True)
        self.max_dialogue_context_chars = getattr(stmt_cfg, "max_dialogue_context_chars", 2000)

    # ── Identity ──

    @property
    def name(self) -> str:
        return "statement_extraction"

    @property
    def is_critical(self) -> bool:
        return True

    # ── Lifecycle ──

    async def render_prompt(self, input_data: StatementStepInput) -> str:
        # Build optional dialogue context from supporting_context messages
        dialogue_content = None
        if self.include_dialogue_context and input_data.supporting_context.msgs:
            dialogue_content = "\n".join(
                f"{m.role}: {m.msg}" for m in input_data.supporting_context.msgs
            )

        input_json = {
            "chunk_id": input_data.chunk_id,
            "end_user_id": input_data.end_user_id,
            "target_content": input_data.target_content,
            "target_message_date": input_data.target_message_date,
            "supporting_context": {
                "msgs": [
                    {"role": m.role, "msg": m.msg}
                    for m in input_data.supporting_context.msgs
                ]
            },
        }

        return await render_statement_extraction_prompt(
            chunk_content=input_data.target_content,
            definitions=self.definitions,
            json_schema=self.json_schema,
            granularity=self.granularity,
            include_dialogue_context=self.include_dialogue_context,
            dialogue_content=dialogue_content,
            max_dialogue_chars=self.max_dialogue_context_chars,
            language=self.language,
            input_json=input_json,
        )

    async def call_llm(self, prompt: Any) -> Any:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert at extracting and labeling atomic statements "
                    "from conversational text. Return valid JSON conforming to the schema."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        return await self.llm_client.response_structured(
            messages, _StatementExtractionResponse
        )

    async def parse_response(
        self, raw_response: Any, input_data: StatementStepInput
    ) -> List[StatementStepOutput]:
        if not hasattr(raw_response, "statements") or raw_response.statements is None:
            return []

        results: List[StatementStepOutput] = []
        for stmt in raw_response.statements:
            results.append(
                StatementStepOutput(
                    statement_id=uuid.uuid4().hex,
                    statement_text=stmt.statement,
                    statement_type=stmt.statement_type.strip().upper(),
                    temporal_type=stmt.temporal_type.strip().upper(),
                    # relevance=stmt.relevance.strip().upper(),
                    speaker="user",  # default; orchestrator overrides from chunk metadata
                    has_emotional_state=getattr(stmt, "has_emotional_state", False),
                    valid_at=stmt.valid_at or "NULL",
                    invalid_at=stmt.invalid_at or "NULL",
                    has_unsolved_reference=getattr(stmt, "has_unsolved_reference", False),
                )
            )
        return results

    def get_default_output(self) -> List[StatementStepOutput]:
        return []
