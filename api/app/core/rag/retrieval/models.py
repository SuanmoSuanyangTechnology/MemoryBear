import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NoReturn, TypeAlias

from app.core.rag.knowledge_graph.config import GraphPipeline
from app.schemas.chunk_schema import RetrieveType


MetadataScalar: TypeAlias = str | bytes | int | float | bool | None | uuid.UUID
FrozenMetadataValue: TypeAlias = (
    MetadataScalar
    | tuple["FrozenMetadataValue", ...]
    | frozenset["FrozenMetadataValue"]
    | Mapping[str, "FrozenMetadataValue"]
)
FrozenMetadataFieldDefinition: TypeAlias = Mapping[str, FrozenMetadataValue]
FrozenMetadataDefinitions: TypeAlias = Mapping[str, FrozenMetadataFieldDefinition]
FrozenMetadataDefinitionsByKnowledge: TypeAlias = Mapping[uuid.UUID, FrozenMetadataDefinitions]


class FrozenMetadataMapping(dict[object, object]):
    __slots__ = ()

    def __new__(cls, *args: object, **kwargs: object) -> "FrozenMetadataMapping":
        mapping = dict.__new__(cls)
        dict.__init__(mapping, *args, **kwargs)
        return mapping

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __setattr__(self, name: str, value: object) -> NoReturn:
        self._reject_mutation()

    def __delattr__(self, name: str) -> NoReturn:
        self._reject_mutation()

    def _reject_mutation(self, *args: object, **kwargs: object) -> NoReturn:
        raise TypeError("Frozen metadata mappings cannot be mutated")

    __setitem__ = _reject_mutation
    __delitem__ = _reject_mutation
    clear = _reject_mutation
    pop = _reject_mutation
    popitem = _reject_mutation
    setdefault = _reject_mutation
    update = _reject_mutation
    __ior__ = _reject_mutation

    def __reduce__(self) -> tuple[type["FrozenMetadataMapping"], tuple[dict[object, object]]]:
        return type(self), (dict(self),)

    def __reduce_ex__(self, protocol: int) -> tuple[type["FrozenMetadataMapping"], tuple[dict[object, object]]]:
        return self.__reduce__()


def _freeze_metadata_value(value: object) -> FrozenMetadataValue:
    if isinstance(value, Mapping):
        return _freeze_metadata_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_metadata_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_metadata_value(item) for item in value)
    if value is None or isinstance(value, (str, bytes, int, float, uuid.UUID)):
        return value
    raise TypeError(f"Unsupported metadata snapshot value: {type(value).__name__}")


def _freeze_metadata_mapping(value: Mapping[object, object]) -> FrozenMetadataFieldDefinition:
    frozen: dict[str, FrozenMetadataValue] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise TypeError("Metadata mapping keys must be strings")
        frozen[key] = _freeze_metadata_value(nested_value)
    return FrozenMetadataMapping(frozen)


def _freeze_metadata_definitions(value: Mapping[object, object]) -> FrozenMetadataDefinitions:
    frozen: dict[str, FrozenMetadataFieldDefinition] = {}
    for field_name, field_definition in value.items():
        if not isinstance(field_name, str):
            raise TypeError("Metadata field names must be strings")
        if not isinstance(field_definition, Mapping):
            raise TypeError("Metadata field definitions must be mappings")
        frozen[field_name] = _freeze_metadata_mapping(field_definition)
    return FrozenMetadataMapping(frozen)


def _freeze_metadata_definitions_by_knowledge(
    value: Mapping[object, object],
) -> FrozenMetadataDefinitionsByKnowledge:
    frozen: dict[uuid.UUID, FrozenMetadataDefinitions] = {}
    for knowledge_id, field_definitions in value.items():
        if not isinstance(knowledge_id, uuid.UUID):
            raise TypeError("Metadata knowledge IDs must be UUIDs")
        if not isinstance(field_definitions, Mapping):
            raise TypeError("Knowledge metadata definitions must be mappings")
        frozen[knowledge_id] = _freeze_metadata_definitions(field_definitions)
    return FrozenMetadataMapping(frozen)


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
    def from_api_key(
        cls,
        api_key: Any,
        model_type: str | None = None,
    ) -> "ModelRuntimeSnapshot":
        return cls(
            model_name=api_key.model_name,
            provider=api_key.provider,
            api_key=api_key.api_key,
            api_base=api_key.api_base,
            capability=tuple(api_key.capability or ()),
            is_omni=bool(api_key.is_omni),
            model_type=model_type if model_type is not None else getattr(api_key, "model_type", None),
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


@dataclass
class RetrievalTimings:
    """Mutable request-local phase timings for retrieval observability."""

    db_snapshot_ms: int = 0
    metadata_llm_ms: int = 0
    metadata_query_ms: int = 0
    embedding_ms: int = 0
    es_vector_ms: int = 0
    es_fulltext_ms: int = 0
    parent_resolution_ms: int = 0
    local_rerank_ms: int = 0
    global_rerank_ms: int = 0
    graph_wait_ms: int = 0
    graph_ms: int = 0

    def as_log_fields(self) -> dict[str, int]:
        return {
            "db_snapshot_ms": self.db_snapshot_ms,
            "metadata_llm_ms": self.metadata_llm_ms,
            "metadata_query_ms": self.metadata_query_ms,
            "embedding_ms": self.embedding_ms,
            "es_vector_ms": self.es_vector_ms,
            "es_fulltext_ms": self.es_fulltext_ms,
            "parent_resolution_ms": self.parent_resolution_ms,
            "local_rerank_ms": self.local_rerank_ms,
            "global_rerank_ms": self.global_rerank_ms,
            "graph_wait_ms": self.graph_wait_ms,
            "graph_ms": self.graph_ms,
        }


@dataclass(frozen=True)
class GraphTargetSnapshot:
    knowledge_id: uuid.UUID
    workspace_id: uuid.UUID
    chunk_index_name: str
    graph_index_name: str
    pipeline: GraphPipeline
    llm: ModelRuntimeSnapshot
    embedding: ModelRuntimeSnapshot


@dataclass(frozen=True)
class GraphRetrievalSnapshot:
    query: str
    pipeline: GraphPipeline
    targets: tuple[GraphTargetSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.targets:
            raise ValueError("graph retrieval snapshot requires at least one target")
        if any(target.pipeline is not self.pipeline for target in self.targets):
            raise ValueError("graph retrieval targets must use the selected pipeline")

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        return tuple(str(target.workspace_id) for target in self.targets)

    @property
    def knowledge_ids(self) -> tuple[str, ...]:
        return tuple(str(target.knowledge_id) for target in self.targets)

    @property
    def llm(self) -> ModelRuntimeSnapshot:
        return self.targets[0].llm

    @property
    def embedding(self) -> ModelRuntimeSnapshot:
        return self.targets[0].embedding


@dataclass(frozen=True)
class RetrievalPreparation:
    targets: tuple[RetrievalTarget, ...]
    tenant_id: uuid.UUID | None
    metadata_defs_by_kb: FrozenMetadataDefinitionsByKnowledge
    common_metadata_defs: FrozenMetadataDefinitions
    metadata_llm: ModelRuntimeSnapshot | None
    graph: GraphRetrievalSnapshot | None
    request_reranker: ModelRuntimeSnapshot | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata_defs_by_kb",
            _freeze_metadata_definitions_by_knowledge(self.metadata_defs_by_kb),
        )
        object.__setattr__(
            self,
            "common_metadata_defs",
            _freeze_metadata_definitions(self.common_metadata_defs),
        )
