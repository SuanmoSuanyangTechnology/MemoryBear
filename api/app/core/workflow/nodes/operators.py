from abc import ABC
from typing import Union, Type

from app.core.workflow.nodes.enums import ComparisonOperator
from app.core.workflow.variable_pool import VariablePool


class OperatorBase(ABC):
    def __init__(self, pool: VariablePool, left_selector, right):
        self.pool = pool
        self.left_selector = left_selector
        self.right = right

        self.type_limit: type[str, int, dict, list] = None

    def check(self, no_right=False):
        left = self.pool.get(self.left_selector)
        if not isinstance(left, self.type_limit):
            raise TypeError(f"The variable to be operated on must be of {self.type_limit} type")

        if not no_right and not isinstance(self.right, self.type_limit):
            raise TypeError(f"The value assigned to the string variable must also be of {self.type_limit} type")


class StringOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = str

    def assign(self) -> None:
        self.check()
        self.pool.set(self.left_selector, self.right)

    def clear(self) -> None:
        self.check(no_right=True)
        self.pool.set(self.left_selector, '')


class NumberOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = (float, int)

    def assign(self) -> None:
        self.check()
        self.pool.set(self.left_selector, self.right)

    def clear(self) -> None:
        self.check(no_right=True)
        self.pool.set(self.left_selector, 0)

    def add(self) -> None:
        self.check()
        origin = self.pool.get(self.left_selector)
        self.pool.set(self.left_selector, origin + self.right)

    def subtract(self) -> None:
        self.check()
        origin = self.pool.get(self.left_selector)
        self.pool.set(self.left_selector, origin - self.right)

    def multiply(self) -> None:
        self.check()
        origin = self.pool.get(self.left_selector)
        self.pool.set(self.left_selector, origin * self.right)

    def divide(self) -> None:
        self.check()
        origin = self.pool.get(self.left_selector)
        self.pool.set(self.left_selector, origin / self.right)


class BooleanOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = bool

    def assign(self) -> None:
        self.check()
        self.pool.set(self.left_selector, self.right)

    def clear(self) -> None:
        self.check(no_right=True)
        self.pool.set(self.left_selector, False)


class ArrayOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = list

    def assign(self) -> None:
        self.check()
        self.pool.set(self.left_selector, self.right)

    def clear(self) -> None:
        self.check(no_right=True)
        self.pool.set(self.left_selector, list())

    def append(self) -> None:
        self.check(no_right=True)
        # TODO：require type limit in list
        origin = self.pool.get(self.left_selector)
        origin.append(self.right)
        self.pool.set(self.left_selector, origin)

    def extend(self) -> None:
        self.check(no_right=True)
        origin = self.pool.get(self.left_selector)
        origin.extend(self.right)
        self.pool.set(self.left_selector, origin)

    def remove_last(self) -> None:
        self.check(no_right=True)
        origin = self.pool.get(self.left_selector)
        origin.pop()
        self.pool.set(self.left_selector, origin)

    def remove_first(self) -> None:
        self.check(no_right=True)
        origin = self.pool.get(self.left_selector)
        origin.pop(0)
        self.pool.set(self.left_selector, origin)


class ObjectOperator(OperatorBase):
    def __init__(self, pool: VariablePool, left_selector, right):
        super().__init__(pool, left_selector, right)
        self.type_limit = object

    def assign(self) -> None:
        self.check()
        self.pool.set(self.left_selector, self.right)

    def clear(self) -> None:
        self.check(no_right=True)
        self.pool.set(self.left_selector, dict())


class AssignmentOperatorResolver:
    @classmethod
    def resolve_by_value(cls, value):
        if isinstance(value, str):
            return StringOperator
        elif isinstance(value, bool):
            return BooleanOperator
        elif isinstance(value, (int, float)):
            return NumberOperator
        elif isinstance(value, list):
            return ArrayOperator
        elif isinstance(value, dict):
            return ObjectOperator
        else:
            raise TypeError(f"Unsupported variable type: {type(value)}")


AssignmentOperatorInstance = Union[
    StringOperator,
    NumberOperator,
    BooleanOperator,
    ArrayOperator,
    ObjectOperator
]
AssignmentOperatorType = Type[AssignmentOperatorInstance]


class ConditionExpressionBuilder:
    """
    Build a Python boolean expression string based on a comparison operator.

    This class does not evaluate the expression.
    It only generates a valid Python expression string
    that can be evaluated later in a workflow context.
    """

    def __init__(self, left: str, operator: ComparisonOperator, right: str):
        self.left = left
        self.operator = operator
        self.right = right

    def _empty(self):
        return f"{self.left} == ''"

    def _not_empty(self):
        return f"{self.left} != ''"

    def _contains(self):
        return f"{self.right} in {self.left}"

    def _not_contains(self):
        return f"{self.right} not in {self.left}"

    def _startswith(self):
        return f'{self.left}.startswith({self.right})'

    def _endswith(self):
        return f'{self.left}.endswith({self.right})'

    def _eq(self):
        return f"{self.left} == {self.right}"

    def _ne(self):
        return f"{self.left} != {self.right}"

    def _lt(self):
        return f"{self.left} < {self.right}"

    def _le(self):
        return f"{self.left} <= {self.right}"

    def _gt(self):
        return f"{self.left} > {self.right}"

    def _ge(self):
        return f"{self.left} >= {self.right}"

    def build(self):
        match self.operator:
            case ComparisonOperator.EMPTY:
                return self._empty()
            case ComparisonOperator.NOT_EMPTY:
                return self._not_empty()
            case ComparisonOperator.CONTAINS:
                return self._contains()
            case ComparisonOperator.NOT_CONTAINS:
                return self._not_contains()
            case ComparisonOperator.START_WITH:
                return self._startswith()
            case ComparisonOperator.END_WITH:
                return self._endswith()
            case ComparisonOperator.EQ:
                return self._eq()
            case ComparisonOperator.NE:
                return self._ne()
            case ComparisonOperator.LT:
                return self._lt()
            case ComparisonOperator.LE:
                return self._le()
            case ComparisonOperator.GT:
                return self._gt()
            case ComparisonOperator.GE:
                return self._ge()
            case _:
                raise ValueError(f"Invalid condition: {self.operator}")
