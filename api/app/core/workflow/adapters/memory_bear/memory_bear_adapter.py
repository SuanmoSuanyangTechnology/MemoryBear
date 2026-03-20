# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/25 14:11
from typing import Any

from app.core.logging_config import get_logger
from app.core.workflow.adapters.base_adapter import (
    PlatformMetadata,
    PlatformType,
    BasePlatformAdapter,
    WorkflowParserResult
)
from app.core.workflow.adapters.errors import ExceptionDefineition, ExceptionType, UnsupportNodeType
from app.core.workflow.adapters.memory_bear.memory_bear_converter import MemoryBearConverter
from app.core.workflow.nodes.enums import NodeType
from app.schemas.workflow_schema import ExecutionConfig, NodeDefinition, EdgeDefinition, VariableDefinition

logger = get_logger()

VALID_NODE_TYPES = frozenset(t.value for t in NodeType if t != NodeType.UNKNOWN)


class MemoryBearAdapter(BasePlatformAdapter, MemoryBearConverter):
    NODE_TYPE_MAPPING = {t.value: t for t in NodeType}

    def __init__(self, config: dict[str, Any]):
        MemoryBearConverter.__init__(self)
        BasePlatformAdapter.__init__(self, config)

    @property
    def origin_nodes(self):
        return self.config.get("workflow").get("nodes") or []

    @property
    def origin_edges(self):
        return self.config.get("workflow").get("edges") or []

    @property
    def origin_variables(self):
        return self.config.get("workflow").get("variables") or []

    def get_metadata(self) -> PlatformMetadata:
        return PlatformMetadata(
            platform_name=PlatformType.MEMORY_BEAR,
            version="0.2.5",
            support_node_types=list(VALID_NODE_TYPES)
        )

    def map_node_type(self, platform_node_type: str) -> NodeType:
        return self.NODE_TYPE_MAPPING.get(platform_node_type, NodeType.UNKNOWN)

    @staticmethod
    def _valid_node(node: dict[str, Any]) -> bool:
        if "id" not in node or "type" not in node:
            return False
        if not isinstance(node.get("config"), dict):
            return False
        return True

    def validate_config(self) -> bool:
        require_fields = frozenset({'app', 'workflow'})
        if not all(field in self.config for field in require_fields):
            return False
        for node in self.origin_nodes:
            if not self._valid_node(node):
                return False
        return True

    def _convert_node(self, node: dict[str, Any]) -> NodeDefinition | None:
        node_id = node.get("id")
        node_name = node.get("name")
        try:
            node_type = self.map_node_type(node["type"])
            if node_type == NodeType.UNKNOWN:
                self.errors.append(UnsupportNodeType(
                    node_id=node_id,
                    node_type=node["type"]
                ))
                return None

            config = node.get("config") or {}
            converter = self.get_node_convert(node_type)
            converter(node_id, node_name, config)  # validates and appends errors if invalid

            return NodeDefinition(**node)
        except Exception as e:
            self.errors.append(ExceptionDefineition(
                type=ExceptionType.NODE,
                node_id=node_id,
                node_name=node_name,
                detail=f"convert node error - {e}"
            ))
            logger.debug(f"MemoryBear convert node error - {e}", exc_info=True)
            return None

    def _convert_edge(self, edge: dict[str, Any], valid_node_ids: set) -> EdgeDefinition | None:
        try:
            if edge.get("source") not in valid_node_ids or edge.get("target") not in valid_node_ids:
                self.warnings.append(ExceptionDefineition(
                    type=ExceptionType.EDGE,
                    detail=f"edge {edge.get('id')} skipped: source or target node not found"
                ))
                return None
            return EdgeDefinition(**edge)
        except Exception as e:
            self.errors.append(ExceptionDefineition(
                type=ExceptionType.EDGE,
                detail=f"convert edge error - {e}"
            ))
            logger.debug(f"MemoryBear convert edge error - {e}", exc_info=True)
            return None

    def _convert_variable(self, variable: dict[str, Any]) -> VariableDefinition | None:
        try:
            return VariableDefinition(**variable)
        except Exception as e:
            self.warnings.append(ExceptionDefineition(
                type=ExceptionType.VARIABLE,
                name=variable.get("name"),
                detail=f"convert variable error - {e}"
            ))
            logger.debug(f"MemoryBear convert variable error - {e}", exc_info=True)
            return None

    def parse_workflow(self) -> WorkflowParserResult:
        for node in self.origin_nodes:
            converted = self._convert_node(node)
            if converted:
                self.nodes.append(converted)

        valid_node_ids = {n.id for n in self.nodes}

        for edge in self.origin_edges:
            converted = self._convert_edge(edge, valid_node_ids)
            if converted:
                self.edges.append(converted)

        for variable in self.origin_variables:
            converted = self._convert_variable(variable)
            if converted:
                self.conv_variables.append(converted)

        return WorkflowParserResult(
            success=not self.errors and not self.warnings,
            platform=self.get_metadata(),
            execution_config=ExecutionConfig(),
            origin_config=self.config,
            trigger=None,
            edges=self.edges,
            nodes=self.nodes,
            variables=self.conv_variables,
            warnings=self.warnings,
            errors=self.errors,
        )
