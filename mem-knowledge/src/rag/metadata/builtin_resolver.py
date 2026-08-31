"""Resolve builtin metadata names to Document columns."""

from .builtin_fields import BUILTIN_METADATA_FIELDS, BuiltinMetadataField


class BuiltinFieldResolver:
    _mapping: dict[str, BuiltinMetadataField] = {
        field.name: field for field in BUILTIN_METADATA_FIELDS
    }

    @classmethod
    def resolve(cls, field_name: str) -> BuiltinMetadataField | None:
        return cls._mapping.get(field_name)

    @classmethod
    def is_builtin(cls, field_name: str) -> bool:
        return field_name in cls._mapping

    @classmethod
    def get_all(cls) -> list[BuiltinMetadataField]:
        return list(BUILTIN_METADATA_FIELDS)
