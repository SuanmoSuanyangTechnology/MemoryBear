from typing import Any, TypeVar, Type, Generic

from deprecated import deprecated

from app.core.workflow.variable.base_variable import BaseVariable, VariableType, FileObject

T = TypeVar("T", bound=BaseVariable)


class StringVariable(BaseVariable):
    type = 'str'

    def valid_value(self, value) -> str:
        if not isinstance(value, str):
            raise TypeError(f"Value must be a string - {type(value)}:{value}")
        return value

    def to_literal(self) -> str:
        return self.value


class NumberVariable(BaseVariable):
    type = 'number'

    def valid_value(self, value) -> int | float:
        if not isinstance(value, (int, float)):
            raise TypeError(f"Value must be a number - {type(value)}:{value}")
        return value

    def to_literal(self) -> str:
        return str(self.value)


class BooleanVariable(BaseVariable):
    type = 'boolean'

    def valid_value(self, value) -> bool:
        if not isinstance(value, bool):
            raise TypeError(f"Value must be a boolean - {type(value)}:{value}")
        return value

    def to_literal(self) -> str:
        return str(self.value).lower()


class DictVariable(BaseVariable):
    type = 'object'

    def valid_value(self, value) -> dict:
        if not isinstance(value, dict):
            raise TypeError(f"Value must be a dict  - {type(value)}:{value}")
        return value

    def to_literal(self) -> str:
        return str(self.value)


class FileVariable(BaseVariable):
    type = 'file'

    def valid_value(self, value) -> FileObject:

        if isinstance(value, dict):
            if not value.get("__file"):
                raise TypeError(f"Value must be a FileObject  - {type(value)}:{value}")
            return FileObject(
                **{
                    "type": str(value.get('type')),
                    "url": value.get('url'),
                    "__file": True
                }
            )
        if isinstance(value, FileObject):
            return value
        raise TypeError(f"Value must be a FileObject - {type(value)}:{value}")

    def to_literal(self) -> str:
        return str(self.value.model_dump())

    def get_value(self) -> Any:
        return self.value.model_dump()


class ArrayObject(BaseVariable, Generic[T]):
    type = 'array'

    def __init__(self, child_type: Type[T], value: list[Any]):
        if not issubclass(child_type, BaseVariable):
            raise TypeError("child_type must be a subclass of BaseVariable")
        self.child_type = child_type
        super().__init__(value)

    def valid_value(self, value: list[Any]) -> list[T]:
        if not isinstance(value, list):
            raise TypeError(f"Value must be a list - {type(value)}:{value}")
        final_value = []
        for v in value:
            try:
                final_value.append(self.child_type(v))
            except:
                raise TypeError(f"All elements must be of type {self.child_type.type}")
        return final_value

    def to_literal(self) -> str:
        return "\n".join([v.to_literal() for v in self.value])

    def get_value(self) -> Any:
        return [v.get_value() for v in self.value]


class NestedArrayObject(BaseVariable):
    type = 'array_nest'

    def valid_value(self, value: list[T]) -> list[T]:
        if not isinstance(value, list):
            raise TypeError(f"Value must be a list - {type(value)}:{value}")
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


@deprecated(
    reason="Using arbitrary-type values may cause unexpected errors; please switch to strongly-typed values.",
    category=RuntimeWarning
)
class AnyObject(BaseVariable):
    type = 'any'

    def valid_value(self, value: Any) -> Any:
        return value

    def to_literal(self) -> str:
        return str(self.value)


def make_array(child_type: Type[T], value: list[Any]) -> ArrayObject[T]:
    """简化 ArrayObject 创建，不需要重复写类型"""

    return ArrayObject(child_type, value)


def create_variable_instance(var_type: VariableType, value: Any) -> T:
    match var_type:
        case VariableType.STRING:
            return StringVariable(value)
        case VariableType.NUMBER:
            return NumberVariable(value)
        case VariableType.BOOLEAN:
            return BooleanVariable(value)
        case VariableType.OBJECT:
            return DictVariable(value)
        case VariableType.ARRAY_STRING:
            return make_array(StringVariable, value)
        case VariableType.ARRAY_NUMBER:
            return make_array(NumberVariable, value)
        case VariableType.ARRAY_BOOLEAN:
            return make_array(BooleanVariable, value)
        case VariableType.ARRAY_OBJECT:
            return make_array(DictVariable, value)
        case VariableType.ARRAY_FILE:
            return make_array(FileVariable, value)
        case VariableType.ANY:
            return AnyObject(value)
        case _:
            raise TypeError(f"Invalid type - {var_type}")
