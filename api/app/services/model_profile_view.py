"""模型三列（input/output_modalities、features）宿主换算与 wire 视图（2d-2，spec §13.4）。

- 写路径统一经 `write_columns`：请求新字段优先直落；仅旧字段（capability/is_omni）经包内
  `legacy_capability_columns` 换算；更新场景以 `fallback_row` 的派生旧列视图补齐缺省，
  防单字段请求（如 `PUT {is_omni:false}`）清空其余维度
- 读侧遗留壳与响应层经 `legacy_view` / `wire_model_config` / `wire_model_base`：旧字段由 profile
  派生输出（wire 双输出窗口，前端未改期间保持逐位兼容），三新列直读
- 行替身（SimpleNamespace）容错：新列属性一律 `getattr(..., None)`，旧列属性缺失按空/false
- 旧列（capability/is_omni）停写冻结，删除并入 M10（Task19 gate）
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from redbear_model import (
    CompositeMember,
    ModelProfile,
    legacy_capability_columns,
)

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.models.models_model import ModelType as HostModelType
from app.schemas import model_schema
from app.services.channel_registry import parse_members

_CHAT_VALUE = HostModelType.CHAT.value


def normalize_type(value) -> str | None:
    """类型值归一：宿主 `ModelType` 含独立 `CHAT="chat"` 且无 `_missing_`（与包内不同），
    写路径显式 `'chat' → 'llm'`（大小写不敏感，兼容 DB/YAML 存量字符串）。"""
    if value is None:
        return None
    raw = str(getattr(value, "value", value))
    if raw.lower() == _CHAT_VALUE:
        return HostModelType.LLM.value
    return raw


def _enum_str(values: Sequence[Any] | None) -> list[str]:
    return [str(getattr(item, "value", item)) for item in values or ()]


def profile_of(row: Any) -> ModelProfile:
    """ORM 行 / 替身 → profile（ModelConfig 与 ModelBase 通用，getattr 容错）。

    ModelConfig 组合行装配 members（provider=composite 才解析 config.members[]）；
    ModelBase 无 tenant_id/config，按缺省处理。
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
    """行 → 旧列视图 `(capability, is_omni)`（profile 派生，2d wire 双输出与遗留壳用）。"""
    profile = profile_of(row)
    capabilities, is_omni = profile.legacy_capability_view(getattr(row, "provider", None))
    return _enum_str(capabilities), bool(is_omni)


def profile_columns(row: Any) -> dict[str, list[str]]:
    """行 → 契约 v2 三列视图（新列非空读新列，空回退旧列派生；payload/wire 同源口径）。"""
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
    """旧字段视图 → 契约 v2 三列（无 ORM 行的 payload 场景；有行一律用 `profile_columns`）。

    沙箱 payload 的 agent/workflow 执行恒为 LLM 族，故 type 固定 `llm`。
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
    """显式新列落库前校验契约不变量（空 = 未迁移态，写侧不得伪造；input 恒含 text、output 非空）。"""
    if input_modalities is not None:
        values = {str(getattr(item, "value", item)) for item in input_modalities}
        if not values:
            raise BusinessException(
                "input_modalities 不能为空列表（省略该字段即按旧字段派生）",
                BizCode.INVALID_PARAMETER,
            )
        if "text" not in values:
            raise BusinessException(
                "input_modalities 必须包含 'text'",
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
    capability: Sequence[str] | None = None,
    is_omni: bool | None = None,
    input_modalities: Sequence[str] | None = None,
    output_modalities: Sequence[str] | None = None,
    features: Sequence[str] | None = None,
    fallback_row: Any = None,
) -> dict[str, list[str]]:
    """请求字段 → 三新列（写路径单一换算点）。

    - 新字段（非 None）优先直落，不做往返转换（经 `_require_valid_new_columns` 校验契约不变量）
    - 缺省侧经包内 `legacy_capability_columns` 换算（旧字段 → 三列）
    - 旧字段/new 字段双双缺省时以 `fallback_row` 的派生视图补齐（更新场景保持现值）
    """
    _require_valid_new_columns(input_modalities, output_modalities)
    resolved_type = row_type if row_type is not None else getattr(fallback_row, "type", None)
    resolved_provider = provider if provider is not None else getattr(fallback_row, "provider", None)
    if resolved_type is None:
        raise ValueError("write_columns requires row_type or fallback_row")

    if fallback_row is not None:
        fallback_capabilities, fallback_omni = legacy_view(fallback_row)
    else:
        fallback_capabilities, fallback_omni = [], False

    derived_input, derived_output, derived_features = legacy_capability_columns(
        type=resolved_type,
        provider=resolved_provider,
        capabilities=capability if capability is not None else fallback_capabilities,
        is_omni=bool(is_omni) if is_omni is not None else fallback_omni,
    )
    return {
        "input_modalities": list(input_modalities) if input_modalities is not None else _enum_str(derived_input),
        "output_modalities": list(output_modalities) if output_modalities is not None else _enum_str(derived_output),
        "features": list(features) if features is not None else _enum_str(derived_features),
    }


def wire_model_config(row: Any) -> model_schema.ModelConfig:
    """ModelConfig 行 → 响应 schema：旧字段由 profile 派生，三新列同源输出（双输出）。"""
    item = model_schema.ModelConfig.model_validate(row)
    profile = profile_of(row)
    capabilities, is_omni = profile.legacy_capability_view(getattr(row, "provider", None))
    item.capability = _enum_str(capabilities)
    item.is_omni = bool(is_omni)
    item.input_modalities = _enum_str(profile.input_modalities)
    item.output_modalities = _enum_str(profile.output_modalities)
    item.features = _enum_str(profile.features)
    return item


def wire_model_base(row: Any) -> model_schema.ModelBase:
    """ModelBase 行 → 响应 schema（旧字段派生 + 三新列，口径同 `wire_model_config`）。"""
    item = model_schema.ModelBase.model_validate(row)
    profile = profile_of(row)
    capabilities, is_omni = profile.legacy_capability_view(getattr(row, "provider", None))
    item.capability = _enum_str(capabilities)
    item.is_omni = bool(is_omni)
    item.input_modalities = _enum_str(profile.input_modalities)
    item.output_modalities = _enum_str(profile.output_modalities)
    item.features = _enum_str(profile.features)
    return item
