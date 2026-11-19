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
    OutputNodeConfig,
)
from app.core.workflow.nodes.enums import NodeType


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

    @staticmethod
    def _pending_config_fields(node_type: NodeType, config: dict) -> tuple[str, ...]:
        """必填字段留空 = 原应用里就没配好，属于「导入后手动配置」，不是 DSL 结构错误。

        迭代节点的 input/output、知识检索节点的 query/image_query 都是变量选择器，
        前端不选值保存时整个键不会落库。这类节点跑 pydantic 必填校验只会得到
        `Field required` / `必须填写其中一个`，用户无法据此操作，故单独识别。
        """
        if node_type == NodeType.ITERATION:
            return tuple(
                field for field in ("input", "output")
                if not str(config.get(field) or "").strip()
            )
        if node_type == NodeType.KNOWLEDGE_RETRIEVAL:
            has_query = bool(str(config.get("query") or "").strip())
            has_image = bool(str(config.get("image_query") or "").strip())
            return () if (has_query or has_image) else ("query/image_query",)
        return ()

    def get_node_convert(self, node_type: NodeType):
        config_cls = self.CONFIG_CLASS_MAP.get(node_type)
        if not config_cls:
            return lambda node_id, node_name, config: config

        def validate(node_id: str, node_name: str, config: dict):
            pending = self._pending_config_fields(node_type, config)
            if pending:
                self.warnings.append(ExceptionDefinition(
                    type=ExceptionType.CONFIG,
                    node_id=node_id,
                    node_name=node_name,
                    detail=f"{'、'.join(pending)} 未配置，请导入后手动配置"
                ))
                return config
            self.config_validate(node_id, node_name, config_cls, config)
            return config

        return validate
