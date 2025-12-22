from abc import ABC
from typing import Union, Type

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
        self.check()
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


AssignmentOperatorInstance = Union[
    StringOperator,
    NumberOperator,
    BooleanOperator,
    ArrayOperator,
    ObjectOperator
]
AssignmentOperatorType = Type[AssignmentOperatorInstance]
