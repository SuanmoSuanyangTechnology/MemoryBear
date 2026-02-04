from enum import StrEnum
from abc import abstractmethod, ABC
from typing import Any


class VariableType(StrEnum):
    """Enumeration of supported variable types in the workflow."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    FILE = "file"

    ARRAY_STRING = "array[string]"
    ARRAY_NUMBER = "array[number]"
    ARRAY_BOOLEAN = "array[boolean]"
    ARRAY_OBJECT = "array[object]"
    ARRAY_FILE = "array[file]"

    NESTED_ARRAY = "array_nest"

    @classmethod
    def type_map(cls, var: Any) -> "VariableType":
        """Maps a Python value to a corresponding VariableType.

        Args:
            var: The Python value to map.

        Returns:
            The VariableType corresponding to the input value.

        Raises:
            TypeError: If the type of the input value is not supported.
        """
        var_type = type(var)
        if isinstance(var_type, str):
            return cls.STRING
        elif isinstance(var_type, (int, float)):
            return cls.NUMBER
        elif isinstance(var_type, bool):
            return cls.BOOLEAN
        elif isinstance(var_type, FileObj):
            return cls.FILE
        elif isinstance(var_type, dict):
            return cls.OBJECT
        elif isinstance(var_type, list):
            if len(var) == 0:
                return cls.ARRAY_STRING
            else:
                child_type = type(var[0])
                if child_type == str:
                    return cls.ARRAY_STRING
                elif child_type == int or child_type == float:
                    return cls.ARRAY_NUMBER
                elif child_type == bool:
                    return cls.ARRAY_BOOLEAN
                elif child_type == dict:
                    return cls.ARRAY_OBJECT
                elif child_type == list:
                    return cls.NESTED_ARRAY
                else:
                    raise TypeError(f"Unsupported array child type - {child_type}")
        raise TypeError(f"Unsupported type - {var_type}")


def DEFAULT_VALUE(var_type: VariableType) -> Any:
    """Returns the default value for a given VariableType.

    Args:
        var_type: The variable type for which to get the default value.

    Returns:
        The default Python value corresponding to the VariableType.

    Raises:
        TypeError: If the VariableType is invalid.
    """
    match var_type:
        case VariableType.STRING:
            return ""
        case VariableType.NUMBER:
            return 0
        case VariableType.BOOLEAN:
            return False
        case VariableType.OBJECT:
            return {}
        case VariableType.FILE:
            return None
        case VariableType.ARRAY_STRING:
            return []
        case VariableType.ARRAY_NUMBER:
            return []
        case VariableType.ARRAY_BOOLEAN:
            return []
        case VariableType.ARRAY_OBJECT:
            return []
        case VariableType.ARRAY_FILE:
            return []
        case _:
            raise TypeError(f"Invalid type - {type}")


class FileObj:
    pass


class BaseVariable(ABC):
    """Abstract base class for all workflow variables.

   Subclasses must implement validation and serialization methods.
   """
    type = None

    def __init__(self, value: Any):
        """Initializes a variable instance.

        Args:
            value: The initial value for the variable.

        Attributes:
            self.value: The validated value stored in the variable.
            self.literal: A string representation of the variable.
        """
        self.value = self.valid_value(value)
        self.literal = self.to_literal()

    @abstractmethod
    def valid_value(self, value) -> Any:
        """Validates or converts a value to the correct type for the variable.

        Args:
            value: The value to validate.

        Returns:
            The validated or converted value.

        Raises:
            TypeError: If the value is invalid.
        """
        pass

    @abstractmethod
    def to_literal(self) -> str:
        """Converts the variable value to a string literal representation.

        Returns:
            A string representing the variable's value.
        """
        pass

    def get_value(self) -> Any:
        """Returns the current value of the variable."""
        return self.value

    def set(self, value):
        """Sets the variable to a new value after validation.

        Args:
            value: The new value to assign to the variable.
        """
        self.value = self.valid_value(value)
