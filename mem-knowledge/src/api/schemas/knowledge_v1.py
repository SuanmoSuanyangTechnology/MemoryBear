"""The v1 request envelope; nested configuration is checked against its registry."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...models.owned import PermissionType


class FolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1)
    type: Literal["Folder"]
    parent_id: UUID | None = None
    description: str | None = None
    avatar: str | None = None


class DocumentKnowledgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1)
    type: Literal["document"]
    source: Literal["space", "website", "third_party"]
    index_strategy: Literal["rag", "wiki", "rag_wiki"]
    processing_configuration: dict[str, Any] = Field(repr=False)
    parent_id: UUID | None = None
    description: str | None = None
    avatar: str | None = None
    permission_id: Literal["Private", "Memory"] | None = None
    embedding_id: UUID | None = None
    reranker_id: UUID | None = None
    llm_id: UUID | None = None
    parser_id: str | None = None
    external_id: str | None = Field(None, min_length=1, max_length=36)


KnowledgeCreate = Annotated[DocumentKnowledgeCreate | FolderCreate, Field(discriminator="type")]


class KnowledgePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str | None = Field(None, min_length=1)
    type: Literal["document", "Folder"] | None = None
    source: Literal["space", "website", "third_party"] | None = None
    index_strategy: Literal["rag", "wiki", "rag_wiki"] | None = None
    parent_id: UUID | None = None
    description: str | None = None
    avatar: str | None = None
    permission_id: PermissionType | None = None
    embedding_id: UUID | None = None
    reranker_id: UUID | None = None
    llm_id: UUID | None = None
    parser_id: str | None = None
    processing_configuration: dict[str, Any] | None = Field(None, repr=False)

    @model_validator(mode="after")
    def reject_nonnullable_fields(self):
        for name in {"name", "type", "source", "index_strategy", "processing_configuration"}:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class KnowledgeStatusPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    status: int = Field(strict=True, ge=0, le=1)
