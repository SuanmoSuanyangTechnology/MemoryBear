"""Structured model output and public responses for reflection previews."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class BaseDataSchema(BaseModel):
    """Base schema for the data"""

    id: str | None = Field(None, description="The unique identifier for the data entry.")
    statement: str | None = Field(None, description="The statement text.")
    created_at: str | None = Field(None, description="The creation timestamp in ISO 8601 format.")
    expired_at: str | None = Field(None, description="The expiration timestamp in ISO 8601 format.")
    description: str | None = Field(None, description="The description of the data entry.")

    entity1_name: str | None = Field(None, description="The first entity name.")
    entity2_name: str | None = Field(None, description="The second entity name.")
    statement_id: str | None = Field(None, description="The statement identifier.")
    predicate: str | None = Field(
        None, description="The predicate describing the relationship between entities."
    )
    relationship_statement_id: str | None = Field(
        None, description="The relationship statement identifier."
    )
    relationship: str | dict[str, Any] | None = Field(
        None, description="The relationship object or string."
    )
    entity2: dict[str, Any] | None = Field(None, description="The second entity object.")

    @model_validator(mode="before")
    def _set_default_created_at(cls, v):
        """Set default created_at if missing"""
        if isinstance(v, dict) and v.get("created_at") is None:
            v["created_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return v


class QualityAssessmentSchema(BaseModel):
    """Schema for memory quality assessment results."""

    score: int = Field(..., ge=0, le=100, description="Quality score percentage (0-100).")
    summary: str = Field(
        ...,
        description="Brief summary of data quality status, including main issues and strengths.",
    )


class MemoryVerifySchema(BaseModel):
    """Schema for memory privacy verification results."""

    has_privacy: bool = Field(..., description="Whether privacy information was detected.")
    privacy_types: list[str] = Field([], description="List of detected privacy information types.")
    summary: str = Field(..., description="Brief summary of privacy detection results.")


class ConflictResultSchema(BaseModel):
    """Schema for the conflict result data in the reflexion_data.json file."""

    data: list[BaseDataSchema] = Field(
        ...,
        description=(
            "The conflict memory data. Only contains conflicting records when conflict is True."
        ),
    )
    conflict: bool = Field(..., description="Whether the memory is in conflict.")
    quality_assessment: QualityAssessmentSchema | None = Field(
        None,
        description=(
            "The quality assessment object. Contains score and summary when "
            "quality_assessment is enabled, null otherwise."
        ),
    )
    memory_verify: MemoryVerifySchema | None = Field(
        None,
        description=(
            "The memory privacy verification object. Contains privacy detection results "
            "when memory_verify is enabled, null otherwise."
        ),
    )

    @model_validator(mode="before")
    def _normalize_data(cls, v):
        if isinstance(v, dict):
            d = v.get("data")
            if isinstance(d, dict):
                v["data"] = [d]
        return v


class ReflexionSchema(BaseModel):
    """Schema for the reflexion data in the reflexion_data"""

    reason: str = Field(..., description="The reason for the reflexion.")
    solution: str = Field(..., description="The solution for the reflexion.")


class ChangeRecordSchema(BaseModel):
    """Schema for individual change records

    字段值格式说明：
    - id: 字符串，表示修改字段对应的记录ID
    - 其他字段: 可以是字符串、None，数组 [修改前的值, 修改后的值]，或嵌套字典结构
    - entity2等嵌套对象的字段也遵循 [old_value, new_value] 格式
    """

    field: list[dict[str, Any]] = Field(
        ...,
        description=(
            "List of field changes. First item: {id: value}, followed by changed fields "
            "as {field_name: [old_value, new_value]} or {field_name: new_value} or nested "
            "structures like {entity2: {field_name: [old, new]}}"
        ),
    )


class ResolvedSchema(BaseModel):
    """Schema for the resolved memory data in the reflexion_data"""

    original_memory_id: str | None = Field(None, description="The original memory identifier.")
    resolved_memory: BaseDataSchema | list[BaseDataSchema] | None = Field(
        None,
        description=(
            "The resolved memory data (only contains records that need modification). Can "
            "be a single record or list of records."
        ),
    )
    change: list[ChangeRecordSchema] | None = Field(
        None, description="List of detailed change records with IDs and field information."
    )


class SingleReflexionResultSchema(BaseModel):
    """Schema for a single reflexion result item."""

    conflict: ConflictResultSchema = Field(
        ..., description="The conflict result data for this specific conflict type."
    )
    reflexion: ReflexionSchema = Field(..., description="The reflexion data for this conflict.")
    resolved: ResolvedSchema | None = Field(
        None, description="The resolved memory data for this conflict."
    )
    type: str = Field("reflexion_result", description="The type identifier.")


class ReflexionResultSchema(BaseModel):
    """Schema for the complete reflexion result data - a list of individual conflict resolutions."""

    results: list[SingleReflexionResultSchema] = Field(
        ..., description="List of individual conflict resolution results, grouped by conflict type."
    )

    @model_validator(mode="before")
    def _normalize_resolved(cls, v):
        if isinstance(v, dict):
            conflict = v.get("conflict")
            if isinstance(conflict, dict) and conflict.get("conflict") is False:
                v["resolved"] = None
            else:
                resolved = v.get("resolved")
                if isinstance(resolved, dict):
                    orig = resolved.get("original_memory_id")
                    mem = resolved.get("resolved_memory")
                    if orig is None and (mem is None or mem == {}):
                        v["resolved"] = None
        return v


class TranslationResponse(BaseModel):
    data: str


class ReflectionTrialConfig(BaseModel):
    baseline: Literal["TIME", "FACT", "HYBRID"]
    reflexion_range: Literal["partial", "all"]
    memory_verify: bool
    quality_assessment: bool


class ReflectionTrialData(BaseModel):
    baseline: Literal["TIME", "FACT", "HYBRID"]
    source_data: str
    memory_verifies: list[MemoryVerifySchema | None]
    quality_assessments: list[QualityAssessmentSchema | None]
    reflexion_data: list[ReflexionSchema]


class ReflectionTrialFailure(BaseModel):
    success: Literal[False] = False
    message: str
    conflicts_found: int = 0
    conflicts_resolved: int = 0
    memories_updated: int = 0
    execution_time: float = Field(ge=0)
    details: dict[str, Any] | None = None
