# -*- coding: UTF-8 -*-
from pydantic import ValidationError

from ...adapters.base_converter import BaseConverter
from ...adapters.errors import ExceptionDefinition, ExceptionType
from ...nodes.base_config import BaseNodeConfig
from ...nodes.configs import (
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
    OutputNodeConfig,
)
from ....utils.workflow_node_types import NodeType


class MemoryBearConverter(BaseConverter):
    errors: list
    warnings: list

    CONFIG_CLASS_MAP: dict[NodeType, type[BaseNodeConfig]] = {
        NodeType.START: StartNodeConfig,
        NodeType.END: EndNodeConfig,
        NodeType.ANSWER: EndNodeConfig,
        NodeType.OUTPUT: OutputNodeConfig,
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

    @staticmethod
    def _validation_detail(config_cls, exc):
        if not isinstance(exc, ValidationError):
            return str(exc)
        details = []
        translated = False
        for issue in exc.errors(include_input=False, include_url=False):
            loc = issue["loc"]
            if config_cls is IterationNodeConfig and issue["type"] == "missing" and loc in (("input",), ("output",)):
                details.append("请选择要遍历的输入列表" if loc == ("input",) else "请选择迭代输出变量")
                translated = True
            elif (config_cls is KnowledgeRetrievalNodeConfig and not loc
                  and issue["type"] == "value_error"
                  and str(issue.get("ctx", {}).get("error")) == "query 与 image_query 必须填写其中一个"):
                details.append("未配置查询内容，文本查询或图片查询至少填写一项")
                translated = True
            else:
                field = ".".join(map(str, loc))
                details.append(f"{field}: {issue['msg']}" if field else issue["msg"])
        return "；".join(details) if translated else str(exc)

    def config_validate(self, node_id: str, node_name: str, config_cls: type[BaseNodeConfig], value: dict):
        try:
            return config_cls.model_validate(value)
        except Exception as e:
            self.errors.append(ExceptionDefinition(
                type=ExceptionType.CONFIG,
                node_id=node_id,
                node_name=node_name,
                detail=self._validation_detail(config_cls, e)
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
