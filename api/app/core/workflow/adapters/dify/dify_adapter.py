# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/24 16:05
from typing import Any

from app.core.logging_config import get_logger
from app.core.workflow.adapters.base_adapter import (
    BasePlatformAdapter,
    PlatformMetadata,
    PlatformType,
    WorkflowParserResult
)
from app.core.workflow.adapters.dify.converter import DifyConverter
from app.core.workflow.adapters.errors import ExceptionDefineition, ExceptionType
from app.core.workflow.nodes.enums import NodeType
from app.schemas.workflow_schema import (
    NodeDefinition,
    EdgeDefinition,
    VariableDefinition,
    TriggerConfig,
    ExecutionConfig
)

logger = get_logger()


class DifyAdapter(BasePlatformAdapter, DifyConverter):
    NODE_TYPE_MAPPING = {
        "start": NodeType.START,
        "llm": NodeType.LLM,
        "answer": NodeType.END,
        "if-else": NodeType.IF_ELSE,
        "loop-start": NodeType.CYCLE_START,
        "iteration-start": NodeType.CYCLE_START,
        "assigner": NodeType.ASSIGNER,
        "loop": NodeType.LOOP,
        "iteration": NodeType.ITERATION,
        "loop-end": NodeType.BREAK,
        "code": NodeType.CODE,
        "http-request": NodeType.HTTP_REQUEST,
        "template-transform": NodeType.JINJARENDER,
        "knowledge-retrieval": NodeType.KNOWLEDGE_RETRIEVAL,
        "parameter-extractor": NodeType.PARAMETER_EXTRACTOR,
        "question-classifier": NodeType.QUESTION_CLASSIFIER,
        "variable-aggregator": NodeType.VAR_AGGREGATOR,
        "tool": NodeType.TOOL,
        "": NodeType.NOTES
    }

    def __init__(self, config: dict[str, Any]):
        DifyConverter.__init__(self)
        BasePlatformAdapter.__init__(self,  config)

    def get_metadata(self) -> PlatformMetadata:
        return PlatformMetadata(
            platform_name=PlatformType.DIFY,
            version="0.5.0",
            support_node_types=list(self.NODE_TYPE_MAPPING.keys())
        )

    def map_node_type(self, platform_node_type) -> str:
        return self.NODE_TYPE_MAPPING.get(platform_node_type, NodeType.UNKNOWN)

    @property
    def origin_nodes(self):
        return self.config.get("workflow").get("graph").get("nodes")

    @property
    def origin_edges(self):
        return self.config.get("workflow").get("graph").get("edges")

    @staticmethod
    def _valid_nodes(node: dict[str, Any]):
        if "data" not in node:
            return False
        if "type" not in node["data"]:
            return False
        if "id" not in node or "type" not in node:
            return False
        return True

    def validate_config(self) -> bool:
        require_fields = frozenset({'app', 'kind', 'version', 'workflow'})
        if not all(field in self.config for field in require_fields):
            return False
        if self.config.get("app",{}).get("mode") == "workflow":
            self.errors.append(ExceptionDefineition(
                type=ExceptionType.PLATFORM,
                detail="workflow mode is not supported"
            ))
            return False

        for node in self.origin_nodes:
            if not self._valid_nodes(node):
                return False
        return True

    def parse_workflow(self) -> WorkflowParserResult:
        self._init_node_output_map()
        for node in self.origin_nodes:
            node = self._convert_node(node)
            if node:
                self.nodes.append(node)
        nodes_id = [node.id for node in self.nodes]
        for edge in self.origin_edges:
            source = edge["source"]
            target = edge["target"]
            if source not in nodes_id or target not in nodes_id:
                continue
            edge = self._convert_edge(edge)
            if edge:
                self.edges.append(edge)
        #
        for variable in self.config.get("workflow").get("conversation_variables"):
            con_var = self._convert_variable(variable)
            if variable:
                self.conv_variables.append(con_var)
        #
        # for variables in config.get("workflow").get("environment_variables"):
        #     variable = self._convert_variable(variables)
        #     conv_variables.append(variable)

        trigger = self._convert_trigger({})
        execution_config = self._convert_execution({})

        return WorkflowParserResult(
            success=not self.errors and not self.warnings,
            platform=self.get_metadata(),
            execution_config=execution_config,
            origin_config=self.config,
            trigger=trigger,
            edges=self.edges,
            nodes=self.nodes,
            variables=self.conv_variables,
            warnings=self.warnings,
            errors=self.errors
        )

    def _init_node_output_map(self):
        for node in self.origin_nodes:
            if self.map_node_type(node["data"]["type"]) == NodeType.LLM:
                self.node_output_map[f"{node['id']}.text"] = f"{node['id']}.output"
            elif self.map_node_type(node["data"]["type"]) == NodeType.KNOWLEDGE_RETRIEVAL:
                self.node_output_map[f"{node['id']}.result"] = f"{node['id']}.output"

    def _convert_cycle_node_position(self, node_id: str, position: dict):
        for node in self.origin_nodes:
            if node["id"] == node_id:
                return {
                    "x": node["position"]["x"] + position["x"],
                    "y": node["position"]["y"] + position["y"]
                }
        self.errors.append(
            ExceptionDefineition(
                type=ExceptionType.NODE,
                node_id=node_id,
                detail="parent cycle node not found"
            )
        )
        raise Exception("parent cycle node not found")

    def _convert_node(self, node: dict[str, Any]) -> NodeDefinition | None:
        node_data = node["data"]
        try:
            return NodeDefinition(
                id=node["id"],
                type=self.map_node_type(node_data["type"]),
                name=node_data.get("title") or "notes",
                cycle=node.get("parentId"),
                description=None,
                config=self._convert_node_config(node),
                position={
                    "x": node["position"]["x"],
                    "y": node["position"]["y"]
                } if node.get("parentId") is None else self._convert_cycle_node_position(
                    node["parentId"],
                    node["position"]
                ),
                error_handling=None,
                cache=None
            )
        except Exception as e:
            logger.debug(f"convert node error - {e}", exc_info=True)

    def _convert_node_config(self, node: dict):
        node_data = node["data"]
        node_type = node_data["type"]
        try:
            converter = self.get_node_convert(node_type)
            if node_type not in self.CONFIG_CONVERT_MAP:
                self.errors.append(ExceptionDefineition(
                    type=ExceptionType.NODE,
                    node_id=node["id"],
                    node_name=node["data"]["title"],
                    detail=f"node type {node_type} is unsupported",
                ))
            return converter(node)
        except Exception as e:
            self.errors.append(ExceptionDefineition(
                type=ExceptionType.NODE,
                node_id=node["id"],
                node_name=node["data"]["title"],
                detail=f"convert node error - {e}",
            ))
            raise e

    def _convert_edge(self, edge: dict[str, Any]) -> EdgeDefinition | None:
        try:

            source = edge["source"]
            target = edge["target"]
            label = None
            if source in self.branch_node_cache:
                case_id = edge["sourceHandle"]
                if case_id == "false":
                    label = f'CASE{len(self.branch_node_cache[source])+1}'
                else:
                    label = f'CASE{self.branch_node_cache[source].index(case_id) + 1}'
            if source in self.error_branch_node_cache:
                case_id = edge["sourceHandle"]
                if case_id == "source":
                    label = "SUCCESS"
                else:
                    label = "ERROR"
            return EdgeDefinition(
                id=edge["id"],
                source=source,
                target=target,
                label=label,
            )
        except Exception as e:
            self.errors.append(ExceptionDefineition(
                type=ExceptionType.EDGE,
                detail=f"convert edge error - {e}",
            ))
            logger.debug(f"convert edge error - {e}", exc_info=True)
            return None

    def _convert_variable(self, variable) -> VariableDefinition | None:
        try:
            return VariableDefinition(
                name=variable["name"],
                default=variable["value"],
                type=self.variable_type_map(variable["value_type"]),
                description=variable.get("description")
            )
        except Exception as e:
            self.errors.append(ExceptionDefineition(
                type=ExceptionType.VARIABLE,
                name=variable.get("name"),
                detail=f"convert variable error - {e}",
            ))

    def _convert_trigger(self, trigger: dict[str, Any]) -> TriggerConfig | None:
        pass

    def _convert_execution(self, execution: dict[str, Any]) -> ExecutionConfig:
        return ExecutionConfig()


