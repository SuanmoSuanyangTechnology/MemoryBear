"""Host conversion and wire views for the model's three columns (input/output_modalities, features).

- The write path goes exclusively through `write_columns`: new request fields land directly; only
  legacy fields (capability/is_omni) are converted via the package's `legacy_capability_columns`;
  updates backfill defaults from `fallback_row`'s derived legacy-column view, so a single-field
  request (e.g. `PUT {is_omni:false}`) does not clear the other dimensions.
- Legacy shells on the read side and the response layer go through `legacy_view` /
  `wire_model_config` / `wire_model_base`: legacy fields are derived from the profile output
  (dual wire output while the frontend is unchanged, keeping slot-by-slot compatibility), and the
  three new columns are read directly.
- Row stand-ins (SimpleNamespace) are tolerated: new-column attributes are always accessed via
  `getattr(..., None)`, and missing legacy-column attributes read as empty/false.
- Legacy columns (capability/is_omni) are no longer written; their removal is deferred.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from redbear_model import (
    CompositeMember,
    Modality,
    ModelProfile,
    ModelType as ContractModelType,
    legacy_capability_columns,
)

from ..errors import BizCode
from ..errors import BusinessException
from ..models.models_model import LEGACY_CHAT_TYPE, ModelType as HostModelType
from ..schemas import model_schema
from .channel_registry import parse_members


def normalize_type(value) -> str | None:
    """Normalize a type value: `'chat' → 'llm'` (case-insensitive, tolerating existing DB/YAML strings); the result is always lowercase."""
    if value is None:
        return None
    raw = str(getattr(value, "value", value))
    normalized = raw.lower()
    if normalized == LEGACY_CHAT_TYPE:
        return HostModelType.LLM.value
    return normalized


def _enum_str(values: Sequence[Any] | None) -> list[str]:
    return [str(getattr(item, "value", item)) for item in values or ()]


def profile_of(row: Any) -> ModelProfile:
    """ORM row / stand-in → profile (shared by ModelConfig and ModelBase, getattr-tolerant).

    Composite ModelConfig rows assemble members (config.members[] is parsed only when
    provider=composite); ModelBase has no tenant_id/config and is handled with defaults.
    """
    config = getattr(row, "config", None)
    members: tuple[CompositeMember, ...] = ()
    if isinstance(config, dict) and getattr(row, "provider", None) == "composite":
        members = tuple(
            CompositeMember(provider=member_provider, model_name=model_name)
            for member_provider, model_name in parse_members(config)
        )
    return ModelProfile.from_stored_fields(
        model_id=getattr(row, "id", None),
        tenant_id=getattr(row, "tenant_id", None),
        type=getattr(row, "type", None),
        provider=getattr(row, "provider", None),
        input_modalities=_row_list(row, "input_modalities"),
        output_modalities=_row_list(row, "output_modalities"),
        features=_row_list(row, "features"),
        capabilities=_row_list(row, "capability"),
        is_omni=bool(getattr(row, "is_omni", False)),
        members=members,
    )


def _row_list(row: Any, name: str) -> list:
    return list(getattr(row, name, None) or ())


def legacy_view(row: Any) -> tuple[list[str], bool]:
    """Row → legacy column view `(capability, is_omni)` (derived from the profile; used by the dual wire output and legacy shells)."""
    profile = profile_of(row)
    capabilities, is_omni = profile.legacy_capability_view(getattr(row, "provider", None))
    return _enum_str(capabilities), bool(is_omni)


def profile_columns(row: Any) -> dict[str, list[str]]:
    """Row → contract-v2 three-column view (read new columns when non-empty, else fall back to legacy-derived values; payload and wire share this source)."""
    profile = profile_of(row)
    return {
        "input_modalities": _enum_str(profile.input_modalities),
        "output_modalities": _enum_str(profile.output_modalities),
        "features": _enum_str(profile.features),
    }


def columns_from_legacy(
    *,
    provider: str | None,
    capabilities: Sequence[str] | None = None,
    is_omni: bool = False,
) -> dict[str, list[str]]:
    """Legacy field view → contract-v2 three columns (payload scenarios without an ORM row; rows always use `profile_columns`).

    Sandbox payload agent/workflow executions are always in the LLM family, so type is fixed to `llm`.
    """
    derived_input, derived_output, derived_features = legacy_capability_columns(
        type=HostModelType.LLM.value,
        provider=provider,
        capabilities=capabilities or (),
        is_omni=is_omni,
    )
    return {
        "input_modalities": _enum_str(derived_input),
        "output_modalities": _enum_str(derived_output),
        "features": _enum_str(derived_features),
    }


def _require_valid_new_columns(
    input_modalities: Sequence[str] | None,
    output_modalities: Sequence[str] | None,
) -> None:
    """Validate contract invariants before explicit new columns are persisted (empty = not yet migrated, the write side must not fabricate values; input/output only need to be non-empty)."""
    if input_modalities is not None and not list(input_modalities):
        raise BusinessException(
            "input_modalities 不能为空列表（省略该字段即按旧字段派生）",
            BizCode.INVALID_PARAMETER,
        )
    if output_modalities is not None and not list(output_modalities):
        raise BusinessException(
            "output_modalities 不能为空列表（省略该字段即按旧字段派生）",
            BizCode.INVALID_PARAMETER,
        )


def write_columns(
    *,
    row_type: Any = None,
    provider: str | None = None,
    input_modalities: Sequence[str] | None = None,
    output_modalities: Sequence[str] | None = None,
    features: Sequence[str] | None = None,
    fallback_row: Any = None,
) -> dict[str, list[str]]:
    """Request fields → the three new columns (single conversion point on the write path).

    - Explicit three columns (not None) land directly with no round-trip conversion (contract
      invariants are checked by `_require_valid_new_columns`).
    - Defaults: updates take the profile's three columns from `fallback_row` (keeping current
      values; existing legacy columns are derived via read-side compatibility); creates take the
      type-based baseline (text / type-based output / empty features). The ASR family uses its
      real modalities (audio → text) rather than the legacy-conversion text baseline (which only
      serves existing-data migration).
    """
    _require_valid_new_columns(input_modalities, output_modalities)
    if fallback_row is not None:
        current = profile_columns(fallback_row)
    else:
        resolved_type = row_type
        if resolved_type is None:
            raise ValueError("write_columns requires row_type or fallback_row")
        derived_input, derived_output, derived_features = legacy_capability_columns(
            type=resolved_type,
            provider=provider,
            capabilities=(),
            is_omni=False,
        )
        if ContractModelType(resolved_type) is ContractModelType.ASR:
            derived_input, derived_output = (Modality.AUDIO,), (Modality.TEXT,)
        current = {
            "input_modalities": _enum_str(derived_input),
            "output_modalities": _enum_str(derived_output),
            "features": _enum_str(derived_features),
        }
    return {
        "input_modalities": list(input_modalities) if input_modalities is not None else current["input_modalities"],
        "output_modalities": list(output_modalities) if output_modalities is not None else current["output_modalities"],
        "features": list(features) if features is not None else current["features"],
    }


def wire_model_config(row: Any) -> model_schema.ModelConfig:
    """ModelConfig row → response schema: the three new columns are derived from the profile (legacy fields are off the wire)."""
    item = model_schema.ModelConfig.model_validate(row)
    profile = profile_of(row)
    item.input_modalities = _enum_str(profile.input_modalities)
    item.output_modalities = _enum_str(profile.output_modalities)
    item.features = _enum_str(profile.features)
    return item


def wire_model_base(row: Any) -> model_schema.ModelBase:
    """ModelBase row → response schema (three new columns, same conventions as `wire_model_config`)."""
    item = model_schema.ModelBase.model_validate(row)
    profile = profile_of(row)
    item.input_modalities = _enum_str(profile.input_modalities)
    item.output_modalities = _enum_str(profile.output_modalities)
    item.features = _enum_str(profile.features)
    return item
