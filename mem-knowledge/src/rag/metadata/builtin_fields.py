"""Builtin metadata fields copied from the legacy RAG package."""

from pydantic import BaseModel


class BuiltinMetadataField(BaseModel):
    name: str
    type: str
    mapping: str


BUILTIN_METADATA_FIELDS: list[BuiltinMetadataField] = [
    BuiltinMetadataField(name="document_name", type="string", mapping="file_name"),
    BuiltinMetadataField(name="upload_date", type="time", mapping="created_at"),
    BuiltinMetadataField(
        name="last_update_date",
        type="time",
        mapping="updated_at",
    ),
]
