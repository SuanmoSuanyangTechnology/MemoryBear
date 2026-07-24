import uuid

from app.core.models import RedBearModelConfig
from app.core.rag.knowledge_graph.config import (
    GraphPipelineConfigError,
    is_graph_enabled,
    require_graph_mapping,
)
from app.core.rag.knowledge_graph.models import GraphIndexRuntime
from app.core.rag.retrieval.models import ModelRuntimeSnapshot
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVectorIndexOps,
)
from app.db import get_db_context
from app.models.knowledge_model import Knowledge
from app.models.models_model import ModelConfig
from app.models.workspace_model import Workspace
from app.services.model_service import ModelApiKeyService


def build_model_config(snapshot: ModelRuntimeSnapshot) -> RedBearModelConfig:
    return RedBearModelConfig(
        model_name=snapshot.model_name,
        provider=snapshot.provider,
        api_key=snapshot.api_key,
        base_url=snapshot.api_base,
        capability=list(snapshot.capability),
        is_omni=snapshot.is_omni,
    )


def _require_runtime_api_key(api_key: object | None, model_role: str) -> object:
    if api_key is None:
        raise GraphPipelineConfigError(
            f"no available {model_role} API key for graph runtime"
        )
    return api_key


def _require_model_config(db, model_id: uuid.UUID, model_role: str) -> ModelConfig:
    model_config = db.query(ModelConfig).filter(
        ModelConfig.id == model_id
    ).first()
    if model_config is None:
        raise GraphPipelineConfigError(
            f"{model_role} model config does not exist: {model_id}"
        )
    return model_config


def snapshot_graph_runtime(knowledge_id: str) -> GraphIndexRuntime:
    try:
        knowledge_uuid = uuid.UUID(str(knowledge_id))
    except ValueError as exc:
        raise GraphPipelineConfigError(
            f"invalid knowledge id: {knowledge_id}"
        ) from exc

    with get_db_context() as db:
        knowledge = db.query(Knowledge).filter(
            Knowledge.id == knowledge_uuid
        ).first()
        if knowledge is None:
            raise GraphPipelineConfigError(
                f"knowledge does not exist: {knowledge_id}"
            )
        if not is_graph_enabled(knowledge.parser_config):
            raise GraphPipelineConfigError(
                f"graph is disabled for knowledge: {knowledge_id}"
            )

        workspace = db.query(Workspace).filter(
            Workspace.id == knowledge.workspace_id
        ).first()
        if workspace is None:
            raise GraphPipelineConfigError(
                f"workspace does not exist: {knowledge.workspace_id}"
            )
        if knowledge.llm_id is None or knowledge.embedding_id is None:
            raise GraphPipelineConfigError(
                "graph runtime requires both LLM and embedding models"
            )

        llm_config = _require_model_config(
            db,
            knowledge.llm_id,
            "LLM",
        )
        embedding_config = _require_model_config(
            db,
            knowledge.embedding_id,
            "embedding",
        )

        llm_api_key = _require_runtime_api_key(
            ModelApiKeyService.get_available_api_key(
                db,
                knowledge.llm_id,
                tenant_id=workspace.tenant_id,
            ),
            "LLM",
        )
        embedding_api_key = _require_runtime_api_key(
            ModelApiKeyService.get_available_api_key(
                db,
                knowledge.embedding_id,
                tenant_id=workspace.tenant_id,
            ),
            "embedding",
        )

        graph_config = require_graph_mapping(knowledge.parser_config)
        raw_entity_types = graph_config.get("entity_types") or ()
        if not isinstance(raw_entity_types, (list, tuple)):
            raise GraphPipelineConfigError("graphrag.entity_types must be a list")

        snapshot = GraphIndexRuntime(
            knowledge_id=str(knowledge.id),
            workspace_id=str(workspace.id),
            graph_index_name=f"graphrag_{workspace.id}",
            chunk_index_name=(
                ElasticSearchVectorIndexOps.collection_name_for_knowledge(
                    knowledge.id
                )
            ),
            entity_types=tuple(
                str(entity_type).strip()
                for entity_type in raw_entity_types
                if str(entity_type).strip()
            ),
            scene_name=str(graph_config.get("scene_name") or ""),
            llm=ModelRuntimeSnapshot.from_api_key(
                llm_api_key,
                model_type=str(llm_config.type),
            ),
            embedding=ModelRuntimeSnapshot.from_api_key(
                embedding_api_key,
                model_type=str(embedding_config.type),
            ),
        )

    return snapshot
