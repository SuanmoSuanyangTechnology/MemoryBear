"""补齐知识库和文档解析器配置表，并回填旧配置。

All schema definitions, historical conversion rules and validation live in this
revision. It imports no repository modules and reads no external SQL/config files.
Alembic supplies the connection and transaction; failures must abort the upgrade.
"""

import json
import logging
import re
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import Flag, StrEnum, auto
from functools import cache
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SQLUUID
from sqlalchemy.types import TypeEngine

revision = "b5389f2ea741"
down_revision = "a73cfb6893d0"
branch_labels = None
depends_on = None


class Scope(Flag):
    KNOWLEDGE = auto()
    DOCUMENT = auto()
    COMMON = KNOWLEDGE | DOCUMENT


@dataclass(frozen=True)
class FieldSpec:
    column: str
    api_path: str
    legacy_path: str | None
    py_type: type
    legacy_default: Any
    scope: Scope = Scope.COMMON
    nullable: bool = False
    allowed: tuple[Any, ...] = ()
    secret: bool = False
    aliases: tuple[str, ...] = ()
    owner: bool = False

    def path(self, scope: Scope) -> str:
        prefix = "defaults." if self.scope == Scope.COMMON and scope == Scope.KNOWLEDGE else ""
        return prefix + self.api_path

    def is_nullable(self, scope: Scope) -> bool:
        return self.nullable or (
            scope == Scope.KNOWLEDGE and self.column in {"chunk_method", "auto_questions"}
        )


K = Scope.KNOWLEDGE


D = Scope.DOCUMENT


FIELDS = (
    FieldSpec(
        "layout_recognize",
        "parsing.layout",
        "layout_recognize",
        str,
        "plain",
        allowed=("plain", "mineru", "textln"),
    ),
    FieldSpec("html4excel", "parsing.html4excel", "html4excel", bool, False),
    FieldSpec("analyze_hyperlink", "parsing.follow_hyperlinks", "analyze_hyperlink", bool, False),
    FieldSpec(
        "markdown_hyperlink_enabled",
        "parsing.extract_markdown_hyperlinks",
        "hyperlink_urls",
        bool,
        False,
    ),
    FieldSpec("image_vision_enabled", "image.enabled", "image.vision_enabled", bool, True),
    FieldSpec("image_vision_mode", "image.mode", "image.vision_mode", int, 1, allowed=(0, 1, 2)),
    FieldSpec(
        "embedded_image_vision_enabled",
        "image.embedded_enabled",
        "embedded_image.vision_enabled",
        bool,
        True,
    ),
    FieldSpec(
        "chunk_method",
        "chunking.method",
        None,
        str,
        None,
        allowed=("default", "custom", "parent_child"),
        aliases=("parent_child_mode",),
    ),
    FieldSpec("chunk_token_num", "chunking.max_tokens", "chunk_token_num", int, 128),
    FieldSpec("chunk_overlap", "chunking.overlap_tokens", "chunk_overlap", int, 0),
    FieldSpec(
        "delimiters",
        "chunking.delimiters",
        "delimiter",
        list,
        ["\n", "!", "?", "。", "；", "！", "？"],
    ),
    FieldSpec("auto_keywords", "chunking.auto_keywords", "auto_keywords", int, 0),
    FieldSpec(
        "parent_chunk_mode",
        "chunking.parent.mode",
        "parent_chunk_mode",
        str,
        "paragraph",
        allowed=("paragraph", "full-doc"),
    ),
    FieldSpec(
        "parent_chunk_token_num", "chunking.parent.max_tokens", "parent_chunk_token_num", int, 1024
    ),
    FieldSpec(
        "parent_chunk_delimiters",
        "chunking.parent.delimiters",
        "parent_chunk_delimiter",
        list,
        ["\n\n"],
    ),
    FieldSpec("auto_questions", "qa.questions_per_chunk", "auto_questions", int, 0),
    FieldSpec("qa_prompt", "qa.prompt", "qa_prompt", str, None, nullable=True),
    FieldSpec(
        "doc_type",
        "parsing.document_kind",
        "doc_type",
        str,
        None,
        D,
        nullable=True,
        allowed=("qa",),
    ),
    FieldSpec("multimodal_enabled", "multimodal.enabled", None, bool, False, K),
    FieldSpec("multimodal_enabled", "multimodal.enabled", None, bool, False, D),
    FieldSpec("vision_model_id", "multimodal.vision_model_id", None, UUID, None, D, nullable=True),
    FieldSpec("audio_model_id", "multimodal.audio_model_id", None, UUID, None, D, nullable=True),
    FieldSpec("video_model_id", "multimodal.video_model_id", None, UUID, None, D, nullable=True),
    FieldSpec(
        "image2text_id",
        "multimodal.vision_model_id",
        None,
        UUID,
        None,
        K,
        nullable=True,
        owner=True,
    ),
    FieldSpec(
        "audio2text_id", "multimodal.audio_model_id", None, UUID, None, K, nullable=True, owner=True
    ),
    FieldSpec(
        "video2text_id", "multimodal.video_model_id", None, UUID, None, K, nullable=True, owner=True
    ),
    FieldSpec("graph_enabled", "graph.enabled", "graphrag.use_graphrag", bool, False, K),
    FieldSpec("graph_scene_name", "graph.scene_name", "graphrag.scene_name", str, "", K),
    FieldSpec("graph_entity_types", "graph.entity_types", "graphrag.entity_types", list, [], K),
    FieldSpec("graph_method", "graph.method", "graphrag.method", str, "general", K),
    FieldSpec("graph_resolution", "graph.resolution", "graphrag.resolution", bool, True, K),
    FieldSpec("graph_community", "graph.community", "graphrag.community", bool, True, K),
    FieldSpec(
        "graph_pipeline",
        "graph.pipeline",
        "graphrag.pipeline",
        str,
        "legacy",
        K,
        allowed=("legacy", "evidence"),
    ),
    FieldSpec("wiki_language", "wiki.language", "wiki.language", str, "zh-CN", K),
    FieldSpec("wiki_extract_prompt", "wiki.extract_prompt", "wiki.extract_prompt", str, "", K),
    FieldSpec(
        "web_entry_url", "sources.website.entry_url", "entry_url", str, None, K, nullable=True
    ),
    FieldSpec("web_max_pages", "sources.website.max_pages", "max_pages", int, 20, K, nullable=True),
    FieldSpec(
        "web_delay_seconds",
        "sources.website.delay_seconds",
        "delay_seconds",
        float,
        1.0,
        K,
        nullable=True,
    ),
    FieldSpec(
        "web_timeout_seconds",
        "sources.website.timeout_seconds",
        "timeout_seconds",
        float,
        10.0,
        K,
        nullable=True,
    ),
    FieldSpec(
        "web_user_agent",
        "sources.website.user_agent",
        "user_agent",
        str,
        "KnowledgeBaseCrawler/1.0",
        K,
        nullable=True,
    ),
    FieldSpec(
        "third_party_platform",
        "sources.third_party.platform",
        "_third_party_platform",
        str,
        None,
        K,
        nullable=True,
        allowed=("yuque", "feishu"),
    ),
    FieldSpec(
        "yuque_user_id",
        "sources.third_party.yuque.user_id",
        "yuque_user_id",
        str,
        None,
        K,
        nullable=True,
    ),
    FieldSpec(
        "yuque_token",
        "sources.third_party.yuque.token",
        "yuque_token",
        str,
        None,
        K,
        nullable=True,
        secret=True,
    ),
    FieldSpec(
        "feishu_app_id",
        "sources.third_party.feishu.app_id",
        "feishu_app_id",
        str,
        None,
        K,
        nullable=True,
    ),
    FieldSpec(
        "feishu_app_secret",
        "sources.third_party.feishu.app_secret",
        "feishu_app_secret",
        str,
        None,
        K,
        nullable=True,
        secret=True,
    ),
    FieldSpec(
        "feishu_folder_token",
        "sources.third_party.feishu.folder_token",
        "feishu_folder_token",
        str,
        None,
        K,
        nullable=True,
        secret=True,
    ),
)


def fields_for(scope: Scope) -> tuple[FieldSpec, ...]:
    if scope not in (K, D):
        raise ValueError("a single configuration scope is required")
    return tuple(field for field in FIELDS if field.scope & scope)


SOURCE_BY_TYPE = {
    "General": "space",
    "Web": "website",
    "Third-party": "third_party",
    "Folder": None,
}


PLACEHOLDERS = frozenset({"User ID", "Token", "App ID", "App Secret", "Folder Token"})


SOURCE_FIELDS = {
    "website": (
        "web_entry_url",
        "web_max_pages",
        "web_delay_seconds",
        "web_timeout_seconds",
        "web_user_agent",
    ),
    "yuque": ("yuque_user_id", "yuque_token"),
    "feishu": ("feishu_app_id", "feishu_app_secret", "feishu_folder_token"),
}


def valid_source_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (
            url.scheme in {"http", "https"}
            and bool(url.hostname)
            and not any(char.isspace() for char in value)
        )
    except ValueError:
        return False


class ConfigurationError(ValueError):
    """Safe diagnostic containing a code and registered field, never an input value."""

    def __init__(self, code: str, field: str = "configuration"):
        self.code = code
        self.field = field
        super().__init__(f"{code}: {field}")


class CommonConfiguration(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    layout_recognize: Literal["plain", "mineru", "textln"]
    html4excel: bool
    analyze_hyperlink: bool
    markdown_hyperlink_enabled: bool
    image_vision_enabled: bool
    image_vision_mode: Literal[0, 1, 2]
    embedded_image_vision_enabled: bool
    chunk_method: Literal["default", "custom", "parent_child"] | None
    chunk_token_num: int = Field(gt=0)
    chunk_overlap: int = Field(ge=0)
    delimiters: list[str]
    auto_keywords: int = Field(ge=0)
    parent_chunk_mode: Literal["paragraph", "full-doc"]
    parent_chunk_token_num: int = Field(gt=0)
    parent_chunk_delimiters: list[str]
    auto_questions: int | None = Field(ge=0, le=10)
    qa_prompt: str | None
    multimodal_enabled: bool
    extras: dict[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("image_vision_mode", mode="before")
    @classmethod
    def check_image_mode_type(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ConfigurationError("invalid_integer", "image_vision_mode")
        return value

    @model_validator(mode="after")
    def check_chunking(self) -> Self:
        if self.chunk_method is not None and self.auto_questions is None:
            raise ConfigurationError("required", "auto_questions")
        if self.chunk_method == "parent_child" and self.auto_questions != 0:
            raise ConfigurationError("parent_child_qa_conflict", "auto_questions")
        return self

    @property
    def scope(self) -> Scope:
        return Scope.KNOWLEDGE if isinstance(self, KnowledgeConfiguration) else Scope.DOCUMENT


class KnowledgeConfiguration(CommonConfiguration):
    knowledge_type: Literal["General", "Web", "Third-party", "Folder"]
    source: Literal["space", "website", "third_party"] | None
    index_strategy: Literal["rag", "wiki", "rag_wiki"] | None
    image2text_id: UUID | None
    audio2text_id: UUID | None
    video2text_id: UUID | None
    graph_enabled: bool
    graph_scene_name: str
    graph_entity_types: list[str]
    graph_method: str
    graph_resolution: bool
    graph_community: bool
    graph_pipeline: Literal["legacy", "evidence"]
    wiki_language: str = Field(min_length=1, max_length=64)
    wiki_extract_prompt: str = Field(max_length=12000)
    web_entry_url: str | None
    web_max_pages: int | None = Field(ge=1)
    web_delay_seconds: float | None = Field(ge=0, allow_inf_nan=False)
    web_timeout_seconds: float | None = Field(gt=0, allow_inf_nan=False)
    web_user_agent: str | None
    third_party_platform: Literal["yuque", "feishu"] | None
    yuque_user_id: str | None
    yuque_token: str | None = Field(repr=False)
    feishu_app_id: str | None
    feishu_app_secret: str | None = Field(repr=False)
    feishu_folder_token: str | None = Field(repr=False)

    @model_validator(mode="after")
    def check_source(self) -> Self:
        if self.source != SOURCE_BY_TYPE[self.knowledge_type]:
            raise ConfigurationError("source_type_mismatch", "source")
        if (self.knowledge_type == "Folder") != (self.index_strategy is None):
            raise ConfigurationError("invalid_index_strategy", "index_strategy")
        if self.index_strategy == "wiki" and self.auto_questions not in (0, None):
            raise ConfigurationError("wiki_qa_conflict", "auto_questions")
        active = "website" if self.source == "website" else self.third_party_platform
        if self.source != "third_party" and self.third_party_platform is not None:
            raise ConfigurationError("platform_required_for_source", "third_party_platform")
        for group, names in SOURCE_FIELDS.items():
            for name in names:
                value = getattr(self, name)
                if group != active and value is not None:
                    raise ConfigurationError("unrelated_source_field", name)
        if self.web_entry_url and not valid_source_url(self.web_entry_url):
            raise ConfigurationError("invalid_source_url", "web_entry_url")
        return self

    def source_issues(self) -> list[ConfigurationError]:
        """Incomplete historical sources remain readable, but cannot synchronize."""
        if self.source == "third_party" and self.third_party_platform is None:
            return [ConfigurationError("platform_required_for_source", "third_party_platform")]
        active = "website" if self.source == "website" else self.third_party_platform
        issues = []
        for name in SOURCE_FIELDS.get(active, ()):
            value = getattr(self, name)
            if value is None or (
                isinstance(value, str) and (not value.strip() or value.strip() in PLACEHOLDERS)
            ):
                issues.append(ConfigurationError("required_source_field", name))
        return issues

    def require_complete_source(self) -> None:
        issues = self.source_issues()
        if issues:
            raise issues[0]


class DocumentConfiguration(CommonConfiguration):
    chunk_method: Literal["default", "custom", "parent_child"]
    auto_questions: int = Field(ge=0, le=10)
    doc_type: Literal["qa"] | None
    vision_model_id: UUID | None
    audio_model_id: UUID | None
    video_model_id: UUID | None


Configuration = KnowledgeConfiguration | DocumentConfiguration


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


def resolve_layout_recognize(
    parser_config: Mapping[str, Any] | None,
) -> Literal["plain", "mineru", "textln"]:
    """Default missing layouts to Plain and unsupported explicit values to MinerU."""
    if parser_config is None or "layout_recognize" not in parser_config:
        return "plain"
    raw_value = parser_config["layout_recognize"]
    if not isinstance(raw_value, str):
        return "mineru"
    normalized = raw_value.strip().lower()
    aliases = {
        "plain": "plain",
        "plaintext": "plain",
        "plain text": "plain",
        "mineru": "mineru",
        "textln": "textln",
    }
    return aliases.get(normalized, "mineru")


def _scoped_vision_enabled(parser_config: dict | None, scope: str) -> bool:
    scope_config = (parser_config or {}).get(scope)
    raw_value = scope_config.get("vision_enabled", True) if isinstance(scope_config, dict) else True
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw_value)


def is_embedded_image_vision_enabled(parser_config: dict | None) -> bool:
    return _scoped_vision_enabled(parser_config, "embedded_image")


def is_direct_image_vision_enabled(parser_config: dict | None) -> bool:
    return _scoped_vision_enabled(parser_config, "image")


class GraphPipelineConfigError(ValueError):
    """Raised when a managed graph pipeline configuration is invalid."""


class GraphPipeline(StrEnum):
    LEGACY = "legacy"
    EVIDENCE = "evidence"


def require_graph_mapping(
    parser_config: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if parser_config is None:
        return {}
    if not isinstance(parser_config, Mapping):
        raise GraphPipelineConfigError("parser_config must be a mapping")
    if "graphrag" not in parser_config:
        return {}
    graph_config = parser_config["graphrag"]
    if not isinstance(graph_config, Mapping):
        raise GraphPipelineConfigError("graphrag must be a mapping")
    return graph_config


def resolve_graph_pipeline(
    parser_config: Mapping[str, Any] | None,
) -> GraphPipeline:
    graph_config = require_graph_mapping(parser_config)
    raw_value = graph_config.get("pipeline", GraphPipeline.LEGACY.value)
    try:
        return GraphPipeline(str(raw_value).strip().lower())
    except ValueError as exc:
        raise GraphPipelineConfigError(f"unsupported graph pipeline: {raw_value}") from exc


def is_graph_enabled(parser_config: Mapping[str, Any] | None) -> bool:
    graph_config = require_graph_mapping(parser_config)
    return graph_config.get("use_graphrag") is True


MAX_LANGUAGE_LENGTH = 64


MAX_EXTRACT_PROMPT_LENGTH = 12000


@dataclass(frozen=True)
class WikiConfig:
    enabled: bool = False
    language: str = "zh-CN"
    extract_prompt: str = ""


def read_wiki_config(parser_config: Mapping[str, Any] | None) -> WikiConfig:
    value = (parser_config or {}).get("wiki", {})
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("wiki must be an object")
    enabled = value.get("enabled", False)
    language = value.get("language", "zh-CN")
    prompt = value.get("extract_prompt", "")
    if not isinstance(enabled, bool):
        raise ValueError("wiki.enabled must be a boolean")
    if not isinstance(language, str) or not 0 < len(language.strip()) <= MAX_LANGUAGE_LENGTH:
        raise ValueError("wiki.language must be a non-empty language identifier")
    if not isinstance(prompt, str) or len(prompt) > MAX_EXTRACT_PROMPT_LENGTH:
        raise ValueError("wiki.extract_prompt is invalid")
    return WikiConfig(enabled, language.strip(), prompt.strip())


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


def _legacy_parent_child_mode(data: Mapping[str, Any], scope: Scope) -> bool:
    # Keep the pre-migration model semantics independent of configuration-backed models.
    if scope == Scope.KNOWLEDGE:
        if "auto_questions" not in data and "parent_chunk_mode" not in data:
            return False
        if (
            "auto_questions" in data
            and "parent_chunk_mode" not in data
            and not data.get("parent_child_mode", False)
        ):
            return False
        return True
    if "parent_child_mode" in data:
        value = data.get("parent_child_mode")
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "y", "on"}
        return bool(value)
    return data.get("parent_chunk_mode") in ["paragraph", "full-doc"]


def legacy_runtime(data: Mapping[str, Any] | None, scope: Scope) -> dict[str, Any]:
    data = dict(data or {})
    parent_child = _legacy_parent_child_mode(data, scope)
    image = data.get("image")
    image = image if isinstance(image, dict) else {}
    result = {
        "layout_recognize": resolve_layout_recognize(data),
        # Compatibility-only fields have no active consumer in this service.
        "html4excel": (
            str(data.get("html4excel", False)).strip().lower() in {"1", "true", "yes", "on"}
        ),
        "auto_keywords": int(data.get("auto_keywords", 0)),
        "analyze_hyperlink": bool(data.get("analyze_hyperlink", False)),
        "hyperlink_urls": bool(data.get("hyperlink_urls", False)),
        "image": {
            "vision_enabled": is_direct_image_vision_enabled(data),
            "vision_mode": image.get("vision_mode", 1),
        },
        "embedded_image": {"vision_enabled": is_embedded_image_vision_enabled(data)},
        "chunk_token_num": int(data.get("chunk_token_num", 128)),
        "chunk_overlap": int(data.get("chunk_overlap", 0) or 0),
        "delimiter": data.get("delimiter", "\n!?。；！？"),
        "parent_child_mode": parent_child,
        "parent_chunk_mode": data.get("parent_chunk_mode", "paragraph"),
        "parent_chunk_token_num": int(data.get("parent_chunk_token_num", 1024)),
        "parent_chunk_delimiter": data.get("parent_chunk_delimiter", "\n\n"),
        "auto_questions": int(data.get("auto_questions", 0) or 0),
        "qa_prompt": data.get("qa_prompt"),
    }
    if scope == Scope.DOCUMENT:
        result["doc_type"] = data.get("doc_type")
        return result
    graph = data.get("graphrag", {})
    wiki = read_wiki_config(data)
    result["graphrag"] = {
        "use_graphrag": is_graph_enabled(data),
        "scene_name": str(graph.get("scene_name") or ""),
        "entity_types": [
            str(item).strip() for item in (graph.get("entity_types") or []) if str(item).strip()
        ],
        "method": graph.get("method", "general"),
        "resolution": graph.get("resolution", True),
        "community": graph.get("community", True),
        "pipeline": resolve_graph_pipeline(data).value,
    }
    result["wiki"] = {
        "enabled": wiki.enabled,
        "language": wiki.language,
        "extract_prompt": wiki.extract_prompt,
    }
    return result


def runtime_differences(
    data: Mapping[str, Any] | None, config: Configuration, runtime: dict[str, Any]
) -> list[str]:
    """Compare every effective parsing key, plus every raw delimiter consumer."""
    expected = legacy_runtime(data, config.scope)
    differences: list[str] = []

    def compare(old: Mapping[str, Any], new: Mapping[str, Any], prefix: str = "") -> None:
        for key, value in old.items():
            path = prefix + key
            actual = new.get(key)
            if isinstance(value, dict) and isinstance(actual, dict):
                compare(value, actual, path + ".")
            elif path == "auto_questions" and config.chunk_method == "parent_child" and actual == 0:
                continue
            elif type(value) is not type(actual) or value != actual:
                differences.append(path)

    compare(expected, runtime)
    raw = data or {}
    # preview.py uses raw truthiness plus an unregistered historical alias;
    # context.py uses raw truthiness when no explicit output mode was supplied.
    old_preview = bool(raw.get("parent_child_mode") or raw.get("chunk_mode") == "parent_child")
    new_preview = bool(
        runtime.get("parent_child_mode") or runtime.get("chunk_mode") == "parent_child"
    )
    if old_preview != new_preview:
        differences.append("preview.parent_child_mode")
    if bool(raw.get("parent_child_mode", False)) != bool(runtime.get("parent_child_mode", False)):
        differences.append("context.parent_child_mode")
    # Preview/block consumers take the exact string without the naive default.
    for key in ("delimiter", "parent_chunk_delimiter"):
        if raw.get(key) != runtime.get(key):
            differences.append(f"raw_consumer.{key}")
    if isinstance(config, KnowledgeConfiguration):
        if config.source == "website":
            defaults = {
                "entry_url": "",
                "max_pages": 20,
                "delay_seconds": 1.0,
                "timeout_seconds": 10,
                "user_agent": "KnowledgeBaseCrawler/1.0",
            }
            for key, default in defaults.items():
                if raw.get(key, default) != runtime.get(key):
                    differences.append(key)
        elif config.source == "third_party":
            keys = (
                ("yuque_user_id", "yuque_token")
                if config.third_party_platform == "yuque"
                else (
                    "feishu_app_id",
                    "feishu_app_secret",
                    "feishu_folder_token",
                )
            )
            for key in keys:
                if raw.get(key, "") != runtime.get(key):
                    differences.append(key)
    return sorted(set(differences))


BATCH_SIZE = 500


@dataclass
class MigrationReport:
    status: str = "ready"
    preserved: dict[str, int] = field(default_factory=lambda: {"knowledges": 0, "documents": 0})
    counts: dict[str, int] = field(default_factory=lambda: {"knowledges": 0, "documents": 0})
    defaulted: dict[str, int] = field(default_factory=dict)
    normalized: dict[str, int] = field(default_factory=dict)
    delimiters: dict[str, int] = field(default_factory=dict)
    parent_child_qa_zeroed: int = 0
    extras_keys: list[str] = field(default_factory=list)
    unconvertible: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    repairs: list[dict[str, str]] = field(default_factory=list)
    source_pending: list[dict[str, Any]] = field(default_factory=list)
    batches: dict[str, list[int]] = field(
        default_factory=lambda: {"knowledges": [], "documents": []}
    )
    validation: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extras_keys(data: Mapping[str, Any], prefix: str = "") -> Iterator[str]:
    for key, value in data.items():
        path = prefix + key
        yield path
        if isinstance(value, dict):
            yield from _extras_keys(value, path + ".")


def inspect_record(
    row: Mapping[str, Any], scope: Scope, context: KnowledgeContext, report: MigrationReport
) -> Configuration | None:
    table_name = "knowledges" if scope == Scope.KNOWLEDGE else "documents"
    report.counts[table_name] += 1
    try:
        result = from_historical(row["parser_config"], scope, context=context)
        report.repairs.extend(
            {"table": table_name, "id": str(row["id"]), "field": name, "reason": reason}
            for name, reason in sorted(result.report.repairs.items())
        )
        if isinstance(result.config, KnowledgeConfiguration) and result.config.source_issues():
            report.source_pending.append(
                {
                    "table": table_name,
                    "id": str(row["id"]),
                    "fields": [issue.field for issue in result.config.source_issues()],
                }
            )
        for name in result.report.defaulted:
            key = table_name + "." + name
            report.defaulted[key] = report.defaulted.get(key, 0) + 1
        for name in result.report.normalized:
            key = table_name + "." + name
            report.normalized[key] = report.normalized.get(key, 0) + 1
        report.parent_child_qa_zeroed += int(result.report.parent_child_qa_zeroed)
        report.extras_keys = sorted(
            set(report.extras_keys)
            | {table_name + "." + name for name in _extras_keys(result.config.extras)}
        )
        for name in ("delimiters", "parent_chunk_delimiters"):
            values = getattr(result.config, name)
            for category, count in {
                "records": 1,
                "items": len(values),
                "empty": int(not values),
                "multichar": sum(len(item) > 1 for item in values),
                "duplicates": len(values) - len(set(values)),
            }.items():
                key = table_name + "." + name + "." + category
                report.delimiters[key] = report.delimiters.get(key, 0) + count
        differences = runtime_differences(
            result.comparison_data, result.config, to_runtime(result.config)
        )
        if differences:
            raise ConfigurationError("runtime_mismatch", ",".join(differences))
        return result.config
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        report.unconvertible.append(
            {
                "table": table_name,
                "id": str(row["id"]),
                "reason": exc.code
                if isinstance(exc, ConfigurationError)
                else "legacy_runtime_invalid",
                "field": exc.field if isinstance(exc, ConfigurationError) else "configuration",
            }
        )
        report.status = "blocked"
        return None


def _context(row: Mapping[str, Any], urls: tuple[str | None, ...] = ()) -> KnowledgeContext:
    return KnowledgeContext(
        knowledge_type=row["type"],
        image2text_id=row.get("image2text_id"),
        audio2text_id=row.get("audio2text_id"),
        video2text_id=row.get("video2text_id"),
        file_urls=urls,
    )


def _skip_orphan_document(row: Mapping[str, Any], report: MigrationReport) -> None:
    report.counts["documents"] += 1
    report.skipped.append(
        {
            "table": "documents",
            "id": str(row["id"]),
            "kb_id": str(row["kb_id"]),
            "reason": "missing_knowledge",
        }
    )


def _saved_configuration(row, owner, scope):
    values = {spec.column: row[spec.column] for spec in fields_for(scope) if not spec.owner}
    values["extras"] = row["extras"]
    if scope == Scope.KNOWLEDGE:
        values.update(
            knowledge_type=owner["type"],
            source=owner["source"],
            index_strategy=owner["index_strategy"],
        )
        values.update({spec.column: owner[spec.column] for spec in fields_for(scope) if spec.owner})
    return validate_values(values, scope)


def _record_pending(config, owner_id, report):
    if isinstance(config, KnowledgeConfiguration) and config.source_issues():
        report.source_pending.append(
            {
                "table": "knowledges",
                "id": str(owner_id),
                "fields": [issue.field for issue in config.source_issues()],
            }
        )


def backfill(connection: Connection, tables: dict[str, Table]) -> MigrationReport:
    """Fill missing rows without committing, overwriting snapshots or saved configs.

    The caller owns the transaction, locks and revision registration. Page reads are
    bounded; new values are read back and compared before proceeding to the next page.
    Existing values are validated directly, never compared with the lossy old snapshot.
    """
    report = MigrationReport()
    knowledges = tables["knowledges"]
    files = tables["files"]
    for scope, name, config_name, key in (
        (Scope.KNOWLEDGE, "knowledges", "knowledge_parser_configs", "knowledge_id"),
        (Scope.DOCUMENT, "documents", "document_parser_configs", "document_id"),
    ):
        owner_table, config_table = tables[name], tables[config_name]
        last_id = None
        while True:
            query = select(owner_table).order_by(owner_table.c.id).limit(BATCH_SIZE)
            if last_id is not None:
                query = query.where(owner_table.c.id > last_id)
            rows = list(connection.execute(query).mappings())
            if not rows:
                break
            ids = [row["id"] for row in rows]
            saved = {
                row[key]: row
                for row in connection.execute(
                    select(config_table).where(config_table.c[key].in_(ids))
                ).mappings()
            }
            owners = {row["id"]: row for row in rows}
            if scope == Scope.DOCUMENT:
                owners = {
                    row["id"]: row
                    for row in connection.execute(
                        select(knowledges).where(
                            knowledges.c.id.in_({row["kb_id"] for row in rows})
                        )
                    ).mappings()
                }
            third_party = [
                row["id"]
                for row in rows
                if scope == Scope.KNOWLEDGE
                and row["id"] not in saved
                and row["type"] == "Third-party"
            ]
            urls = {}
            if third_party:
                for kb_id, url in connection.execute(
                    select(files.c.kb_id, files.c.file_url).where(
                        files.c.kb_id.in_(third_party),
                        files.c.file_role == "source",
                    )
                ):
                    urls.setdefault(kb_id, []).append(url)
            expected = {}
            for row in rows:
                owner_id = row["id"] if scope == Scope.KNOWLEDGE else row["kb_id"]
                owner = owners.get(owner_id)
                if owner is None:
                    _skip_orphan_document(row, report)
                    continue
                try:
                    if row["id"] in saved:
                        current = _saved_configuration(saved[row["id"]], owner, scope)
                        report.counts[name] += 1
                        report.preserved[name] += 1
                        _record_pending(current, row["id"], report)
                        continue
                    config = inspect_record(
                        row, scope, _context(owner, tuple(urls.get(owner_id, ()))), report
                    )
                    if config is None:
                        failure = report.unconvertible[-1]
                        raise ConfigurationError(failure["reason"], failure["field"])
                    if scope == Scope.KNOWLEDGE:
                        # A partial/manual rollout may already have saved immutable identity.
                        # Keep it, including wiki-only, instead of deriving it again from JSON.
                        values = config.model_dump()
                        for field_name in ("source", "index_strategy"):
                            if owner[field_name] is not None:
                                values[field_name] = owner[field_name]
                        config = validate_values(values, scope)
                        connection.execute(
                            knowledges.update()
                            .where(knowledges.c.id == row["id"])
                            .values(
                                source=config.source,
                                index_strategy=config.index_strategy,
                            )
                        )
                    values = {
                        spec.column: getattr(config, spec.column)
                        for spec in fields_for(scope)
                        if not spec.owner
                    }
                    connection.execute(
                        config_table.insert().values(
                            **{key: row["id"]},
                            **values,
                            extras=config.extras,
                        )
                    )
                    expected[row["id"]] = config
                except ConfigurationError as exc:
                    raise ConfigurationError(exc.code, f"{name}/{row['id']}/{exc.field}") from None
            actual_rows = (
                {
                    row[key]: row
                    for row in connection.execute(
                        select(config_table).where(config_table.c[key].in_(list(expected)))
                    ).mappings()
                }
                if expected
                else {}
            )
            for owner_id, config in expected.items():
                if owner_id not in actual_rows:
                    raise ConfigurationError("configuration_missing", f"{name}/{owner_id}")
                owner = owners[owner_id] if scope == Scope.KNOWLEDGE else {}
                if scope == Scope.KNOWLEDGE:
                    owner = {
                        **owner,
                        "source": config.source,
                        "index_strategy": config.index_strategy,
                    }
                actual = _saved_configuration(actual_rows[owner_id], owner, scope)
                if actual != config:
                    raise ConfigurationError("stored_configuration_mismatch", f"{name}/{owner_id}")
            report.batches[name].append(len(expected))
            last_id = rows[-1]["id"]

    counts = {}
    for name, config_name, key in (
        ("knowledges", "knowledge_parser_configs", "knowledge_id"),
        ("documents", "document_parser_configs", "document_id"),
    ):
        owners, configs = tables[name], tables[config_name]
        owner_ids = select(owners.c.id)
        if name == "documents":
            owner_ids = owner_ids.where(
                select(knowledges.c.id)
                .where(
                    knowledges.c.id == owners.c.kb_id,
                )
                .exists()
            )
        owner_count = connection.scalar(select(func.count()).select_from(owner_ids.subquery()))
        count = connection.scalar(
            select(func.count())
            .select_from(configs)
            .where(
                configs.c[key].in_(owner_ids),
            )
        )
        if count != owner_count:
            raise ConfigurationError("row_count_mismatch", config_name)
        counts[config_name] = count
    report.status = "validated"
    report.validation = {
        "passed": True,
        "counts": counts,
        "skipped_documents": len(report.skipped),
        "checks": [
            "owner_counts_including_folders",
            "typed_entities",
            "runtime_values",
            "registered_repairs",
            "stored_values",
            "existing_configurations_preserved",
        ],
    }
    return report


logger = logging.getLogger("alembic.runtime.migration")


SOURCE_TYPE_CHECK = (
    "type IS NOT NULL AND ((type = 'Folder' AND source IS NULL) OR "
    "(source IS NOT NULL AND ((type = 'General' AND source = 'space') OR "
    "(type = 'Web' AND source = 'website') OR "
    "(type = 'Third-party' AND source = 'third_party'))))"
)


INDEX_STRATEGY_CHECK = (
    "type IS NOT NULL AND ((type = 'Folder' AND index_strategy IS NULL) OR "
    "(type <> 'Folder' AND index_strategy IS NOT NULL "
    "AND index_strategy IN ('rag', 'wiki', 'rag_wiki')))"
)


def configuration_table(name: str, metadata: MetaData, scope: Scope) -> Table:
    knowledge = scope == Scope.KNOWLEDGE
    owner = "knowledge" if knowledge else "document"
    prefix = "kpc" if knowledge else "dpc"
    sql_types: dict[type, Callable[[], TypeEngine[Any]]] = {
        str: Text,
        int: Integer,
        float: Float,
        bool: Boolean,
        list: lambda: ARRAY(Text),
        UUID: lambda: SQLUUID(as_uuid=True),
    }
    columns: list[Column[Any]] = [
        Column(
            owner + "_id",
            SQLUUID(as_uuid=True),
            ForeignKey(
                "knowledges.id" if knowledge else "documents.id",
                ondelete="CASCADE",
                name=f"fk_{name}_owner",
            ),
            primary_key=True,
            comment="所属对象 ID；物理删除级联，软删除保留",
        )
    ]
    constraints = []
    for spec in fields_for(scope):
        if spec.owner:
            continue
        sql_type = (
            SmallInteger() if spec.column == "image_vision_mode" else sql_types[spec.py_type]()
        )
        columns.append(
            Column(
                spec.column,
                sql_type,
                nullable=spec.is_nullable(scope),
                comment=f"解析配置 {spec.path(scope)}",
            )
        )
        if spec.allowed:
            allowed = ", ".join(repr(item) for item in spec.allowed)
            constraints.append(
                CheckConstraint(f"{spec.column} IN ({allowed})", name=f"ck_{prefix}_{spec.column}")
            )
    checks = {
        "chunk_token_num_positive": "chunk_token_num > 0",
        "chunk_overlap_nonnegative": "chunk_overlap >= 0",
        "auto_keywords_nonnegative": "auto_keywords >= 0",
        "parent_tokens_positive": "parent_chunk_token_num > 0",
        "auto_questions_range": "auto_questions BETWEEN 0 AND 10",
        "parent_child_qa": "chunk_method IS DISTINCT FROM 'parent_child' OR auto_questions = 0",
        "extras": "jsonb_typeof(extras) = 'object'",
    }
    if knowledge:
        checks.update(
            {
                "chunk_method_fields": "chunk_method IS NULL OR auto_questions IS NOT NULL",
                "wiki_language": "char_length(wiki_language) BETWEEN 1 AND 64",
                "wiki_prompt": "char_length(wiki_extract_prompt) <= 12000",
                "other_platform_cleared": "(third_party_platform IS NOT DISTINCT FROM 'yuque' OR "
                "(yuque_user_id IS NULL AND yuque_token IS NULL)) AND "
                "(third_party_platform IS NOT DISTINCT FROM 'feishu' OR "
                "(feishu_app_id IS NULL AND feishu_app_secret IS NULL "
                "AND feishu_folder_token IS NULL))",
                "web_values": "(web_max_pages IS NULL OR web_max_pages >= 1) AND "
                "(web_delay_seconds IS NULL OR web_delay_seconds >= 0) AND "
                "(web_timeout_seconds IS NULL OR web_timeout_seconds > 0)",
            }
        )
    constraints.extend(
        CheckConstraint(expression, name=f"ck_{prefix}_{key}") for key, expression in checks.items()
    )
    columns.extend(
        [
            Column(
                "extras",
                JSONB,
                nullable=False,
                server_default=text("'{}'::jsonb"),
                comment="未登记历史键；只供旧响应回显",
            ),
            Column(
                "created_at",
                DateTime,
                nullable=False,
                server_default=text("timezone('utc', now())"),
                comment="创建时间 UTC",
            ),
            Column(
                "updated_at",
                DateTime,
                nullable=False,
                server_default=text("timezone('utc', now())"),
                comment="修改时间 UTC",
            ),
        ]
    )
    return Table(name, metadata, *columns, *constraints, comment="所属对象的一对一解析配置")


def _tables() -> dict[str, Table]:
    metadata = MetaData()
    Table(
        "knowledges",
        metadata,
        Column("id", SQLUUID(as_uuid=True), primary_key=True),
        Column("type", Text),
        Column("parser_config", JSONB),
        Column("source", sa.String(16), comment="知识库来源；Folder 为空"),
        Column("index_strategy", sa.String(16), comment="索引策略；Folder 为空"),
        *(
            Column(name, SQLUUID(as_uuid=True))
            for name in ("image2text_id", "audio2text_id", "video2text_id")
        ),
    )
    Table(
        "documents",
        metadata,
        Column("id", SQLUUID(as_uuid=True), primary_key=True),
        Column("kb_id", SQLUUID(as_uuid=True)),
        Column("parser_config", JSONB),
    )
    Table(
        "files",
        metadata,
        Column("kb_id", SQLUUID(as_uuid=True)),
        Column("file_url", Text),
        Column("file_role", Text),
    )
    configuration_table("knowledge_parser_configs", metadata, Scope.KNOWLEDGE)
    configuration_table("document_parser_configs", metadata, Scope.DOCUMENT)
    return dict(metadata.tables)


def _default(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", "", value.replace("::text", "")).lower()


def _column_matches(actual, expected, dialect) -> bool:
    def sql_type(value):
        compiled = str(value.compile(dialect=dialect)).upper()
        return "DOUBLE PRECISION" if compiled == "FLOAT" else compiled

    default = str(expected.server_default.arg) if expected.server_default is not None else None
    return (
        sql_type(actual["type"]) == sql_type(expected.type)
        and actual["nullable"] == expected.nullable
        and _default(actual.get("default")) == _default(default)
        and not actual.get("identity")
        and not actual.get("computed")
    )


def _schema_error(table: str, part: str) -> None:
    raise RuntimeError(f"Knowledge redesign schema mismatch: {table}.{part}; no data was replaced")


def _adopt_table(connection, table: Table) -> None:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table.name, schema="public"):
        table.create(connection)
        return
    columns = {
        column["name"]: column for column in inspector.get_columns(table.name, schema="public")
    }
    if columns.keys() != set(table.c.keys()):
        _schema_error(table.name, "columns")
    for column in table.c:
        if not _column_matches(columns[column.name], column, connection.dialect):
            _schema_error(table.name, column.name)
    key = list(table.primary_key.columns.keys())
    if inspector.get_pk_constraint(table.name, schema="public")["constrained_columns"] != key:
        _schema_error(table.name, "primary_key")
    if inspector.get_unique_constraints(table.name, schema="public"):
        _schema_error(table.name, "unique_constraints")
    for index in inspector.get_indexes(table.name, schema="public"):
        if index.get("unique") and not index.get("duplicates_constraint"):
            _schema_error(table.name, "unique_index")
    foreign_keys = inspector.get_foreign_keys(table.name, schema="public")
    foreign_key = next(iter(table.foreign_keys))
    if foreign_keys:
        if len(foreign_keys) != 1:
            _schema_error(table.name, "foreign_keys")
        actual = foreign_keys[0]
        if not (
            actual["constrained_columns"] == key
            and actual["referred_table"] == foreign_key.column.table.name
            and actual.get("referred_schema") in (None, "public")
            and actual["referred_columns"] == ["id"]
            and actual.get("options", {}).get("ondelete") == "CASCADE"
            and actual.get("options", {}).get("onupdate", "NO ACTION") == "NO ACTION"
            and not actual.get("options", {}).get("deferrable")
        ):
            _schema_error(table.name, "owner_foreign_key")
    else:
        op.create_foreign_key(
            foreign_key.constraint.name,
            table.name,
            foreign_key.column.table.name,
            key,
            ["id"],
            ondelete="CASCADE",
            postgresql_not_valid=True,
        )


def _replace_checks(connection, table: Table) -> None:
    """Normalize the two shipped manual-DDL layouts to the final frozen checks.

    No row values are changed. NOT VALID permits backfill before the final full
    validation, while enforcing every new insert/update immediately.
    """
    expected = {c.name: c for c in table.constraints if isinstance(c, CheckConstraint)}
    prefix = "ck_kpc_" if table.name == "knowledge_parser_configs" else "ck_dpc_"
    known_old = {
        prefix + suffix
        for suffix in (
            "layout",
            "chunk_token_num",
            "chunk_overlap",
            "auto_keywords",
            "parent_mode",
            "parent_tokens",
            "auto_questions",
            "image_mode",
        )
    }
    if table.name == "knowledge_parser_configs":
        known_old.update({"ck_kpc_yuque_fields", "ck_kpc_feishu_fields"})
    actual = sa.inspect(connection).get_check_constraints(table.name, schema="public")
    if any(check["name"] not in expected.keys() | known_old for check in actual):
        _schema_error(table.name, "unknown_check_constraint")
    for check in actual:
        op.drop_constraint(check["name"], table.name, type_="check")
    for name, constraint in sorted(expected.items()):
        op.create_check_constraint(
            name, table.name, str(constraint.sqltext), postgresql_not_valid=True
        )


def _expand(connection, tables):
    if connection.exec_driver_sql("SELECT current_schema()").scalar_one() != "public":
        raise RuntimeError("Knowledge redesign requires the public schema")
    # The deployment must stop old writers before upgrading. These locks also keep
    # the paged conversion stable if a stray writer is still connected during upgrade.
    connection.exec_driver_sql(
        "LOCK TABLE knowledges, documents, files IN SHARE ROW EXCLUSIVE MODE"
    )
    columns = {
        column["name"]: column
        for column in sa.inspect(connection).get_columns("knowledges", schema="public")
    }
    for name in ("source", "index_strategy"):
        column = tables["knowledges"].c[name]
        if name not in columns:
            op.add_column(
                "knowledges", Column(name, sa.String(16), nullable=True, comment=column.comment)
            )
        elif not _column_matches(columns[name], column, connection.dialect):
            _schema_error("knowledges", name)
    # Earlier manual SQL allowed NULL to bypass its checks. Replace just the two
    # redesign checks and validate after backfill, preserving all original checks.
    checks = {
        check["name"]
        for check in sa.inspect(connection).get_check_constraints("knowledges", schema="public")
    }
    for name, expression in (
        ("ck_knowledges_source_type", SOURCE_TYPE_CHECK),
        ("ck_knowledges_index_strategy", INDEX_STRATEGY_CHECK),
    ):
        if name in checks:
            op.drop_constraint(name, "knowledges", type_="check")
        op.create_check_constraint(name, "knowledges", expression, postgresql_not_valid=True)
    for name in ("knowledge_parser_configs", "document_parser_configs"):
        _adopt_table(connection, tables[name])
        connection.exec_driver_sql(f"LOCK TABLE {name} IN SHARE ROW EXCLUSIVE MODE")
        _replace_checks(connection, tables[name])


def _validate_constraints(connection):
    for name in ("ck_knowledges_source_type", "ck_knowledges_index_strategy"):
        connection.exec_driver_sql(f"ALTER TABLE knowledges VALIDATE CONSTRAINT {name}")
    for name in ("knowledge_parser_configs", "document_parser_configs"):
        inspector = sa.inspect(connection)
        constraints = inspector.get_check_constraints(name, schema="public")
        constraints += inspector.get_foreign_keys(name, schema="public")
        quote = connection.dialect.identifier_preparer.quote
        for constraint in constraints:
            connection.exec_driver_sql(
                f"ALTER TABLE {name} VALIDATE CONSTRAINT {quote(constraint['name'])}"
            )


def _upgrade_parser_configuration(connection: Connection) -> None:
    try:
        tables = _tables()
        _expand(connection, tables)
        report = backfill(connection, tables)
        _validate_constraints(connection)
    except ConfigurationError as exc:
        # Neither exception chaining nor bound SQL parameters may expose credentials.
        raise RuntimeError(f"Knowledge redesign backfill failed: {exc.code}: {exc.field}") from None
    except sa.exc.SQLAlchemyError as exc:
        raise RuntimeError(
            f"Knowledge redesign database operation failed ({type(exc).__name__}); "
            "transaction must roll back"
        ) from None
    logger.info(
        "Knowledge redesign validated; commit is pending: %s",
        json.dumps(report.as_dict(), ensure_ascii=False),
    )


def upgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "Knowledge redesign needs live data reads; "
            "use the normal online Alembic upgrade, not --sql"
        )
    connection = op.get_bind()
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise RuntimeError("Knowledge redesign requires an Alembic PostgreSQL transaction")

    _upgrade_parser_configuration(connection)


def downgrade() -> None:
    raise RuntimeError(
        "Knowledge redesign preserves configuration data. "
        "Roll back the application, retaining these tables and the Alembic revision."
    )
