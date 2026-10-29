from typing import Protocol
from uuid import UUID

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.services.scene_community_graph_types import AuthorizedUser, bad_data, not_found


class UserLookup(Protocol):
    async def find_active_flag(self, workspace_id: UUID, user_id: UUID) -> bool | None: ...
    async def merge_targets(self, workspace_id: UUID, user_id: UUID) -> list[UUID]: ...


class AccessResolver:
    """Resolve users and every merge hop strictly inside the authorized workspace."""
    def __init__(self, lookup: UserLookup, max_hops: int = 8):
        self.lookup = lookup
        self.max_hops = max_hops

    async def resolve(self, workspace_id: UUID, requested_id: str) -> AuthorizedUser:
        try:
            current = UUID(requested_id)
        except (ValueError, TypeError, AttributeError):
            raise BusinessException("无效的用户ID格式", BizCode.INVALID_USER_ID) from None

        seen: set[UUID] = set()
        # Allow max_hops merge edges, then one final active-user lookup.
        for hop in range(self.max_hops + 1):
            if current in seen:
                raise bad_data()
            seen.add(current)
            active = await self.lookup.find_active_flag(workspace_id, current)
            if active is None:
                raise not_found()
            if active:
                return AuthorizedUser(workspace_id, current)
            targets = await self.lookup.merge_targets(workspace_id, current)
            if not targets:
                raise not_found()
            if len(set(targets)) != 1 or hop == self.max_hops:
                raise bad_data()
            current = targets[0]
        raise bad_data()
