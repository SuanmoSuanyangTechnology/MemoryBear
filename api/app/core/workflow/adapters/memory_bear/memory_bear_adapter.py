# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/25 14:11
from typing import Any

from app.core.workflow.adapters.base_adapter import (
    PlatformMetadata,
    PlatformType,
    BasePlatformAdapter,
    WorkflowParserResult
)
from app.schemas.workflow_schema import ExecutionConfig


class MemoryBearAdapter(BasePlatformAdapter):
    NODE_TYPE_MAPPING = {}

    @property
    def origin_nodes(self):
        return self.config.get("workflow").get("nodes")

    @property
    def origin_edges(self):
        return self.config.get("workflow").get("edges")

    @property
    def origin_variables(self):
        return self.config.get("workflow").get("variables")

    def get_metadata(self) -> PlatformMetadata:
        return PlatformMetadata(
            platform_name=PlatformType.MEMORY_BEAR,
            version="0.2.5",
            support_node_types=list(self.NODE_TYPE_MAPPING.keys())
        )

    def map_node_type(self, platform_node_type) -> str:
        return platform_node_type

    @staticmethod
    def _valid_nodes(node: dict[str, Any]):
        if "type" not in node["data"]:
            return False
        if "id" not in node or "type" not in node:
            return False
        return True

    def validate_config(self) -> bool:
        require_fields = frozenset({'app', 'workflow'})
        if not all(field in self.config for field in require_fields):
            return False

        for node in self.origin_nodes:
            if not self._valid_nodes(node):
                return False
        return True

    def parse_workflow(self) -> WorkflowParserResult:
        self.nodes = self.origin_nodes
        self.edges = self.origin_edges
        self.conv_variables = self.origin_variables

        return WorkflowParserResult(
            success=True,
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
