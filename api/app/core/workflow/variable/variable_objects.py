from typing import Any, TypeVar, Type, Generic

from app.core.workflow.variable.base_variable import BaseVariable, VariableType

T = TypeVar("T", bound=BaseVariable)


class StringObject(BaseVariable):
    type = 'str'

    def valid_value(self, value) -> str:
        if not isinstance(value, str):
            raise TypeError("Value must be a string")
        return value

    def to_literal(self) -> str:
        return self.value


class NumberObject(BaseVariable):
    type = 'number'

    def valid_value(self, value) -> int | float:
        if not isinstance(value, (int, float)):
            raise TypeError("Value must be a number")
        return value

    def to_literal(self) -> str:
        return str(self.value)


class BooleanObject(BaseVariable):
    type = 'boolean'

    def valid_value(self, value) -> bool:
        if not isinstance(value, bool):
            raise TypeError("Value must be a boolean")
        return value

    def to_literal(self) -> str:
        return str(self.value).lower()


class DictObject(BaseVariable):
    type = 'object'

    def valid_value(self, value) -> dict:
        if not isinstance(value, dict):
            raise TypeError("Value must be a dict")
        return value

    def to_literal(self) -> str:
        return str(self.value)


class FileObject(BaseVariable):
    type = 'file'

    def valid_value(self, value) -> Any:
        pass

    def to_literal(self) -> str:
        pass


class ArrayObject(BaseVariable, Generic[T]):
    type = 'array'

    def __init__(self, child_type: Type[T], value: list[Any]):
        if not issubclass(child_type, BaseVariable):
            raise TypeError("child_type must be a subclass of BaseVariable")
        self.child_type = child_type
        super().__init__(value)

    def valid_value(self, value: list[Any]) -> list[T]:
        if not isinstance(value, list):
            raise TypeError("Value must be a list")
        final_value = []
        for v in value:
            try:
                final_value.append(self.child_type(v))
            except:
                raise TypeError(f"All elements must be of type {self.child_type.type}")
        return final_value

    def to_literal(self) -> str:
        return "\n".join([v.to_literal() for v in self.value])


class NestedArrayObject(BaseVariable):
    type = 'array_nest'

    def valid_value(self, value: list[T]) -> list[T]:
        if not isinstance(value, list):
            raise TypeError("Value must be a list")
        final_value = []
        for v in value:
            if not isinstance(v, ArrayObject):
                raise TypeError("All elements must be of type list")
            final_value.append(v)
        return final_value

    def to_literal(self) -> str:
        return "\n".join(["\n".join([item.to_literal() for item in row]) for row in self.value])

    def get_value(self) -> Any:
        return [[item.get_value() for item in row] for row in self.value]


def make_array(child_type: Type[T], value: list[Any]) -> ArrayObject[T]:
    """简化 ArrayObject 创建，不需要重复写类型"""

    return ArrayObject(child_type, value)


def create_variable_instance(var_type: VariableType, value: Any) -> T:
    match var_type:
        case VariableType.STRING:
            return StringObject(value)
        case VariableType.NUMBER:
            return NumberObject(value)
        case VariableType.BOOLEAN:
            return BooleanObject(value)
        case VariableType.OBJECT:
            return DictObject(value)
        case VariableType.ARRAY_STRING:
            return make_array(StringObject, value)
        case VariableType.ARRAY_NUMBER:
            return make_array(NumberObject, value)
        case VariableType.ARRAY_BOOLEAN:
            return make_array(BooleanObject, value)
        case VariableType.ARRAY_OBJECT:
            return make_array(DictObject, value)
        case VariableType.ARRAY_FILE:
            return make_array(FileObject, value)
        case _:
            raise TypeError(f"Invalid type - {var_type}")
