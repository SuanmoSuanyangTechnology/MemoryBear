"""Permanent-memory management for the memory value-ranking feature."""

from __future__ import annotations

import logging
import uuid
from numbers import Number
from typing import Any, Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.quota_manager import get_end_user_memory_limit_async
from app.core.utils.datetime_utils import (
    convert_neo4j_datetime_to_python,
    to_timestamp_ms,
)
from app.db import get_async_db_context
from app.repositories.end_user_repository import (
    EndUserRepository,
    get_tenant_id_by_end_user_id_async,
)
from app.repositories.neo4j.cypher_queries import (
    PERMANENT_MEMORY_COUNT,
    PERMANENT_MEMORY_LIST,
    PERMANENT_MEMORY_UNMARK,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_value_ranking_schema import (
    PermanentMemoryItem,
    PermanentMemoryList,
    PermanentMemoryProperties,
    PermanentMemoryQuota,
    PermanentMemoryUnmarkResult,
)
from app.utils.redis_cache import invalidate_cache

logger = logging.getLogger(__name__)

PERMANENT_MEMORY_RATIO = 0.10


class PermanentMemoryError(Exception):
    """Base domain error."""


class PermanentMemoryNotFound(PermanentMemoryError):
    pass


class PermanentMemoryUnavailable(PermanentMemoryError):
    pass


def calculate_permanent_memory_limit(total_memory_limit: int) -> int:
    return int(total_memory_limit * PERMANENT_MEMORY_RATIO)


def allocate_permanent_slots(
    statement_nodes: Sequence[Any],
    *,
    used: int,
    limit: int,
) -> int:
    """Assign the available permanent-memory slots in input order."""
    remaining = max(limit - used, 0)
    assigned = 0
    for node in statement_nodes:
        if not bool(getattr(node, "is_permanent", False)):
            continue
        if remaining > 0:
            node.is_permanent = True
            remaining -= 1
            assigned += 1
        else:
            node.is_permanent = False
    return assigned


def disable_permanent_candidates(statement_nodes: Sequence[Any]) -> None:
    for node in statement_nodes:
        if bool(getattr(node, "is_permanent", False)):
            node.is_permanent = False


async def get_total_memory_limit(end_user_id: str) -> int:
    try:
        parsed_id = uuid.UUID(end_user_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise PermanentMemoryNotFound("end user not found") from exc

    async with get_async_db_context() as db:
        tenant_id = await get_tenant_id_by_end_user_id_async(db, parsed_id)
        if tenant_id is None:
            raise PermanentMemoryNotFound("end user not found")
        total_memory_limit = await get_end_user_memory_limit_async(db, tenant_id)

    if (
        isinstance(total_memory_limit, bool)
        or not isinstance(total_memory_limit, Number)
        or total_memory_limit <= 0
    ):
        raise PermanentMemoryUnavailable("end-user memory quota unavailable")
    normalized = int(total_memory_limit)
    if normalized <= 0:
        raise PermanentMemoryUnavailable("end-user memory quota unavailable")
    return normalized


async def assign_permanent_memory_slots(
    connector: Neo4jConnector,
    end_user_id: str,
    statement_nodes: Sequence[Any],
) -> int:
    """Read capacity state and assign final flags inside the serialized worker write."""
    candidates = [n for n in statement_nodes if bool(getattr(n, "is_permanent", False))]
    if not candidates:
        return 0
    total_memory_limit = await get_total_memory_limit(end_user_id)
    limit = calculate_permanent_memory_limit(total_memory_limit)
    count_rows = await connector.execute_query(PERMANENT_MEMORY_COUNT, end_user_id=end_user_id)
    used = int(count_rows[0]["used"]) if count_rows else 0
    return allocate_permanent_slots(
        statement_nodes,
        used=used,
        limit=limit,
    )


class MemoryValueRankingService:
    def __init__(
        self,
        db: AsyncSession,
        connector_factory: Callable[[], Neo4jConnector] | None = None,
    ) -> None:
        self.db = db
        self.connector_factory = connector_factory or Neo4jConnector

    async def _get_end_user(
        self,
        end_user_id: str | uuid.UUID,
        workspace_id: uuid.UUID,
    ):
        try:
            parsed_id = end_user_id if isinstance(end_user_id, uuid.UUID) else uuid.UUID(end_user_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise PermanentMemoryNotFound("end user not found") from exc
        end_user_repo = EndUserRepository(self.db)
        end_user = await end_user_repo.get_active_end_user_in_workspace_async(
            parsed_id,
            workspace_id,
        )
        if end_user is None:
            raise PermanentMemoryNotFound("end user not found")
        return end_user

    @staticmethod
    async def _count(connector: Neo4jConnector, end_user_id: str) -> int:
        rows = await connector.execute_query(PERMANENT_MEMORY_COUNT, end_user_id=end_user_id)
        return int(rows[0]["used"]) if rows else 0

    @staticmethod
    def _quota(total_memory_limit: int, used: int) -> PermanentMemoryQuota:
        permanent_limit = calculate_permanent_memory_limit(total_memory_limit)
        return PermanentMemoryQuota(
            total_memory_limit=total_memory_limit,
            permanent_limit=permanent_limit,
            used=used,
            remaining=max(permanent_limit - used, 0),
        )

    async def get_quota(
        self,
        end_user_id: str | uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> PermanentMemoryQuota:
        end_user = await self._get_end_user(end_user_id, workspace_id)
        connector = self.connector_factory()
        try:
            total_memory_limit = await get_total_memory_limit(str(end_user.id))
            used = await self._count(connector, str(end_user.id))
            return self._quota(total_memory_limit, used)
        except PermanentMemoryError:
            raise
        except Exception as exc:
            raise PermanentMemoryUnavailable("failed to load permanent-memory quota") from exc
        finally:
            await connector.close()

    async def list_permanent_memories(
        self,
        end_user_id: str | uuid.UUID,
        workspace_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> PermanentMemoryList:
        end_user = await self._get_end_user(end_user_id, workspace_id)
        connector = self.connector_factory()
        try:
            total_memory_limit = await get_total_memory_limit(str(end_user.id))
            used = await self._count(connector, str(end_user.id))
            rows = await connector.execute_query(
                PERMANENT_MEMORY_LIST,
                json_format=True,
                end_user_id=str(end_user.id),
                skip=(page - 1) * page_size,
                limit=page_size,
            )
            items = []
            for row in rows:
                properties = dict(row.get("properties") or {})
                properties["created_at"] = to_timestamp_ms(
                    convert_neo4j_datetime_to_python(properties.get("created_at"))
                )
                items.append(
                    PermanentMemoryItem(
                        id=str(row["id"]),
                        label="Statement",
                        properties=PermanentMemoryProperties(**properties),
                    )
                )
            return PermanentMemoryList(
                page={
                    "page": page,
                    "pagesize": page_size,
                    "total": used,
                    "hasnext": page * page_size < used,
                },
                quota=self._quota(total_memory_limit, used),
                items=items,
            )
        except PermanentMemoryError:
            raise
        except Exception as exc:
            raise PermanentMemoryUnavailable("failed to list permanent memories") from exc
        finally:
            await connector.close()

    async def unmark_permanent_memory(
        self,
        element_id: str,
        end_user_id: str | uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> PermanentMemoryUnmarkResult:
        end_user = await self._get_end_user(end_user_id, workspace_id)
        connector = self.connector_factory()
        try:
            total_memory_limit = await get_total_memory_limit(str(end_user.id))
            rows = await connector.execute_query(
                PERMANENT_MEMORY_UNMARK,
                element_id=element_id,
                end_user_id=str(end_user.id),
            )
            if not rows:
                raise PermanentMemoryNotFound("statement not found")
            used = await self._count(connector, str(end_user.id))
            try:
                await invalidate_cache(prefix=f"forget_candidates:{end_user.id}")
            except Exception:
                logger.exception(
                    "Failed to invalidate forgetting candidates after unmark: end_user_id=%s",
                    end_user.id,
                )
            return PermanentMemoryUnmarkResult(
                id=str(rows[0]["id"]),
                is_permanent=False,
                quota=self._quota(total_memory_limit, used),
            )
        except PermanentMemoryError:
            raise
        except Exception as exc:
            raise PermanentMemoryUnavailable("failed to unmark permanent memory") from exc
        finally:
            await connector.close()
