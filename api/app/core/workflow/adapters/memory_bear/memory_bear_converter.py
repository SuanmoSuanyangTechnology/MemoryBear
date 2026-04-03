# -*- coding: UTF-8 -*-
from app.core.workflow.adapters.base_converter import BaseConverter
from app.core.workflow.adapters.errors import ExceptionDefinition, ExceptionType
from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.configs import (
    StartNodeConfig,
    EndNodeConfig,
    LLMNodeConfig,
    AgentNodeConfig,
    IfElseNodeConfig,
    KnowledgeRetrievalNodeConfig,
    AssignerNodeConfig,
    CodeNodeConfig,
    HttpRequestNodeConfig,
    JinjaRenderNodeConfig,
    VariableAggregatorNodeConfig,
    ParameterExtractorNodeConfig,
    LoopNodeConfig,
    IterationNodeConfig,
    QuestionClassifierNodeConfig,
    ToolNodeConfig,
    MemoryReadNodeConfig,
    MemoryWriteNodeConfig,
    NoteNodeConfig,
    ListOperatorNodeConfig,
    DocExtractorNodeConfig,
)
from app.core.workflow.nodes.enums import NodeType


class MemoryBearConverter(BaseConverter):
    errors: list
    warnings: list

    CONFIG_CLASS_MAP: dict[NodeType, type[BaseNodeConfig]] = {
        NodeType.START: StartNodeConfig,
        NodeType.END: EndNodeConfig,
        NodeType.ANSWER: EndNodeConfig,
        NodeType.LLM: LLMNodeConfig,
        NodeType.AGENT: AgentNodeConfig,
        NodeType.IF_ELSE: IfElseNodeConfig,
        NodeType.KNOWLEDGE_RETRIEVAL: KnowledgeRetrievalNodeConfig,
        NodeType.ASSIGNER: AssignerNodeConfig,
        NodeType.CODE: CodeNodeConfig,
        NodeType.HTTP_REQUEST: HttpRequestNodeConfig,
        NodeType.JINJARENDER: JinjaRenderNodeConfig,
        NodeType.VAR_AGGREGATOR: VariableAggregatorNodeConfig,
        NodeType.PARAMETER_EXTRACTOR: ParameterExtractorNodeConfig,
        NodeType.LOOP: LoopNodeConfig,
        NodeType.ITERATION: IterationNodeConfig,
        NodeType.QUESTION_CLASSIFIER: QuestionClassifierNodeConfig,
        NodeType.TOOL: ToolNodeConfig,
        NodeType.MEMORY_READ: MemoryReadNodeConfig,
        NodeType.MEMORY_WRITE: MemoryWriteNodeConfig,
        NodeType.NOTES: NoteNodeConfig,
        NodeType.LIST_OPERATOR: ListOperatorNodeConfig,
        NodeType.DOCUMENT_EXTRACTOR: DocExtractorNodeConfig,
    }

    @staticmethod
    def _convert_file(var):
        return None

    @staticmethod
    def _convert_array_file(var):
        return []

    def config_validate(self, node_id: str, node_name: str, config_cls: type[BaseNodeConfig], value: dict):
        try:
            return config_cls.model_validate(value)
        except Exception as e:
            self.errors.append(ExceptionDefinition(
                type=ExceptionType.CONFIG,
                node_id=node_id,
                node_name=node_name,
                detail=str(e)
            ))
            return None

    def get_node_convert(self, node_type: NodeType):
        config_cls = self.CONFIG_CLASS_MAP.get(node_type)
        if not config_cls:
            return lambda node_id, node_name, config: config

        def validate(node_id: str, node_name: str, config: dict):
            self.config_validate(node_id, node_name, config_cls, config)
            return config

        return validate
