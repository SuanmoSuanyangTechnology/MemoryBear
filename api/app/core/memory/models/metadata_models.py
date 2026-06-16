"""Models for user metadata extraction.

Independent from triplet_models.py - these models are used by the
standalone metadata extraction pipeline (post-dedup async Celery task).

The field definitions align with the Jinja2 prompt template
``extract_user_metadata.jinja2``.

Output schema: ``operations`` patch list, where each item is one of
``add`` / ``delete`` / ``update`` against one of 8 metadata fields.

The 9th field ``aliases`` referenced by the prompt template is intentionally
filtered out at runtime — alias merging is handled by the reflection-engine
``alias_merger`` pipeline and stays decoupled from this metadata patch flow.
"""

import logging
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


# ── Fields managed by metadata patch ──
# NOTE: ``aliases`` is intentionally excluded; see module docstring.
ALLOWED_METADATA_FIELDS: tuple[str, ...] = (
    "core_facts",
    "traits",
    "relations",
    "goals",
    "interests",
    "beliefs_or_stances",
    "anchors",
    "events",
)

# Fields the prompt template documents but the runtime intentionally rejects.
# Used to give a clearer error than "unknown field" when LLM honors the prompt
# but ignores the runtime whitelist.
FILTERED_METADATA_FIELDS: tuple[str, ...] = ("aliases",)

OperationLiteral = Literal["add", "delete", "update"]


class MetadataOperation(BaseModel):
    """Single patch operation produced by the LLM.

    Per the prompt template, exactly one of the following layouts is valid:

    - ``add``    : ``op``, ``field``, ``value``
    - ``delete`` : ``op``, ``field``, ``old_value``
    - ``update`` : ``op``, ``field``, ``old_value``, ``new_value``

    Validation is permissive on extra keys (LLM noise is dropped) but strict
    on required keys per ``op``.

    The ``op`` field is typed as ``Literal["add","delete","update"]`` so that
    Pydantic / mypy reject unknown ops up-front; ``_validate_shape`` only
    handles ``field`` whitelisting and per-op required-key combinations.
    """

    model_config = ConfigDict(extra="ignore")

    op: OperationLiteral
    field: str
    value: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "MetadataOperation":
        field = (self.field or "").strip()
        if field in FILTERED_METADATA_FIELDS:
            raise ValueError(
                f"field {self.field!r} is filtered at runtime "
                f"(handled by a separate pipeline)"
            )
        if field not in ALLOWED_METADATA_FIELDS:
            raise ValueError(f"unknown field: {self.field!r}")
        self.field = field

        if self.op == "add":
            v = (self.value or "").strip()
            if not v:
                raise ValueError("`add` operation requires non-empty `value`")
            self.value = v
        elif self.op == "delete":
            ov = (self.old_value or "").strip()
            if not ov:
                raise ValueError("`delete` operation requires non-empty `old_value`")
            self.old_value = ov
        else:  # update
            ov = (self.old_value or "").strip()
            nv = (self.new_value or "").strip()
            if not ov or not nv:
                raise ValueError(
                    "`update` operation requires both `old_value` and `new_value`"
                )
            self.old_value = ov
            self.new_value = nv
        return self


class MetadataExtractionResponse(BaseModel):
    """LLM 元数据提取响应结构。

    仅保留 ``operations`` 一个顶层字段；任何不合法或越界的 op 在
    ``model_validator(mode="before")`` 阶段通过尝试构造 :class:`MetadataOperation`
    静默丢弃，校验逻辑只在 ``MetadataOperation._validate_shape`` 一处。

    Pydantic v2 默认 ``revalidate_instances="never"``，因此 before-validator
    构造好的实例不会再被外层字段重新校验，没有双重校验开销。
    """

    model_config = ConfigDict(extra="ignore")

    operations: List[MetadataOperation] = Field(
        default_factory=list,
        description="LLM 输出的元数据 patch 操作列表",
    )

    @model_validator(mode="before")
    @classmethod
    def _filter_operations(cls, data: object) -> object:
        """通过 try/except 静默丢弃 LLM 输出的无效 / 越界 op。

        被静默丢弃的情形（均由 ``MetadataOperation._validate_shape`` 决定）：
            - 非 dict、未知 op、未知或被白名单过滤的 field（如 ``aliases``）
            - shape 不符（例如 ``add`` 缺 ``value``、``update`` 缺 ``new_value``）

        丢弃计数写入 ``data["_dropped_ops_count"]``，供上层 task 记日志。
        由于 ``model_config = ConfigDict(extra="ignore")``，该 key 不会
        出现在最终模型实例上（Pydantic 自动忽略）。
        """
        if not isinstance(data, dict):
            return data
        raw_ops = data.get("operations")
        if not isinstance(raw_ops, list):
            return data

        cleaned: List[MetadataOperation] = []
        dropped = 0
        dropped_details: List[str] = []
        for item in raw_ops:
            try:
                cleaned.append(MetadataOperation.model_validate(item))
            except Exception as e:
                dropped += 1
                dropped_details.append(
                    f"item={item!r}, reason={e}"
                )
                continue

        if dropped > 0:
            logger.warning(
                f"[Metadata] MetadataExtractionResponse 丢弃了 {dropped}/{len(raw_ops)} 条无效 op: "
                + "; ".join(dropped_details[:5])
                + ("..." if dropped > 5 else "")
            )

        return {**data, "operations": cleaned, "_dropped_ops_count": dropped}
