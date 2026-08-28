"""Builtin metadata definitions used by Knowledge interfaces."""

from .builtin_fields import BUILTIN_METADATA_FIELDS, BuiltinMetadataField
from .builtin_resolver import BuiltinFieldResolver

__all__ = [
    "BUILTIN_METADATA_FIELDS",
    "BuiltinFieldResolver",
    "BuiltinMetadataField",
]
