import uuid
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.models.document_model import Document
from .filter_strategies import StringFilterStrategy, NumberFilterStrategy, TimeFilterStrategy, _escape_like
from .builtin_resolver import BuiltinFieldResolver


class FilterCondition:
    """过滤条件（与 chunk_schema 中的定义保持一致）"""
    def __init__(self, field: str, operator: str, value: Any = None):
        self.field = field
        self.operator = operator
        self.value = value


class FilterGroup:
    """条件组"""
    def __init__(self, conditions: list[FilterCondition], logic: str = "AND"):
        self.conditions = conditions
        logic_upper = logic.upper()
        if logic_upper not in ("AND", "OR"):
            raise BusinessException(
                f"无效的组内逻辑: {logic}（仅支持 AND/OR）",
                code=BizCode.INVALID_PARAMETER,
            )
        self.logic = logic_upper


class MetadataFilterEngine:
    """元数据过滤引擎：将多条件过滤转化为 SQLAlchemy 查询"""

    def __init__(self, db: Session):
        self.db = db
        self._strategies = {
            "string": StringFilterStrategy(),
            "number": NumberFilterStrategy(),
            "time": TimeFilterStrategy(),
        }

    def build_query(
        self,
        knowledge_id: uuid.UUID,
        filter_groups: list[FilterGroup],
        metadata_defs: dict[str, dict],
    ):
        """
        构建过滤查询
        Args:
            knowledge_id: 知识库 ID
            filter_groups: 条件组列表
            metadata_defs: {field_name: {"type": "string", "is_builtin": False}} 字段定义
        Returns:
            SQLAlchemy Query 对象（未执行）
        """
        query = self.db.query(Document.id).filter(Document.kb_id == knowledge_id)

        group_conditions = []
        for group in filter_groups:
            conditions = []
            for cond in group.conditions:
                field_def = metadata_defs.get(cond.field)
                if not field_def:
                    raise BusinessException(
                        f"未知元数据字段: {cond.field}",
                        code=BizCode.METADATA_FIELD_NOT_FOUND,
                    )

                is_builtin = field_def.get("is_builtin", False)

                if is_builtin:
                    filter_expr = self._build_builtin_filter(cond, field_def)
                else:
                    strategy = self._strategies[field_def["type"]]
                    if not strategy.supports(cond.operator):
                        raise BusinessException(
                            f"字段 '{cond.field}' (类型 {field_def['type']}) "
                            f"不支持操作符 '{cond.operator}'",
                            code=BizCode.METADATA_INVALID_OPERATOR,
                        )
                    filter_expr = strategy.apply(cond.field, cond.operator, cond.value)

                conditions.append(filter_expr)

            if not conditions:
                continue

            if group.logic == "OR":
                group_conditions.append(or_(*conditions))
            else:
                group_conditions.append(and_(*conditions))

        if group_conditions:
            query = query.filter(and_(*group_conditions))

        return query

    def execute(self, *args, **kwargs) -> list[uuid.UUID]:
        """执行过滤，返回符合条件的 document_id 列表"""
        query = self.build_query(*args, **kwargs)
        return [row[0] for row in query.all()]

    def _build_builtin_filter(self, cond: FilterCondition, field_def: dict):
        """构建内置字段的过滤表达式（查真实列）"""
        builtin_field = BuiltinFieldResolver.resolve(cond.field)
        if not builtin_field:
            raise BusinessException(
                f"未知内置字段: {cond.field}",
                code=BizCode.METADATA_FIELD_NOT_FOUND,
            )

        column_name = builtin_field.mapping
        field_type = builtin_field.type

        return self._build_column_filter(column_name, field_type, cond.operator, cond.value)

    def _build_column_filter(self, column_name: str, field_type: str, operator: str, value: Any):
        """为真实列构建过滤表达式"""
        match field_type:
            case "string":
                return self._build_string_column_filter(column_name, operator, value)
            case "time":
                return self._build_time_column_filter(column_name, operator, value)
            case _:
                raise BusinessException(
                    f"不支持的内置字段类型: {field_type}",
                    code=BizCode.METADATA_INVALID_VALUE_TYPE,
                )

    def _build_string_column_filter(self, column_name: str, operator: str, value: Any):
        col = getattr(Document, column_name)
        match operator:
            case "eq":
                return col == str(value)
            case "ne":
                return col != str(value)
            case "contains":
                return col.like(f"%{_escape_like(value)}%", escape="\\")
            case "not_contains":
                return ~col.like(f"%{_escape_like(value)}%", escape="\\")
            case "starts_with":
                return col.like(f"{_escape_like(value)}%", escape="\\")
            case "ends_with":
                return col.like(f"%{_escape_like(value)}", escape="\\")
            case "is_empty":
                return or_(col.is_(None), col == "")
            case "not_empty":
                return and_(col.is_not(None), col != "")
            case "in":
                values = list(value) if hasattr(value, '__iter__') and not isinstance(value, str) else [value]
                return col.in_([str(v) for v in values])
            case "not_in":
                values = list(value) if hasattr(value, '__iter__') and not isinstance(value, str) else [value]
                return ~col.in_([str(v) for v in values])
        raise BusinessException(
            f"unsupported operator '{operator}' for string column",
            code=BizCode.METADATA_INVALID_OPERATOR,
        )

    def _build_time_column_filter(self, column_name: str, operator: str, value: Any):
        from sqlalchemy import func, text as sa_text
        col = getattr(Document, column_name)
        match operator:
            case "eq":
                # 分钟级比较：将列值截断到分钟后比较
                return func.date_trunc('minute', col) == sa_text(":val::timestamp").bindparams(val=str(value))
            case "before":
                return func.date_trunc('minute', col) < sa_text(":val::timestamp").bindparams(val=str(value))
            case "after":
                return func.date_trunc('minute', col) > sa_text(":val::timestamp").bindparams(val=str(value))
            case "is_empty":
                return col.is_(None)
            case "not_empty":
                return col.is_not(None)
        raise BusinessException(
            f"unsupported operator '{operator}' for time column",
            code=BizCode.METADATA_INVALID_OPERATOR,
        )
