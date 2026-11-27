"""Creation defaults and field metadata from the configuration registry."""

from typing import Any
from uuid import UUID

from .conversion import new_configuration, to_api
from .entities import DocumentConfiguration, KnowledgeConfiguration
from .fields import Scope, fields_for

WORKSPACE_MODELS = {
    "embedding_id": "embedding",
    "reranker_id": "rerank",
    "llm_id": "llm",
    "vision_model_id": "vision",
    "audio_model_id": "audio",
    "video_model_id": "video",
}

CONSTRAINTS = [
    {
        "code": "parent_child_qa",
        "description": "父子分块的每块问题数必须为 0。",
        "fields": ["defaults.chunking.method", "defaults.qa.questions_per_chunk"],
    },
    {
        "code": "wiki_qa",
        "description": "仅 Wiki 策略不允许文档自动问答。",
        "fields": ["index_strategy", "defaults.qa.questions_per_chunk"],
    },
    {
        "code": "sources",
        "description": "仅接受当前来源与平台的连接字段；必需凭据不能清空。",
        "fields": ["source", "sources.website", "sources.third_party"],
    },
    {
        "code": "immutable",
        "description": (
            "类型、来源、策略不可修改；第三方平台确定后不可修改，历史未定平台允许首次补齐。"
        ),
        "fields": ["type", "source", "index_strategy", "sources.third_party.platform"],
    },
    {
        "code": "multimodal_inheritance",
        "description": "文档模型 null 继承库；库模型 null 回退空间。",
        "fields": [
            "multimodal.vision_model_id",
            "multimodal.audio_model_id",
            "multimodal.video_model_id",
        ],
    },
    {
        "code": "default_chunking",
        "description": "默认分块不接受自定义长度、重叠、分隔符或父块参数。",
        "fields": ["defaults.chunking"],
    },
    {
        "code": "graph_pipeline",
        "description": "新建使用 evidence；切换流水线必须使用专门迁移命令。",
        "fields": ["graph.pipeline"],
    },
]


def configuration_options(workspace: Any) -> dict[str, Any]:
    knowledge = new_configuration(Scope.KNOWLEDGE)
    document = new_configuration(Scope.DOCUMENT)
    fields = []
    types: dict[type, str] = {
        str: "string",
        UUID: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
    }
    for scope, config, model in (
        (Scope.KNOWLEDGE, knowledge, KnowledgeConfiguration),
        (Scope.DOCUMENT, document, DocumentConfiguration),
    ):
        for spec in fields_for(scope):
            required = scope == Scope.KNOWLEDGE and spec.column == "chunk_method"
            source = spec.api_path.startswith("sources.")
            item = {
                "path": spec.path(scope),
                "scope": scope.name.lower(),
                "type": types[spec.py_type],
                "nullable": spec.nullable and not source,
                "required_on_create": required,
                "default": None
                if required
                else (spec.legacy_default if source else getattr(config, spec.column)),
                "description": spec.description,
            }
            if spec.allowed:
                item["enum"] = list(spec.allowed)
            for constraint in model.model_fields[spec.column].metadata:
                for attr, key in (("ge", "minimum"), ("le", "maximum"), ("gt", "minimum")):
                    value = getattr(constraint, attr, None)
                    if value is not None:
                        item[key] = value + 1 if attr == "gt" and spec.py_type is int else value
            fields.append(item)
    return {
        "knowledge_defaults": to_api(knowledge),
        "document_defaults": to_api(document),
        "workspace_models": {
            key: getattr(workspace, field, None) or None for key, field in WORKSPACE_MODELS.items()
        },
        "fields": fields,
        "constraints": CONSTRAINTS,
    }
