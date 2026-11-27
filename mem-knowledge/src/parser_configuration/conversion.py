"""Conversions shared by offline migration and future business callers."""

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from ..rag.parser_config import build_default_knowledge_parser_config, resolve_layout_recognize
from .entities import (
    PLACEHOLDERS,
    SOURCE_BY_TYPE,
    Configuration,
    ConfigurationError,
    DocumentConfiguration,
    KnowledgeConfiguration,
)
from .fields import FieldSpec, Scope, fields_for

MISSING = object()


@dataclass(frozen=True)
class KnowledgeContext:
    knowledge_type: str = "General"
    image2text_id: UUID | None = None
    audio2text_id: UUID | None = None
    video2text_id: UUID | None = None
    file_urls: tuple[str | None, ...] = ()

    @property
    def multimodal_enabled(self) -> bool:
        return any((self.image2text_id, self.audio2text_id, self.video2text_id))


@dataclass
class ConversionReport:
    defaulted: list[str] = field(default_factory=list)
    normalized: list[str] = field(default_factory=list)
    parent_child_qa_zeroed: bool = False
    repairs: dict[str, str] = field(default_factory=dict)


@dataclass
class ConversionResult:
    config: Configuration
    report: ConversionReport
    comparison_data: dict[str, Any] | None = None


def get_path(data: Mapping[str, Any], path: str, default: Any = MISSING) -> Any:
    value: Any = data
    for key in path.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return default
        value = value[key]
    return value


def set_path(data: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    for key in keys[:-1]:
        if not isinstance(data.get(key), dict):
            data[key] = {}
        data = data[key]
    data[keys[-1]] = deepcopy(value)


def remove_path(data: dict[str, Any], path: str) -> None:
    key, _, rest = path.partition(".")
    if not rest:
        data.pop(key, None)
    elif isinstance(data.get(key), dict):
        remove_path(data[key], rest)
        if not data[key]:
            data.pop(key)


def split_delimiters(value: str) -> list[str]:
    """Keep input order and duplicates; the tokenizer's length sort is not storage order."""
    result: list[str] = []
    start = 0
    for match in re.finditer(r"`([^`]+)`", value):
        result.extend(value[start : match.start()])
        result.append(match.group(1))
        start = match.end()
    result.extend(value[start:])
    return result


def join_delimiters(value: list[str]) -> str:
    return "".join(item if len(item) == 1 else f"`{item}`" for item in value)


def parse_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce(spec: FieldSpec, value: Any) -> Any:
    if spec.column == "graph_scene_name":
        return str(value or "")
    if spec.column == "graph_entity_types":
        items = value or ()
        if not isinstance(items, (list, tuple)):
            raise ConfigurationError("invalid_entity_types", spec.column)
        return [str(item).strip() for item in items if str(item).strip()]
    if spec.column == "graph_enabled":
        return value is True
    if spec.py_type is bool:
        return parse_bool(value)
    if value is None:
        return None
    if spec.column in {"delimiters", "parent_chunk_delimiters"}:
        if not isinstance(value, str):
            raise ConfigurationError("invalid_delimiter", spec.column)
        return split_delimiters(value)
    if spec.py_type in (int, float):
        if isinstance(value, bool) or (
            spec.py_type is int and isinstance(value, float) and not value.is_integer()
        ):
            raise ConfigurationError("invalid_number", spec.column)
        return spec.py_type(value)
    if spec.column in {"graph_pipeline", "wiki_language", "wiki_extract_prompt"}:
        if not isinstance(value, str):
            raise ConfigurationError("invalid_string", spec.column)
        return value.strip().lower() if spec.column == "graph_pipeline" else value.strip()
    return deepcopy(value)


def validate_values(values: dict[str, Any], scope: Scope) -> Configuration:
    model = KnowledgeConfiguration if scope == Scope.KNOWLEDGE else DocumentConfiguration
    try:
        config = model.model_validate(values)
    except ValidationError as exc:
        for error in exc.errors(include_input=False):
            cause = error.get("ctx", {}).get("error")
            if isinstance(cause, ConfigurationError):
                raise cause from None
        errors = exc.errors(include_input=False, include_context=False)
        location = ".".join(str(part) for part in errors[0]["loc"]) or "configuration"
        raise ConfigurationError("invalid_configuration", location) from None
    for name in ("delimiters", "parent_chunk_delimiters"):
        items = getattr(config, name)
        if split_delimiters(join_delimiters(items)) != items:
            raise ConfigurationError("unrepresentable_delimiters", name)
    return config


def _legacy_chunk_method(data: Mapping[str, Any], scope: Scope) -> str | None:
    if scope == Scope.KNOWLEDGE:
        if "auto_questions" not in data and "parent_chunk_mode" not in data:
            return None
        if (
            "auto_questions" in data
            and "parent_chunk_mode" not in data
            and not data.get("parent_child_mode", False)
        ):
            return "custom"
        return "parent_child"
    if "parent_child_mode" in data:
        raw = data["parent_child_mode"]
        enabled = (
            raw.strip().lower() in {"true", "1", "yes", "y", "on"}
            if (isinstance(raw, str))
            else bool(raw)
        )
    else:
        enabled = data.get("parent_chunk_mode") in {"paragraph", "full-doc"}
    return "parent_child" if enabled else "custom"


def _source_values(
    values: dict[str, Any], data: Mapping[str, Any], context: KnowledgeContext
) -> None:
    source = SOURCE_BY_TYPE.get(context.knowledge_type, MISSING)
    if source is MISSING:
        raise ConfigurationError("unknown_knowledge_type", "type")
    values["knowledge_type"] = context.knowledge_type
    values["source"] = source
    enabled = get_path(data, "wiki.enabled", False)
    if type(enabled) is not bool:
        raise ConfigurationError("invalid_boolean", "wiki.enabled")
    values["index_strategy"] = None if source is None else ("rag_wiki" if enabled else "rag")
    for spec in fields_for(Scope.KNOWLEDGE):
        if spec.owner:
            values[spec.column] = getattr(context, spec.column)
        if spec.api_path.startswith("sources."):
            raw = values[spec.column]
            if isinstance(raw, str) and (not raw.strip() or raw.strip() in PLACEHOLDERS):
                values[spec.column] = None
    if source == "third_party":
        values["third_party_platform"] = infer_platform(data, context.file_urls)
    else:
        values["third_party_platform"] = None
    for spec in fields_for(Scope.KNOWLEDGE):
        if not spec.api_path.startswith("sources.") or spec.column == "third_party_platform":
            continue
        keep = (spec.column.startswith("web_") and source == "website") or (
            source == "third_party" and spec.column.startswith(values["third_party_platform"] + "_")
        )
        if not keep:
            values[spec.column] = None


def infer_platform(
    data: Mapping[str, Any], file_urls: tuple[str | None, ...], *, check_dispatch: bool = True
) -> str:
    candidates: set[str] = set()
    explicit = data.get("_third_party_platform")
    if explicit:
        if not isinstance(explicit, str) or explicit not in {"yuque", "feishu"}:
            raise ConfigurationError("unknown_platform", "third_party_platform")
        candidates.add(explicit)
    for platform, key in (("yuque", "yuque_user_id"), ("feishu", "feishu_app_id")):
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip() and raw.strip() not in PLACEHOLDERS:
            candidates.add(platform)
    for url in file_urls:
        if url and "yuque.com" in url:
            candidates.add("yuque")
        if url and "feishu.cn" in url:
            candidates.add("feishu")
    if len(candidates) != 1:
        raise ConfigurationError("ambiguous_or_unknown_platform", "third_party_platform")
    platform = next(iter(candidates))
    if not check_dispatch:
        return platform
    # The old dispatcher uses two independent branches and ignores explicit platform.
    dispatched: set[str] = set()
    for name, key, placeholder, domain in (
        ("yuque", "yuque_user_id", "User ID", "yuque.com"),
        ("feishu", "feishu_app_id", "App ID", "feishu.cn"),
    ):
        raw = data.get(key)
        if (
            raw
            and raw != placeholder
            and (not file_urls or any(url and domain in url for url in file_urls))
        ):
            dispatched.add(name)
    if dispatched != {platform}:
        raise ConfigurationError("legacy_platform_dispatch_mismatch", "third_party_platform")
    return platform


def from_legacy(
    data: Mapping[str, Any] | None, scope: Scope, *, context: KnowledgeContext | None = None,
    source_config: KnowledgeConfiguration | None = None,
) -> ConversionResult:
    if data is not None and not isinstance(data, Mapping):
        raise ConfigurationError("expected_object")
    data = data or {}
    context = context or KnowledgeContext()
    extras = deepcopy(dict(data))
    report = ConversionReport()
    values: dict[str, Any] = {}
    for spec in fields_for(scope):
        raw = get_path(data, spec.legacy_path) if spec.legacy_path else MISSING
        if source_config is not None and spec.api_path.startswith("sources."):
            values[spec.column] = getattr(source_config, spec.column)
            if spec.legacy_path:
                remove_path(extras, spec.legacy_path)
            continue
        if spec.column.startswith("web_") and context.knowledge_type != "Web":
            values[spec.column] = None
            if raw is not MISSING and raw is not None:
                report.normalized.append(spec.column)
            if spec.legacy_path:
                remove_path(extras, spec.legacy_path)
            continue
        if raw is MISSING:
            values[spec.column] = deepcopy(spec.legacy_default)
            report.defaulted.append(spec.column)
        else:
            try:
                value = _coerce(spec, raw)
            except (ValueError, TypeError, OverflowError):
                raise ConfigurationError("invalid_legacy_value", spec.column) from None
            values[spec.column] = value
            if type(raw) is not type(value) or raw != value:
                report.normalized.append(spec.column)
        for path in ((spec.legacy_path,) if spec.legacy_path else ()) + spec.aliases:
            remove_path(extras, path)
    layout = resolve_layout_recognize(data)
    if layout != values["layout_recognize"]:
        report.normalized.append("layout_recognize")
    values["layout_recognize"] = layout
    values["chunk_method"] = _legacy_chunk_method(data, scope)
    if values["chunk_method"] == "parent_child":
        report.parent_child_qa_zeroed = values["auto_questions"] not in (0, None)
        values["auto_questions"] = 0
    values["multimodal_enabled"] = context.multimodal_enabled
    if scope == Scope.KNOWLEDGE:
        before_source = deepcopy(values)
        if source_config is None:
            _source_values(values, data, context)
        else:
            # Only callers updating unrelated fields may reuse a saved incomplete source.
            values.update(
                knowledge_type=context.knowledge_type,
                source=source_config.source,
                index_strategy=source_config.index_strategy,
            )
            for spec in fields_for(scope):
                if spec.owner:
                    values[spec.column] = getattr(context, spec.column)
                elif spec.api_path.startswith("sources."):
                    values[spec.column] = getattr(source_config, spec.column)
        report.normalized.extend(
            spec.column
            for spec in fields_for(scope)
            if spec.api_path.startswith("sources.")
            and spec.column not in report.normalized
            and before_source[spec.column] != values[spec.column]
        )
        remove_path(extras, "wiki.enabled")
    values["extras"] = extras
    config = validate_values(values, scope)
    if isinstance(config, KnowledgeConfiguration) and source_config is None:
        config.require_complete_source()
    return ConversionResult(config, report)


def new_configuration(scope: Scope, *, context: KnowledgeContext | None = None) -> Configuration:
    result = from_legacy(build_default_knowledge_parser_config(), scope, context=context)
    values = result.config.model_dump()
    values["chunk_method"] = "default"
    values["parent_chunk_delimiters"] = ["\n\n"]
    return validate_values(values, scope)


def to_legacy(config: Configuration, include_extras: bool = True) -> dict[str, Any]:
    data = deepcopy(config.extras) if include_extras else {}
    for spec in fields_for(config.scope):
        if spec.legacy_path is None:
            continue
        value = getattr(config, spec.column)
        if spec.api_path.startswith("sources.") and value is None:
            continue
        if spec.column in {"delimiters", "parent_chunk_delimiters"}:
            value = join_delimiters(value)
        set_path(data, spec.legacy_path, value)
    if isinstance(config, KnowledgeConfiguration):
        set_path(data, "wiki.enabled", config.index_strategy in {"wiki", "rag_wiki"})
    if config.chunk_method is None:
        for key in ("auto_questions", "parent_child_mode", "parent_chunk_mode"):
            data.pop(key, None)
    else:
        data["parent_child_mode"] = config.chunk_method == "parent_child"
        if isinstance(config, KnowledgeConfiguration) and config.chunk_method != "parent_child":
            data.pop("parent_chunk_mode", None)
    return data


def to_runtime(config: Configuration) -> dict[str, Any]:
    data = to_legacy(config, include_extras=False)
    data["auto_questions"] = config.auto_questions or 0
    data["parent_child_mode"] = config.chunk_method == "parent_child"
    data["parent_chunk_mode"] = config.parent_chunk_mode
    return data


def to_api(config: Configuration) -> dict[str, Any] | None:
    if isinstance(config, KnowledgeConfiguration) and config.knowledge_type == "Folder":
        return None
    data: dict[str, Any] = {}
    if isinstance(config, KnowledgeConfiguration):
        data["sources"] = {}
    for spec in fields_for(config.scope):
        value = getattr(config, spec.column)
        if spec.api_path.startswith("sources.") and value is None:
            active_secret = (
                spec.secret and isinstance(config, KnowledgeConfiguration)
                and config.third_party_platform is not None
                and spec.column.startswith(config.third_party_platform + "_")
            )
            if not active_secret:
                continue
        path = spec.path(config.scope)
        if spec.secret:
            set_path(data, path + "_set", bool(value))
        else:
            set_path(data, path, str(value) if isinstance(value, UUID) else value)
    return data


def from_api(nested: Mapping[str, Any], current: Configuration) -> Configuration:
    if not isinstance(nested, Mapping):
        raise ConfigurationError("expected_object")
    specs = {spec.path(current.scope): spec for spec in fields_for(current.scope)}
    submitted: dict[str, Any] = {}

    def visit(data: Mapping[str, Any], prefix: str = "") -> None:
        for key, value in data.items():
            if not isinstance(key, str) or "." in key:
                raise ConfigurationError("unknown_api_field")
            path = prefix + str(key)
            if path in specs:
                submitted[path] = value
            elif any(candidate.startswith(path + ".") for candidate in specs):
                if not isinstance(value, Mapping):
                    raise ConfigurationError("expected_group_object", path)
                visit(value, path + ".")
            else:
                raise ConfigurationError("unknown_api_field")

    visit(nested)
    values = current.model_dump()
    for path, raw in submitted.items():
        spec = specs[path]
        if raw is None and (
            not spec.is_nullable(current.scope)
            or spec.column
            in {
                "chunk_method",
                "auto_questions",
            }
        ):
            raise ConfigurationError("not_nullable", spec.column)
        if spec.allowed and raw is not None and raw not in spec.allowed:
            raise ConfigurationError("invalid_enum", spec.column)
        if spec.column == "wiki_language" and isinstance(raw, str):
            raw = raw.strip()
        if spec.py_type is UUID and isinstance(raw, str):
            try:
                raw = UUID(raw)
            except ValueError:
                raise ConfigurationError("invalid_uuid", spec.column) from None
        selecting_platform = (
            spec.column == "third_party_platform"
            and isinstance(current, KnowledgeConfiguration)
            and current.source == "third_party"
            and current.third_party_platform is None
        )
        if spec.column in {"graph_pipeline", "third_party_platform"} and (
            raw != getattr(current, spec.column) and not selecting_platform
        ):
            raise ConfigurationError("immutable_field", spec.column)
        values[spec.column] = deepcopy(raw)
    prefix = "defaults." if current.scope == Scope.KNOWLEDGE else ""
    chunk_path = prefix + "chunking."
    custom_paths = {chunk_path + leaf for leaf in ("max_tokens", "overlap_tokens", "delimiters")}
    if values["chunk_method"] == "default":
        if any(
            path in custom_paths or path.startswith(chunk_path + "parent.") for path in submitted
        ):
            raise ConfigurationError("default_with_custom_parameters", "chunking")
        if chunk_path + "method" in submitted:
            defaults = new_configuration(Scope.DOCUMENT)
            for spec in fields_for(Scope.DOCUMENT):
                if (
                    prefix + spec.api_path in custom_paths
                    or spec.api_path.startswith("chunking.parent.")
                ):
                    values[spec.column] = getattr(defaults, spec.column)
    if isinstance(current, KnowledgeConfiguration) and "sources" in nested:
        sources = nested["sources"]
        allowed = (
            {"website"}
            if current.source == "website"
            else ({"third_party"} if current.source == "third_party" else set())
        )
        if set(sources) - allowed:
            raise ConfigurationError("unrelated_source_group", "sources")
        third_party = sources.get("third_party", {})
        if set(third_party) - {"platform", values["third_party_platform"]}:
            raise ConfigurationError("unrelated_platform_group", "sources")
    config = validate_values(values, current.scope)
    if isinstance(config, KnowledgeConfiguration) and any(
        path.startswith("sources.")
        and getattr(config, specs[path].column) != getattr(current, specs[path].column)
        for path in submitted
    ):
        config.require_complete_source()
    return config
