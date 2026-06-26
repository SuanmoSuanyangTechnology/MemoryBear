"""Pydantic models for hot-pluggable sidecar step inputs and outputs.

Sidecar steps are non-critical (is_critical=False) modules registered via
``@SidecarStepFactory.register`` that run concurrently alongside the main
extraction pipeline.  Failures degrade gracefully to default outputs.
"""

from typing import Callable, Dict, List, Optional, Tuple, TypeVar

from pydantic import BaseModel, Field

from app.core.memory.models.metadata_models import (
    ALLOWED_METADATA_FIELDS,
    MetadataOperation,
)


# ── Emotion extraction (sidecar) ──
class EmotionStepInput(BaseModel):
    """Input for EmotionExtractionStep."""

    statement_id: str
    statement_text: str
    speaker: str


class EmotionStepOutput(BaseModel):
    """Output of EmotionExtractionStep."""

    emotion_type: str = "neutral"
    emotion_intensity: float = 0.0
    emotion_keywords: List[str] = Field(default_factory=list)


# ── Metadata extraction (async post-dedup) ──
class MetadataStepInput(BaseModel):
    """Input for MetadataExtractionStep."""

    entity_id: str
    entity_name: str
    descriptions: List[str] = Field(
        default_factory=list,
        description="用户实体的 description 列表（可能由分号分隔拆分而来）",
    )
    existing_metadata: dict = Field(
        default_factory=dict,
        description="Neo4j 中已有的元数据，用于增量去重 / patch old_value 校验",
    )


_TItem = TypeVar("_TItem")


class MetadataStepOutput(BaseModel):
    """Output of MetadataExtractionStep.

    The new contract is a flat list of patch operations. Helpers below group
    them by field for the Cypher patch query.
    """

    operations: List[MetadataOperation] = Field(default_factory=list)
    dropped_ops_count: int = Field(
        default=0,
        description="被 LLM 响应校验阶段静默丢弃的无效 op 数量（格式错误/字段越界等）",
    )

    def has_any(self) -> bool:
        return bool(self.operations)

    def _group_by_field(
        self,
        op_kind: str,
        extractor: Callable[[MetadataOperation], Optional[_TItem]],
    ) -> Dict[str, List[_TItem]]:
        """按 ``op.field`` 分组指定 ``op_kind`` 的 operations。

        ``extractor`` 用于把 op 转成桶内元素；返回 ``None`` 视为该 op 跳过。
        所有白名单字段都会出现在结果中（空 list），方便上层一次性拼参。
        """
        out: Dict[str, List[_TItem]] = {f: [] for f in ALLOWED_METADATA_FIELDS}
        for op in self.operations:
            if op.op != op_kind:
                continue
            item = extractor(op)
            if item is None:
                continue
            out[op.field].append(item)
        return out

    def adds_by_field(self) -> Dict[str, List[str]]:
        return self._group_by_field("add", lambda op: op.value or None)

    def deletes_by_field(self) -> Dict[str, List[str]]:
        return self._group_by_field("delete", lambda op: op.old_value or None)

    def updates_by_field(self) -> Dict[str, List[Tuple[str, str]]]:
        def _pair(op: MetadataOperation) -> Optional[Tuple[str, str]]:
            if not op.old_value or not op.new_value:
                return None
            return op.old_value, op.new_value

        return self._group_by_field("update", _pair)

    def counts(self) -> Dict[str, int]:
        result = {"add": 0, "delete": 0, "update": 0}
        for op in self.operations:
            if op.op in result:
                result[op.op] += 1
        return result
