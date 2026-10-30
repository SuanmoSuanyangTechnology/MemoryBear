"""External lookup the insight-input projection needs.

``SceneNameLookup`` maps scene-type ids to their display names for one tenant.
The enterprise edition supplies it; the open build wires an adapter that has no
service to call.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from src.domain.shared.ids import TenantId


class SceneNameLookup(Protocol):
    def resolve(
        self, scene_type_ids: Sequence[UUID], tenant_id: TenantId
    ) -> Mapping[str, str] | None:
        """Resolve scene ids to names for one tenant.

        The three outcomes are distinct on purpose:

        * a mapping — the authoritative answer; ids missing from it have no
          name (deleted, or never existed), so the caller may drop them;
        * ``{}`` — authoritative "none of these ids has a name";
        * ``None`` — the lookup is unavailable (transport failure, bad payload).
          Callers must not read that as "deleted": the projection is skipped and
          the stored keys are kept as they are.
        """
        ...
