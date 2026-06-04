from .builtin_fields import BUILTIN_METADATA_FIELDS, BuiltinMetadataField


class BuiltinFieldResolver:
    """内置元数据字段解析器：将字段名映射到 Document 表真实列"""

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
