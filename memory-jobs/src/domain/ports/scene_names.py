"""External lookups the domain needs.

``SceneNameLookup`` maps scene-type ids to their display names for one tenant.
The enterprise edition supplies it; the open build passes ``None``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from src.domain.shared.ids import TenantId


class SceneNameLookup(Protocol):
    def __call__(
        self, scene_type_ids: list[UUID], tenant_id: TenantId
    ) -> Mapping[str, str]: ...
