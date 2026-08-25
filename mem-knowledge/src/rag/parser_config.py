"""Parser configuration behavior copied from the legacy RAG package."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal

from .knowledge_graph.config import (
    GraphPipeline,
    GraphPipelineConfigError,
    require_graph_mapping,
    resolve_graph_pipeline,
)


def _default_graph_config() -> dict[str, Any]:
    return {
        "use_graphrag": False,
        "scene_name": "",
        "entity_types": ["organization", "person", "geo", "event", "category"],
        "method": "general",
        "resolution": True,
        "community": True,
        "pipeline": GraphPipeline.EVIDENCE.value,
    }


def resolve_layout_recognize(
    parser_config: Mapping[str, Any] | None,
) -> Literal["mineru", "textln"]:
    if parser_config is None or "layout_recognize" not in parser_config:
        return "mineru"
    raw_value = parser_config["layout_recognize"]
    if not isinstance(raw_value, str):
        raise GraphPipelineConfigError(f"unsupported layout_recognize: {raw_value!r}")
    normalized = raw_value.strip().lower()
    if normalized not in {"mineru", "textln"}:
        raise GraphPipelineConfigError(f"unsupported layout_recognize: {raw_value}")
    return "mineru" if normalized == "mineru" else "textln"


def build_default_knowledge_parser_config() -> dict[str, Any]:
    return {
        "entry_url": "https://ai.redbearai.com",
        "max_pages": 20,
        "delay_seconds": 1.0,
        "timeout_seconds": 10,
        "user_agent": "KnowledgeBaseCrawler/1.0",
        "yuque_user_id": "User ID",
        "yuque_token": "Token",
        "feishu_app_id": "App ID",
        "feishu_app_secret": "App Secret",
        "feishu_folder_token": "Folder Token",
        "sync_cron": "30 7 * * 1-5",
        "layout_recognize": "mineru",
        "chunk_token_num": 128,
        "delimiter": "\n",
        "auto_keywords": 0,
        "auto_questions": 0,
        "html4excel": False,
        "parent_child_mode": False,
        "parent_chunk_token_num": 1024,
        "parent_chunk_delimiter": "\n\n",
        "graphrag": _default_graph_config(),
    }


def build_default_document_parser_config() -> dict[str, Any]:
    return {
        "layout_recognize": "mineru",
        "chunk_token_num": 130,
        "delimiter": "\n",
        "auto_keywords": 0,
        "auto_questions": 0,
        "html4excel": False,
        "parent_child_mode": False,
        "parent_chunk_token_num": 1024,
        "parent_chunk_delimiter": "\n\n",
        "graphrag": _default_graph_config(),
    }


def _copy_parser_config(
    parser_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if parser_config is None:
        return {}
    if not isinstance(parser_config, Mapping):
        raise GraphPipelineConfigError("parser_config must be a mapping")
    return deepcopy(dict(parser_config))


def normalize_new_knowledge_parser_config(
    parser_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    requested = _copy_parser_config(parser_config)
    if "layout_recognize" in requested:
        requested["layout_recognize"] = resolve_layout_recognize(requested)
    chunk_mode_requested = any(
        key in requested for key in ("auto_questions", "parent_child_mode", "parent_chunk_mode")
    )
    requested_graph = require_graph_mapping(requested)
    if "pipeline" in requested_graph:
        requested_pipeline = resolve_graph_pipeline({"graphrag": requested_graph})
        if requested_pipeline is not GraphPipeline.EVIDENCE:
            raise GraphPipelineConfigError("new knowledge must use the evidence graph pipeline")

    normalized = build_default_knowledge_parser_config()
    normalized.update({key: value for key, value in requested.items() if key != "graphrag"})
    graph_config = normalized["graphrag"]
    graph_config.update(deepcopy(dict(requested_graph)))
    graph_config["pipeline"] = GraphPipeline.EVIDENCE.value
    if not chunk_mode_requested:
        normalized.pop("auto_questions", None)
        normalized.pop("parent_child_mode", None)
    return normalized


def normalize_knowledge_parser_config_update(
    current: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if incoming is None:
        raise GraphPipelineConfigError("parser_config update must be a mapping")
    current_copy = _copy_parser_config(current)
    incoming_copy = _copy_parser_config(incoming)
    if "layout_recognize" in incoming_copy:
        incoming_copy["layout_recognize"] = resolve_layout_recognize(incoming_copy)
    current_graph = require_graph_mapping(current)
    incoming_graph = require_graph_mapping(incoming_copy)
    current_pipeline = resolve_graph_pipeline(current)
    if "pipeline" in incoming_graph:
        requested_pipeline = resolve_graph_pipeline({"graphrag": incoming_graph})
        if requested_pipeline is not current_pipeline:
            raise GraphPipelineConfigError("graph pipeline changes require managed migration")
    normalized = {key: value for key, value in current_copy.items() if key != "graphrag"}
    normalized.update({key: value for key, value in incoming_copy.items() if key != "graphrag"})
    merged_graph = deepcopy(dict(current_graph))
    merged_graph.update(deepcopy(dict(incoming_graph)))
    merged_graph["pipeline"] = current_pipeline.value
    normalized["graphrag"] = merged_graph
    return normalized


def set_graph_pipeline_for_migration(
    parser_config: Mapping[str, Any] | None,
    pipeline: GraphPipeline | str,
) -> dict[str, Any]:
    try:
        target_pipeline = (
            pipeline
            if isinstance(pipeline, GraphPipeline)
            else GraphPipeline(str(pipeline).strip().lower())
        )
    except ValueError as exc:
        raise GraphPipelineConfigError(f"unsupported graph pipeline: {pipeline}") from exc
    normalized = _copy_parser_config(parser_config)
    graph_config = deepcopy(dict(require_graph_mapping(normalized)))
    graph_config["pipeline"] = target_pipeline.value
    normalized["graphrag"] = graph_config
    return normalized
