from typing import Any
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode

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
        logic_upper = str(logic).strip().upper()
        if logic_upper not in ("AND", "OR"):
            raise BusinessException(
                f"无效的组内逻辑: {logic}（仅支持 AND/OR）",
                code=BizCode.INVALID_PARAMETER,
            )
        self.logic = logic_upper
