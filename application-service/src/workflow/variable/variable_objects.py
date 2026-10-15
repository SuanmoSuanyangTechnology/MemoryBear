from typing import Any, TypeVar, Type, Generic

import httpx
from deprecated import deprecated

from .base_variable import BaseVariable, VariableType, FileObject, FileType
from ...config import current_settings

T = TypeVar("T", bound=BaseVariable)


class StringVariable(BaseVariable):
    value: str
    type = 'str'

    def valid_value(self, value) -> str:
        if not isinstance(value, str):
            raise TypeError(f"Value must be a string - {type(value)}:{value}")
        return value

    def to_literal(self) -> str:
        return self.value


class NumberVariable(BaseVariable):
    value: int | float
    type = 'number'

    def valid_value(self, value) -> int | float:
        if not isinstance(value, (int, float)):
            raise TypeError(f"Value must be a number - {type(value)}:{value}")
        return value

    def to_literal(self) -> str:
        return str(self.value)


class BooleanVariable(BaseVariable):
    value: bool
    type = 'boolean'

    def valid_value(self, value) -> bool:
        if not isinstance(value, bool):
            raise TypeError(f"Value must be a boolean - {type(value)}:{value}")
        return value

    def to_literal(self) -> str:
        return str(self.value).lower()


class DictVariable(BaseVariable):
    value: dict
    type = 'object'

    def valid_value(self, value) -> dict:
        if not isinstance(value, dict):
            raise TypeError(f"Value must be a dict - {type(value)}:{value}")
        return value

    def to_literal(self) -> str:
        return str(self.value)


class FileVariable(BaseVariable):
    value: FileObject | None
    type = 'file'

    def valid_value(self, value) -> FileObject | None:
        if value is None:
            return None
        if isinstance(value, dict):
            # Field mapping: frontend format -> FileObject format
            mapped = dict(value)
            
            # 1. file_id mapping: prefer file_id, then upload_file_id, then uid; else generate a temp ID
            if "file_id" not in mapped:
                if "upload_file_id" in mapped:
                    mapped["file_id"] = mapped["upload_file_id"]
                elif "uid" in mapped:
                    mapped["file_id"] = mapped["uid"]
                else:
                    # Remote URL files have no file_id; generate a temporary one
                    import uuid
                    mapped["file_id"] = str(uuid.uuid4())
            
            # 2. type: MIME type -> enum value
            file_type = mapped.get("type")
            if file_type and file_type not in ["image", "document", "audio", "video"]:
                # Infer the file type from the MIME type
                if file_type.startswith("image/"):
                    mapped["type"] = "image"
                elif file_type.startswith("audio/"):
                    mapped["type"] = "audio"
                elif file_type.startswith("video/"):
                    mapped["type"] = "video"
                else:
                    mapped["type"] = "document"
            
            # 3. Fill in missing fields
            mapped.setdefault("is_file", True)
            mapped.setdefault("url", "")
            mapped.setdefault("origin_file_type", mapped.get("type", "document"))
            mapped.setdefault("transfer_method", "local_file" if mapped.get("upload_file_id") else "remote_url")
            
            try:
                return FileObject(**mapped)
            except Exception as e:
                raise TypeError(f"Failed to create FileObject from {value}: {e}")
        
        if isinstance(value, FileObject):
            return value
        raise TypeError(f"Value must be a FileObject - {type(value)}:{value}")

    def to_literal(self) -> str:
        if self.value is None:
            return ""
        return f'{"!"if self.value.type == FileType.IMAGE else ""}[file]({self.value.url})'

    def get_value(self) -> Any:
        if self.value is None:
            return None
        return self.value.model_dump(exclude={"content_cache"})

    async def get_content(self):
        total_bytes = 0
        chunks = []

        async with httpx.AsyncClient(follow_redirects=True) as client:
            async with client.stream("GET", self.value.url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(8192):
                    total_bytes += len(chunk)
                    if total_bytes > current_settings().max_file_size:
                        raise ValueError(f"File too large: {total_bytes} bytes")
                    chunks.append(chunk)

        return b"".join(chunks)


class ArrayVariable(BaseVariable, Generic[T]):
    value: list[T]
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
            except Exception as e:
                raise TypeError(
                    f"All elements must be of type {self.child_type.type}: "
                    f"element={type(v).__name__}:{v!r}, error={e}"
                )
        return final_value

    def to_literal(self) -> str:
        return "\n".join([v.to_literal() for v in self.value])

    def get_value(self) -> Any:
        return [v.get_value() for v in self.value]


class NestedArrayVariable(BaseVariable):
    value: list[ArrayVariable]
    type = 'array_nest'

    def valid_value(self, value: list[T]) -> list[T]:
        if not isinstance(value, list):
            raise TypeError(f"Value must be a list - {type(value)}:{value}")
        final_value = []
        for v in value:
            if not isinstance(v, list):
                raise TypeError("All elements must be of type list")
            final_value.append(make_array(AnyVariable, v))
        return final_value

    def to_literal(self) -> str:
        return "\n".join(["\n".join([str(item) for item in row.get_value()]) for row in self.value])

    def get_value(self) -> Any:
        return [[item for item in row.get_value()] for row in self.value]


@deprecated(
    reason="Using arbitrary-type values may cause unexpected errors; please switch to strongly-typed values.",
    category=RuntimeWarning
)
class AnyVariable(BaseVariable):
    value: Any
    type = 'any'

    def valid_value(self, value: Any) -> Any:
        return value

    def to_literal(self) -> str:
        return str(self.value)


def make_array(child_type: Type[T], value: list[Any]) -> ArrayVariable[T]:
    """Convenience helper for creating an ArrayVariable without repeating the type."""

    return ArrayVariable(child_type, value)


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
        case VariableType.FILE:
            return FileVariable(value)
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
        case VariableType.NESTED_ARRAY:
            return NestedArrayVariable(value)
        case VariableType.ANY:
            return AnyVariable(value)
        case _:
            raise TypeError(f"Invalid type - {var_type}")
