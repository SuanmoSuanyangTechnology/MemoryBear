import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.schemas.chunk_schema import RetrieveType


@dataclass(frozen=True)
class ModelRuntimeSnapshot:
    model_name: str
    provider: str
    api_key: str = field(repr=False)
    api_base: str | None = None
    capability: tuple[str, ...] = ()
    is_omni: bool = False
    model_type: str | None = None

    @classmethod
    def from_api_key(cls, api_key: Any) -> "ModelRuntimeSnapshot":
        return cls(
            model_name=api_key.model_name,
            provider=api_key.provider,
            api_key=api_key.api_key,
            api_base=api_key.api_base,
            capability=tuple(api_key.capability or ()),
            is_omni=bool(api_key.is_omni),
            model_type=getattr(api_key, "model_type", None),
        )


@dataclass(frozen=True)
class RetrievalPrincipal:
    id: uuid.UUID | None
    username: str | None
    tenant_id: uuid.UUID | None
    current_workspace_id: uuid.UUID | None
    is_superuser: bool

    @classmethod
    def from_user(cls, user: Any) -> "RetrievalPrincipal | None":
        if user is None:
            return None
        if isinstance(user, cls):
            return user
        return cls(
            id=getattr(user, "id", None),
            username=getattr(user, "username", None),
            tenant_id=getattr(user, "tenant_id", None),
            current_workspace_id=getattr(user, "current_workspace_id", None),
            is_superuser=bool(getattr(user, "is_superuser", False)),
        )


@dataclass(frozen=True)
class RetrievalParams:
    similarity_threshold: float
    vector_similarity_weight: float | None
    top_k: int
    top_n: int
    retrieve_type: RetrieveType
    rerank_score_threshold: float = 0.1


@dataclass(frozen=True)
class RetrievalTarget:
    knowledge_id: uuid.UUID
    workspace_id: uuid.UUID
    index_name: str
    params: RetrievalParams
    embedding: ModelRuntimeSnapshot
    reranker: ModelRuntimeSnapshot | None


@dataclass(frozen=True)
class RetrievalSearchOptions:
    indices: str
    top_k: int
    score_threshold: float | None
    file_names_filter: tuple[str, ...]
    document_ids_include: tuple[str, ...] | None
    knn_num_candidates: int | None


@dataclass(frozen=True)
class GraphRetrievalSnapshot:
    query: str
    workspace_ids: tuple[str, ...]
    knowledge_ids: tuple[str, ...]
    llm: ModelRuntimeSnapshot
    embedding: ModelRuntimeSnapshot


@dataclass(frozen=True)
class RetrievalPreparation:
    targets: tuple[RetrievalTarget, ...]
    tenant_id: uuid.UUID | None
    metadata_defs_by_kb: Mapping[uuid.UUID, Mapping[str, dict[str, Any]]]
    common_metadata_defs: Mapping[str, dict[str, Any]]
    metadata_llm: ModelRuntimeSnapshot | None
    graph: GraphRetrievalSnapshot | None
