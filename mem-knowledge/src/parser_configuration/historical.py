"""Repair persisted legacy JSON only; API submissions keep the strict converters."""

from collections.abc import Mapping
from copy import deepcopy
from functools import cache
from typing import Annotated, Any

from pydantic import TypeAdapter

from ..rag.parser_config import resolve_layout_recognize
from .conversion import (
    MISSING,
    ConversionReport,
    ConversionResult,
    KnowledgeContext,
    _coerce,
    _legacy_chunk_method,
    get_path,
    infer_platform,
    join_delimiters,
    parse_bool,
    remove_path,
    set_path,
    split_delimiters,
    validate_values,
)
from .entities import (
    PLACEHOLDERS,
    SOURCE_BY_TYPE,
    ConfigurationError,
    DocumentConfiguration,
    KnowledgeConfiguration,
    valid_source_url,
)
from .fields import Scope, fields_for


@cache
def _field_adapter(scope: Scope, name: str) -> TypeAdapter:
    model = KnowledgeConfiguration if scope == Scope.KNOWLEDGE else DocumentConfiguration
    field = model.model_fields[name]
    return (
        TypeAdapter(Annotated[field.annotation, *field.metadata])
        if field.metadata
        else (TypeAdapter(field.annotation))
    )


def from_historical(
    data: Any,
    scope: Scope,
    *,
    context: KnowledgeContext | None = None,
) -> ConversionResult:
    """Produce usable saved values and a value-free audit of registered repairs.

    The caller preserves the original snapshot. Invalid owner metadata and programming
    errors still fail; this boundary repairs JSON fields, not database integrity.
    """
    context = context or KnowledgeContext()
    if context.knowledge_type not in SOURCE_BY_TYPE:
        raise ConfigurationError("unknown_knowledge_type", "type")
    report = ConversionReport()
    if not isinstance(data, Mapping):
        report.repairs["configuration"] = "expected_object"
        data = {}
    clean = deepcopy(dict(data))
    groups = {
        spec.legacy_path.split(".")[0]
        for spec in fields_for(scope)
        if spec.legacy_path and "." in spec.legacy_path
    }
    for group in sorted(groups):
        if group in clean and not isinstance(clean[group], Mapping):
            clean[group] = {}
            report.repairs[group] = "expected_group_object"
    values: dict[str, Any] = {}
    for spec in fields_for(scope):
        raw = get_path(clean, spec.legacy_path) if spec.legacy_path else MISSING
        value = deepcopy(spec.legacy_default)
        if raw is MISSING:
            report.defaulted.append(spec.column)
        else:
            try:
                value = (
                    resolve_layout_recognize(data)
                    if spec.column == "layout_recognize"
                    else _coerce(spec, raw)
                )
                if (
                    value is None
                    and spec.column.startswith("web_")
                    and spec.legacy_default is not None
                ):
                    raise ConfigurationError("required_default", spec.column)
                if spec.column == "web_user_agent" and isinstance(value, str) and not value.strip():
                    raise ConfigurationError("required_default", spec.column)
                value = _field_adapter(scope, spec.column).validate_python(value, strict=True)
                if spec.py_type is int and value is not None and not -(2**31) <= value < 2**31:
                    raise ConfigurationError("integer_out_of_range", spec.column)
                if spec.column in {"delimiters", "parent_chunk_delimiters"} and (
                    split_delimiters(join_delimiters(value)) != value
                ):
                    raise ConfigurationError("unrepresentable_delimiters", spec.column)
                if spec.column == "web_entry_url" and value and not valid_source_url(value):
                    raise ConfigurationError("invalid_source_url", spec.column)
            except (ValueError, TypeError, OverflowError) as exc:
                value = deepcopy(spec.legacy_default)
                report.repairs[spec.column] = (
                    exc.code if isinstance(exc, ConfigurationError) else "invalid_legacy_value"
                )
                report.defaulted.append(spec.column)
                # Keep key presence for the historical chunk-method discriminator.
                set_path(clean, spec.legacy_path, value)
            if type(raw) is not type(value) or raw != value:
                report.normalized.append(spec.column)
                report.repairs.setdefault(spec.column, "normalized_legacy_value")
        values[spec.column] = value

    values["chunk_method"] = _legacy_chunk_method(clean, scope)
    if values["chunk_method"] == "parent_child":
        report.parent_child_qa_zeroed = values["auto_questions"] not in (0, None)
        if values["auto_questions"] != 0:
            report.repairs["auto_questions"] = "parent_child_qa_zeroed"
        values["auto_questions"] = 0
    elif values["chunk_method"] is not None and values["auto_questions"] is None:
        values["auto_questions"] = 0
        report.repairs["auto_questions"] = "required_default"
    values["multimodal_enabled"] = context.multimodal_enabled

    if scope == Scope.KNOWLEDGE:
        source = SOURCE_BY_TYPE[context.knowledge_type]
        enabled = get_path(clean, "wiki.enabled", False)
        wiki_enabled = (
            parse_bool(enabled) if isinstance(enabled, (str, int, float, bool)) else False
        )
        if type(enabled) is not bool:
            report.repairs["wiki.enabled"] = "normalized_legacy_value"
        values.update(
            knowledge_type=context.knowledge_type,
            source=source,
            index_strategy=None if source is None else ("rag_wiki" if wiki_enabled else "rag"),
            third_party_platform=None,
        )
        if source == "third_party":
            try:
                values["third_party_platform"] = infer_platform(
                    clean,
                    context.file_urls,
                    check_dispatch=False,
                )
            except ConfigurationError as exc:
                report.repairs["third_party_platform"] = exc.code
        for spec in fields_for(scope):
            if spec.owner:
                values[spec.column] = getattr(context, spec.column)
            if not spec.api_path.startswith("sources.") or spec.column == "third_party_platform":
                continue
            value = values[spec.column]
            platform = values["third_party_platform"]
            active = (source == "website" and spec.column.startswith("web_")) or (
                source == "third_party" and platform and spec.column.startswith(platform + "_")
            )
            if not active or (
                isinstance(value, str) and (not value.strip() or value.strip() in PLACEHOLDERS)
            ):
                if value is not None and get_path(data, spec.legacy_path) is not MISSING:
                    report.repairs[spec.column] = "inactive_or_placeholder_source_field"
                values[spec.column] = None

    extras = deepcopy(clean)
    for spec in fields_for(scope):
        for path in ((spec.legacy_path,) if spec.legacy_path else ()) + spec.aliases:
            remove_path(extras, path)
    if scope == Scope.KNOWLEDGE:
        remove_path(extras, "wiki.enabled")
    values["extras"] = extras
    config = validate_values(values, scope)

    # Feed repaired input to the independent old-consumer oracle, not to_runtime's
    # output. Unexpected projection differences must still block migration.
    comparison = deepcopy(clean)
    for spec in fields_for(scope):
        if spec.legacy_path is None:
            continue
        value = values[spec.column]
        if spec.column in {"delimiters", "parent_chunk_delimiters"}:
            value = join_delimiters(value)
            if get_path(data, spec.legacy_path) != value:
                report.repairs.setdefault(spec.column, "canonical_delimiter")
        set_path(comparison, spec.legacy_path, value)
    comparison["parent_child_mode"] = config.chunk_method == "parent_child"
    if comparison.get("parent_child_mode") != data.get("parent_child_mode", False):
        report.repairs["chunk_method"] = "canonical_chunk_method"
    if "chunk_mode" in comparison:
        comparison.pop("chunk_mode")
        report.repairs["chunk_method"] = "canonical_chunk_method"
    if scope == Scope.KNOWLEDGE:
        set_path(comparison, "wiki.enabled", config.index_strategy == "rag_wiki")
        if config.chunk_method != "parent_child":
            comparison.pop("parent_chunk_mode", None)
        if config.chunk_method is None:
            comparison.pop("auto_questions", None)
    return ConversionResult(config, report, comparison)
