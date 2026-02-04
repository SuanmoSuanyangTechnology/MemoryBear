import json
import re
from abc import ABC
from typing import Union, Type, NoReturn, Any

from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.nodes.enums import ValueInputType
from app.core.workflow.variable_pool import VariablePool


class TypeTransformer:
    @classmethod
    def _fail(cls, value, target) -> NoReturn:
        raise TypeError(f"Cannot convert {value!r} to {target} type")

    @classmethod
    def _json_load(cls, value, target):
        try:
            return json.loads(value)
        except Exception:
            cls._fail(value, target)

    @classmethod
    def transform(cls, variable_literal: str | bool, target_type: VariableType):
        match target_type:
            case VariableType.STRING:
                return str(variable_literal)

            case VariableType.NUMBER:
                for caster in (int, float):
                    try:
                        return caster(variable_literal)
                    except Exception:
                        pass
                cls._fail(variable_literal, target_type)

            case VariableType.BOOLEAN:
                if isinstance(variable_literal, bool):
                    return variable_literal
                cls._fail(variable_literal, target_type)

            case VariableType.OBJECT:
                obj = cls._json_load(variable_literal, target_type)
                if isinstance(obj, dict):
                    return obj
                cls._fail(variable_literal, target_type)

            case VariableType.ARRAY_BOOLEAN:
                return cls._parse_list(variable_literal, bool, target_type)

            case VariableType.ARRAY_NUMBER:
                return cls._parse_list(variable_literal, (int, float), target_type)

            case VariableType.ARRAY_STRING:
                return cls._parse_list(variable_literal, str, target_type)

            case VariableType.ARRAY_OBJECT:
                return cls._parse_list(variable_literal, dict, target_type)

            case _:
                raise TypeError("Invalid type")

    @classmethod
    def _parse_list(cls, value, item_type, target):
        arr = cls._json_load(value, target)
        if isinstance(arr, list) and all(isinstance(i, item_type) for i in arr):
            return arr
        cls._fail(value, target)


class OperatorBase(ABC):
    def __init__(self, pool: VariablePool, left_selector: str, right: Any):
        self.pool = pool
        self.left_selector = left_selector
        self.right = right

        self.type_limit: type[str, int, dict, list] = None

    def check(self, no_right=False):
        left = self.pool.get_value(self.left_selector)
        if not isinstance(left, self.type_limit):
            raise TypeError(f"The variable to be operated on must be of {self.type_limit} type")

        if not no_right and not isinstance(self.right, self.type_limit):
            raise TypeError(
                f"The value assigned must be of {self.type_limit} type"
            )


class StringOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = str

    async def assign(self) -> None:
        self.check()
        await self.pool.set(self.left_selector, self.right)

    async def clear(self) -> None:
        self.check(no_right=True)
        await self.pool.set(self.left_selector, '')


class NumberOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = (float, int)

    async def assign(self) -> None:
        self.check()
        await self.pool.set(self.left_selector, self.right)

    async def clear(self) -> None:
        self.check(no_right=True)
        await self.pool.set(self.left_selector, 0)

    async def add(self) -> None:
        self.check()
        origin = self.pool.get_value(self.left_selector)
        await self.pool.set(self.left_selector, origin + self.right)

    async def subtract(self) -> None:
        self.check()
        origin = self.pool.get_value(self.left_selector)
        await self.pool.set(self.left_selector, origin - self.right)

    async def multiply(self) -> None:
        self.check()
        origin = self.pool.get_value(self.left_selector)
        await self.pool.set(self.left_selector, origin * self.right)

    async def divide(self) -> None:
        self.check()
        origin = self.pool.get_value(self.left_selector)
        await self.pool.set(self.left_selector, origin / self.right)


class BooleanOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = bool

    async def assign(self) -> None:
        self.check()
        await self.pool.set(self.left_selector, self.right)

    async def clear(self) -> None:
        self.check(no_right=True)
        await self.pool.set(self.left_selector, False)


class ArrayOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = list

    async def assign(self) -> None:
        self.check()
        await self.pool.set(self.left_selector, self.right)

    async def clear(self) -> None:
        self.check(no_right=True)
        await self.pool.set(self.left_selector, list())

    async def append(self) -> None:
        self.check(no_right=True)
        origin = self.pool.get_value(self.left_selector)
        origin.append(self.right)
        await self.pool.set(self.left_selector, origin)

    async def extend(self) -> None:
        self.check(no_right=True)
        origin = self.pool.get_value(self.left_selector)
        origin.extend(self.right)
        await self.pool.set(self.left_selector, origin)

    async def remove_last(self) -> None:
        self.check(no_right=True)
        origin = self.pool.get_value(self.left_selector)
        origin.pop()
        await self.pool.set(self.left_selector, origin)

    async def remove_first(self) -> None:
        self.check(no_right=True)
        origin = self.pool.get_value(self.left_selector)
        origin.pop(0)
        await self.pool.set(self.left_selector, origin)


class ObjectOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = dict

    async def assign(self) -> None:
        self.check()
        await self.pool.set(self.left_selector, self.right)

    async def clear(self) -> None:
        self.check(no_right=True)
        await self.pool.set(self.left_selector, dict())


class AssignmentOperatorResolver:
    OPERATOR_MAP = {
        str: StringOperator,
        bool: BooleanOperator,
        int: NumberOperator,
        float: NumberOperator,
        list: ArrayOperator,
        dict: ObjectOperator,
    }

    @classmethod
    def resolve_by_value(cls, value):
        for t, op in cls.OPERATOR_MAP.items():
            if isinstance(value, t):
                return op
        raise TypeError(f"Unsupported variable type: {type(value)}")


AssignmentOperatorInstance = Union[
    StringOperator,
    NumberOperator,
    BooleanOperator,
    ArrayOperator,
    ObjectOperator
]
AssignmentOperatorType = Type[AssignmentOperatorInstance]


class ConditionBase(ABC):
    type_limit: type[str, int, dict, list] = None

    def __init__(
            self,
            pool: VariablePool,
            left_selector,
            right_selector: str,
            input_type: ValueInputType
    ):
        self.pool = pool
        self.left_selector = left_selector
        self.right_selector = right_selector
        self.input_type = input_type

        self.left_value = self.pool.get_value(self.left_selector)
        self.right_value = self.resolve_right_literal_value()

        self.type_limit = getattr(self, "type_limit", None)

    def resolve_right_literal_value(self):
        if self.input_type == ValueInputType.VARIABLE:
            pattern = r"\{\{\s*(.*?)\s*\}\}"
            right_expression = re.sub(pattern, r"\1", self.right_selector).strip()
            return self.pool.get_value(right_expression)
        elif self.input_type == ValueInputType.CONSTANT:
            return self.right_selector
        raise RuntimeError("Unsupported variable type")

    def check(self, no_right=False):
        if not isinstance(self.left_value, self.type_limit):
            raise TypeError(f"The variable to be compared on must be of {self.type_limit} type")
        if not no_right:
            right = self.resolve_right_literal_value()
            if not isinstance(right, self.type_limit):
                raise TypeError(
                    f"The compared variable must be of {self.type_limit} type"
                )


class StringComparisonOperator(ConditionBase):
    type_limit = str

    def __init__(self, pool: VariablePool, left_selector, right_selector, input_type):
        super().__init__(pool, left_selector, right_selector, input_type)

    def empty(self):
        self.check(no_right=True)
        return self.left_value == ""

    def not_empty(self):
        return not self.empty()

    def contains(self):
        self.check()
        return self.right_value in self.left_value

    def not_contains(self):
        return self.right_value not in self.left_value

    def startswith(self):
        self.check()
        return self.left_value.startswith(self.right_value)

    def endswith(self):
        return self.left_value.endswith(self.right_value)

    def eq(self):
        return self.left_value == self.right_value

    def ne(self):
        return self.left_value != self.right_value


class NumberComparisonOperator(ConditionBase):
    type_limit = (int, float)

    def __init__(self, pool: VariablePool, left_selector, right_selector, input_type):
        super().__init__(pool, left_selector, right_selector, input_type)

    def empty(self):
        return self.left_value == 0

    def not_empty(self):
        return self.left_value != 0

    def eq(self):
        return self.left_value == self.right_value

    def ne(self):
        return self.left_value != self.right_value

    def lt(self):
        return self.left_value < self.right_value

    def le(self):
        return self.left_value <= self.right_value

    def gt(self):
        return self.left_value > self.right_value

    def ge(self):
        return self.left_value >= self.right_value


class BooleanComparisonOperator(ConditionBase):
    type_limit = bool

    def __init__(self, pool: VariablePool, left_selector, right_selector, input_type):
        super().__init__(pool, left_selector, right_selector, input_type)

    def eq(self):
        return self.left_value == self.right_value

    def ne(self):
        return self.left_value != self.right_value


class ObjectComparisonOperator(ConditionBase):
    type_limit = dict

    def __init__(self, pool: VariablePool, left_selector, right_selector, input_type):
        super().__init__(pool, left_selector, right_selector, input_type)

    def eq(self):
        return self.left_value == self.right_value

    def ne(self):
        return self.left_value != self.right_value

    def empty(self):
        return not self.left_value

    def not_empty(self):
        return bool(self.left_value)


class ArrayComparisonOperator(ConditionBase):
    type_limit = list

    def __init__(self, pool: VariablePool, left_selector, right_selector, input_type):
        super().__init__(pool, left_selector, right_selector, input_type)

    def empty(self):
        return not self.left_value

    def not_empty(self):
        return bool(self.left_value)

    def contains(self):
        return self.right_value in self.left_value

    def not_contains(self):
        return self.right_value not in self.left_value


class NoneObjectComparisonOperator:
    def __init__(self, *arg, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: False


CompareOperatorInstance = Union[
    StringComparisonOperator,
    NumberComparisonOperator,
    BooleanComparisonOperator,
    ArrayComparisonOperator,
    ObjectComparisonOperator
]
CompareOperatorType = Type[CompareOperatorInstance]


class ConditionExpressionResolver:
    CONDITION_OPERATOR_MAP = {
        str: StringComparisonOperator,
        bool: BooleanComparisonOperator,
        int: NumberComparisonOperator,
        float: NumberComparisonOperator,
        list: ArrayComparisonOperator,
        dict: ObjectComparisonOperator,
        type(None): NoneObjectComparisonOperator
    }

    @classmethod
    def resolve_by_value(cls, value) -> CompareOperatorType:
        for t, op in cls.CONDITION_OPERATOR_MAP.items():
            if isinstance(value, t):
                return op
        raise TypeError(f"Unsupported variable type: {type(value)}")
