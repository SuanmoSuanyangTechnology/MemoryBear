"""Graph pipeline configuration copied from the legacy RAG package."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


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
