"""Validate supplied parser settings without filling defaults or dropping legacy keys."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, ConfigDict, Field, StrictBool
from typing_extensions import TypedDict

from ...rag.wiki.config import MAX_EXTRACT_PROMPT_LENGTH, MAX_LANGUAGE_LENGTH


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("内容不能为空或只有空白字符")
    return value


NonblankText = Annotated[str, AfterValidator(nonblank)]
GraphPipelineValue = Annotated[
    Literal["legacy", "evidence"],
    BeforeValidator(lambda value: value.strip().lower() if isinstance(value, str) else value),
]


class WikiConfigInput(TypedDict, total=False):
    __pydantic_config__ = ConfigDict(extra="allow")
    enabled: StrictBool
    language: Annotated[
        str,
        BeforeValidator(lambda value: value.strip() if isinstance(value, str) else value),
        Field(min_length=1, max_length=MAX_LANGUAGE_LENGTH),
    ]
    extract_prompt: Annotated[str, Field(max_length=MAX_EXTRACT_PROMPT_LENGTH)]


class GraphConfigInput(TypedDict, total=False):
    __pydantic_config__ = ConfigDict(extra="allow")
    pipeline: GraphPipelineValue
    use_graphrag: StrictBool
    entity_types: list[str]


class NewGraphConfigInput(GraphConfigInput, total=False):
    pipeline: Annotated[
        Literal["evidence"],
        BeforeValidator(lambda value: value.strip().lower() if isinstance(value, str) else value),
    ]


class DocumentParserConfig(TypedDict, total=False):
    __pydantic_config__ = ConfigDict(extra="allow")
    chunk_token_num: int
    parent_chunk_token_num: int
    delimiter: str
    parent_chunk_delimiter: str
    parent_child_mode: bool
    auto_questions: int
    auto_keywords: int
    graphrag: GraphConfigInput


class KnowledgeCreateParserConfig(DocumentParserConfig, total=False):
    graphrag: NewGraphConfigInput
    wiki: WikiConfigInput | None


class KnowledgeUpdateParserConfig(DocumentParserConfig, total=False):
    wiki: WikiConfigInput
