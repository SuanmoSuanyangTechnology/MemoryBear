"""Response serializers that append localized enum display fields.

Port of the host ``app/i18n/serializers.py`` onto ``src.i18n.translate_enum``:
``serialize`` accepts a Pydantic model or a dict, adds ``{field}_display`` for
each declared enum field whose value is present, and leaves everything else
untouched. ``null``/absent fields produce no display key (host behavior).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from src.i18n import translate_enum


class I18nResponseMixin:
    """Add ``{field}_display`` translations for declared enum fields."""

    _enum_fields: tuple[tuple[str, str], ...] = ()

    def serialize(self, data: Any, locale: str = "zh") -> Any:
        return self.serialize_with_i18n(data, locale)

    def serialize_list(self, items: list[Any], locale: str = "zh") -> list[Any]:
        return [self.serialize(item, locale) for item in items]

    def serialize_with_i18n(self, data: Any, locale: str = "zh") -> Any:
        if isinstance(data, BaseModel):
            data = data.model_dump()
        if isinstance(data, dict):
            return self._serialize_dict(data, locale)
        if isinstance(data, list):
            return [
                self._serialize_dict(item, locale) if isinstance(item, dict) else item
                for item in data
            ]
        return data

    def _serialize_dict(self, data: dict[str, Any], locale: str) -> dict[str, Any]:
        result = dict(data)
        for field, enum_type in self._enum_fields:
            if field in result and result[field] is not None:
                result[f"{field}_display"] = translate_enum(
                    enum_type, str(result[field]), locale
                )
        return result


class WorkspaceSerializer(I18nResponseMixin):
    _enum_fields = (("role", "workspace_role"), ("status", "workspace_status"))


class WorkspaceMemberSerializer(I18nResponseMixin):
    _enum_fields = (("role", "workspace_role"),)


class WorkspaceInviteSerializer(I18nResponseMixin):
    _enum_fields = (("status", "invite_status"), ("role", "workspace_role"))


__all__ = [
    "I18nResponseMixin",
    "WorkspaceInviteSerializer",
    "WorkspaceMemberSerializer",
    "WorkspaceSerializer",
]
