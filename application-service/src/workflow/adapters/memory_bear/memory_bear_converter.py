# -*- coding: UTF-8 -*-
from pydantic import BaseModel, ValidationError

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


_MISSING_HINTS = {
    "tool_id": "请选择工具",
    "input_list": "请选择要处理的输入列表",
    "categories": "请配置分类列表",
    "class_name": "请填写分类名称",
    "cases": "请配置分支条件",
}

_VALIDATOR_HINTS = {
    "query 与 image_query 必须填写其中一个": "未配置查询内容，请在文本查询和图片查询中选择一项",
    "query 与 image_query 只能二选一，不能同时填写": "文本查询和图片查询不能同时填写，请只保留一项",
    "At least one cases are required": "请至少配置一个分支条件",
    "Custom auth header not specified": "请填写自定义认证请求头名称",
    "API key for authentication not specified": "请填写认证用的 API Key",
    "When content_type is 'form-data', data must be a list of HttpFormData": "请选择表单字段列表作为请求体",
    "When content_type is JSON, data must be of type str": "请以字符串形式填写 JSON 请求体",
    "When content_type is x-www-form-urlencoded, data must be an object(dict)": "请以键值对象填写表单请求体",
    "When content_type is raw/binary, data must be a string (File descriptor)": "请以字符串形式填写 raw/binary 请求体",
}

_TYPE_HINTS = {
    "string_type": "请填写文本",
    "list_type": "请提供列表",
    "dict_type": "请提供键值对象",
    "int_parsing": "请填写整数",
    "int_type": "请填写整数",
    "uuid_parsing": "请提供有效的 UUID",
}


_ITERATION_HINTS = {
    "input": "请选择要遍历的输入列表",
    "output": "请选择迭代输出变量",
}
_QUERY_REQUIRED = "query 与 image_query 必须填写其中一个"


def _unconfigured_fields(config_cls, value):
    def is_empty(field):
        item = value.get(field) if isinstance(value, dict) else getattr(value, field, None)
        return item is None or isinstance(item, str) and not item.strip()

    if config_cls is IterationNodeConfig:
        return {(field,): hint for field, hint in _ITERATION_HINTS.items() if is_empty(field)}
    if config_cls is KnowledgeRetrievalNodeConfig and all(is_empty(field) for field in ("query", "image_query")):
        return {("query",): _VALIDATOR_HINTS[_QUERY_REQUIRED]}
    return {}


def validate_import_config(config_cls: type[BaseModel], value: dict | BaseModel) -> tuple[BaseModel | None, str | None]:
    """Validate the full node config and retain all actionable import errors."""
    pending = _unconfigured_fields(config_cls, value)
    details = []
    validated = None
    try:
        validated = config_cls.model_validate(value)
    except ValidationError as exc:
        for issue in exc.errors(include_input=False, include_url=False):
            loc = issue["loc"]
            kind = issue["type"]
            fallback = issue.get("msg") or "配置校验失败"
            if loc in pending and kind in ("missing", "string_type"):
                message = pending.pop(loc)
            elif kind == "missing":
                message = _MISSING_HINTS.get(loc[-1] if loc else None, "请补全此必填配置")
            elif kind == "value_error":
                reason = str(issue.get("ctx", {}).get("error") or "").strip()
                message = _VALIDATOR_HINTS.get(reason, reason or "配置校验失败")
                if config_cls is KnowledgeRetrievalNodeConfig and not loc and reason == _QUERY_REQUIRED:
                    pending.pop(("query",), None)
            else:
                message = _TYPE_HINTS.get(kind, fallback)
            field = ".".join(map(str, loc))
            details.append(f"{field}: {message}" if field else message)
    except Exception as exc:
        return None, str(exc) or "配置校验失败"

    details.extend(f"{'.'.join(loc)}: {hint}" for loc, hint in pending.items())
    return (None, "；".join(details)) if details else (validated, None)


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
        validated, detail = validate_import_config(config_cls, value)
        if detail is not None:
            self.errors.append(ExceptionDefinition(
                type=ExceptionType.CONFIG,
                node_id=node_id,
                node_name=node_name,
                detail=detail
            ))
        return validated

    def get_node_convert(self, node_type: NodeType):
        config_cls = self.CONFIG_CLASS_MAP.get(node_type)
        if not config_cls:
            return lambda node_id, node_name, config: config

        def validate(node_id: str, node_name: str, config: dict):
            self.config_validate(node_id, node_name, config_cls, config)
            return config

        return validate
