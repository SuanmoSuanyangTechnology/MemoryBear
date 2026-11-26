"""Transport schemas for the two end-user callbacks.

Both shapes mirror what the application service already sends to the monolith:

* ``DomainInfoBody`` — ``POST /end-user/domain-profile``: one end user, no options;
* ``InfoUpdateBody`` — ``POST /end-user/info-update``: the host
  ``EndUserInfoUpdate``, whose only writable fields are the display name and the
  alias list.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DomainInfoBody(BaseModel):
    """Body of ``POST /internal/v1/end-user/domain-info``."""

    end_user_id: str


class InfoUpdateBody(BaseModel):
    """Body of ``POST /internal/v1/end-user/info/update``.

    ``meta_data`` stays accepted for wire compatibility but is never written: the
    extraction pipeline owns that column, and echoing the display projection back
    would clear it.
    """

    end_user_id: str
    other_name: str | None = None
    aliases: list[Any] | None = None
    meta_data: dict[str, Any] | None = Field(default=None)


__all__ = ["DomainInfoBody", "InfoUpdateBody"]
