"""Build configuration columns from the registry and validate entity/ORM agreement."""

from collections.abc import Callable
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SQLUUID
from sqlalchemy.types import TypeEngine

from ..utils.datetime_utils import utcnow_naive
from .entities import DocumentConfiguration, KnowledgeConfiguration
from .fields import Scope, fields_for, validate_registry

SOURCE_TYPE_CHECK = (
    "type IS NOT NULL AND ((type = 'Folder' AND source IS NULL) OR "
    "(source IS NOT NULL AND ((type = 'General' AND source = 'space') OR "
    "(type = 'Web' AND source = 'website') OR "
    "(type = 'Third-party' AND source = 'third_party'))))"
)
INDEX_STRATEGY_CHECK = (
    "type IS NOT NULL AND ((type = 'Folder' AND index_strategy IS NULL) OR "
    "(type <> 'Folder' AND index_strategy IS NOT NULL "
    "AND index_strategy IN ('rag', 'wiki', 'rag_wiki')))"
)


def configuration_table(name: str, metadata: MetaData, scope: Scope) -> Table:
    knowledge = scope == Scope.KNOWLEDGE
    owner = "knowledge" if knowledge else "document"
    prefix = "kpc" if knowledge else "dpc"
    sql_types: dict[type, Callable[[], TypeEngine[Any]]] = {
        str: Text,
        int: Integer,
        float: Float,
        bool: Boolean,
        list: lambda: ARRAY(Text),
        UUID: lambda: SQLUUID(as_uuid=True),
    }
    columns: list[Column[Any]] = [
        Column(
            owner + "_id",
            SQLUUID(as_uuid=True),
            ForeignKey(
                "knowledges.id" if knowledge else "documents.id",
                ondelete="CASCADE",
                name=f"fk_{name}_owner",
            ),
            primary_key=True,
            comment="所属对象 ID；物理删除级联，软删除保留",
        )
    ]
    constraints = []
    for spec in fields_for(scope):
        if spec.owner:
            continue
        sql_type = (
            SmallInteger() if spec.column == "image_vision_mode" else sql_types[spec.py_type]()
        )
        columns.append(
            Column(
                spec.column,
                sql_type,
                nullable=spec.is_nullable(scope),
                comment=f"解析配置 {spec.path(scope)}",
            )
        )
        if spec.allowed:
            allowed = ", ".join(repr(item) for item in spec.allowed)
            constraints.append(
                CheckConstraint(f"{spec.column} IN ({allowed})", name=f"ck_{prefix}_{spec.column}")
            )
    checks = {
        "chunk_token_num_positive": "chunk_token_num > 0",
        "chunk_overlap_nonnegative": "chunk_overlap >= 0",
        "auto_keywords_nonnegative": "auto_keywords >= 0",
        "parent_tokens_positive": "parent_chunk_token_num > 0",
        "auto_questions_range": "auto_questions BETWEEN 0 AND 10",
        "parent_child_qa": "chunk_method IS DISTINCT FROM 'parent_child' OR auto_questions = 0",
        "extras": "jsonb_typeof(extras) = 'object'",
    }
    if knowledge:
        checks.update(
            {
                "chunk_method_fields": "chunk_method IS NULL OR auto_questions IS NOT NULL",
                "wiki_language": "char_length(wiki_language) BETWEEN 1 AND 64",
                "wiki_prompt": "char_length(wiki_extract_prompt) <= 12000",
                "other_platform_cleared": "(third_party_platform IS NOT DISTINCT FROM 'yuque' OR "
                "(yuque_user_id IS NULL AND yuque_token IS NULL)) AND "
                "(third_party_platform IS NOT DISTINCT FROM 'feishu' OR "
                "(feishu_app_id IS NULL AND feishu_app_secret IS NULL "
                "AND feishu_folder_token IS NULL))",
                "web_values": "(web_max_pages IS NULL OR web_max_pages >= 1) AND "
                "(web_delay_seconds IS NULL OR web_delay_seconds >= 0) AND "
                "(web_timeout_seconds IS NULL OR web_timeout_seconds > 0)",
            }
        )
    constraints.extend(
        CheckConstraint(expression, name=f"ck_{prefix}_{key}") for key, expression in checks.items()
    )
    columns.extend(
        [
            Column(
                "extras",
                JSONB,
                nullable=False,
                server_default=text("'{}'::jsonb"),
                comment="未登记历史键；只供旧响应回显",
            ),
            Column(
                "created_at",
                DateTime,
                nullable=False,
                default=utcnow_naive,
                server_default=text("timezone('utc', now())"),
                comment="创建时间 UTC",
            ),
            Column(
                "updated_at",
                DateTime,
                nullable=False,
                default=utcnow_naive,
                onupdate=utcnow_naive,
                server_default=text("timezone('utc', now())"),
                comment="修改时间 UTC",
            ),
        ]
    )
    return Table(name, metadata, *columns, *constraints, comment="所属对象的一对一解析配置")


def validate_schema() -> None:
    # Called on model import, before any database connection or task execution.
    from ..models.owned import DocumentParserConfig, Knowledge, KnowledgeParserConfig

    validate_registry()
    for scope, entity, model in (
        (Scope.KNOWLEDGE, KnowledgeConfiguration, KnowledgeParserConfig),
        (Scope.DOCUMENT, DocumentConfiguration, DocumentParserConfig),
    ):
        table = model.__table__
        specs = fields_for(scope)
        expected = {spec.column for spec in specs if not spec.owner}
        owner_id = "knowledge_id" if scope == Scope.KNOWLEDGE else "document_id"
        if set(table.columns.keys()) != expected | {owner_id, "extras", "created_at", "updated_at"}:
            raise RuntimeError("parser configuration ORM columns differ from registry")
        entity_names = {spec.column for spec in specs} | {"extras"}
        if scope == Scope.KNOWLEDGE:
            entity_names |= {"knowledge_type", "source", "index_strategy"}
        if set(entity.model_fields) != entity_names:
            raise RuntimeError("parser configuration entity fields differ from registry")
        for spec in specs:
            column = (Knowledge.__table__ if spec.owner else table).c[spec.column]
            annotation = entity.model_fields[spec.column].annotation
            nullable = type(None) in get_args(annotation)
            if get_origin(annotation) in (Union, UnionType):
                annotation = next(item for item in get_args(annotation) if item is not type(None))
            allowed = get_args(annotation) if get_origin(annotation) is Literal else ()
            py_type = type(allowed[0]) if allowed else (get_origin(annotation) or annotation)
            if (
                column.nullable != spec.is_nullable(scope)
                or nullable != spec.is_nullable(scope)
                or column.type.python_type is not spec.py_type
                or py_type is not spec.py_type
                or tuple(allowed) != spec.allowed
            ):
                raise RuntimeError(f"parser configuration schema mismatch: {spec.column}")
