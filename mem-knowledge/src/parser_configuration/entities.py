"""Typed saved values; required fields deliberately have no business defaults."""

from typing import Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .fields import Scope

SOURCE_BY_TYPE = {
    "General": "space",
    "Web": "website",
    "Third-party": "third_party",
    "Folder": None,
}
PLACEHOLDERS = frozenset({"User ID", "Token", "App ID", "App Secret", "Folder Token"})
SOURCE_FIELDS = {
    "website": (
        "web_entry_url", "web_max_pages", "web_delay_seconds",
        "web_timeout_seconds", "web_user_agent",
    ),
    "yuque": ("yuque_user_id", "yuque_token"),
    "feishu": ("feishu_app_id", "feishu_app_secret", "feishu_folder_token"),
}


def valid_source_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return url.scheme in {"http", "https"} and bool(url.hostname) and not any(
            char.isspace() for char in value
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
