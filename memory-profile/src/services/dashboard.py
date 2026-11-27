"""Dashboard read orchestration with one authenticated workspace boundary."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.common.errors import BizCode, ProfileError
from src.common.normalization import datetime_to_epoch_ms
from src.middleware.auth import MemoryPrincipal
from src.repositories.dashboard import DashboardRepository
from src.repositories.rag_memory import RAGMemoryRepository
from src.runtime import ProfileRuntime
from src.services.end_user import EndUserService
from src.services.statistics import StatisticsService
from src.services.workspace import WorkspaceService

logger = logging.getLogger(__name__)


def normalize_tags(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    result, seen = [], set()
    for value in values:
        if not isinstance(value, str):
            continue
        value = " ".join(value.split())
        if value and len(value) <= 12 and value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
        if len(result) >= 5:
            break
    return result


class DashboardService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.repo = DashboardRepository(runtime.postgres)
        self.rag = RAGMemoryRepository(runtime.elasticsearch)
        self.users = EndUserService.from_postgres(runtime.postgres)
        self.workspace = WorkspaceService.from_postgres(runtime.postgres)
        self.quota = runtime.quota
        self.neo4j = runtime.neo4j
        self.statistics = StatisticsService(runtime)

    async def _workspace(self, context: MemoryPrincipal) -> dict[str, Any]:
        row = await self.workspace.row(context.workspace_id)
        if row is None or not row.get("is_active") or row.get("tenant_id") != context.tenant_id:
            raise ProfileError(BizCode.WORKSPACE_ACCESS_DENIED, "工作空间不可访问")
        return row

    async def _user(self, context: MemoryPrincipal, value: str) -> UUID:
        try:
            user_id = UUID(value)
        except ValueError as exc:
            raise ProfileError(BizCode.INVALID_PARAMETER, "存在非法的 end_user_id") from exc
        if not await self.users.exists_in_workspace(user_id, context.workspace_id):
            raise ProfileError(BizCode.USER_NOT_FOUND, "终端用户不存在或不可访问")
        return user_id

    async def end_users(
        self,
        context: MemoryPrincipal,
        page: int,
        pagesize: int,
        keyword: str | None,
        label: str | None,
        workspace_id: UUID | None,
    ):
        if workspace_id is not None and workspace_id != context.workspace_id:
            raise ProfileError(BizCode.WORKSPACE_ACCESS_DENIED, "工作空间不可访问")
        pagesize = min(pagesize, 100)
        workspace = await self._workspace(context)
        is_rag = workspace.get("storage_type") == "rag"
        rag_counts = (
            await self.rag.counts(
                [str(uid) for uid in await self.users.active_end_user_ids(context.workspace_id)]
            )
            if is_rag
            else None
        )
        rows, total = await self.repo.end_users(
            context.workspace_id, page, pagesize, keyword, label, rag_counts
        )
        pagination = {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "hasnext": page * pagesize < total,
        }
        if not rows:
            return {"items": [], "page": pagination}
        try:
            config = await self.repo.workspace_config(context.workspace_id)
        except Exception:
            logger.warning("Dashboard memory config lookup failed", exc_info=True)
            config = {"memory_config_id": None, "memory_config_name": None}
        limits = await self.quota.effective_limits(context.tenant_id)
        ids = [str(row["id"]) for row in rows]
        active = {}
        if not is_rag:
            try:
                active = await self.repo.active_counts(self.neo4j, ids)
            except Exception:
                logger.warning("Dashboard active memory count lookup failed", exc_info=True)
        items = []
        for row in rows:
            uid = str(row["id"])
            status = row["identity_status"]
            user = {
                "id": uid,
                "other_name": row["other_name"],
                "label": "long" if status == "confirmed" else "short",
            }
            if row["other_name"]:
                user["other_id"] = row["other_id"]
            if status == "temporary":
                user["write_time"] = datetime_to_epoch_ms(row["write_time"])
                expires = (
                    row["write_time"] + timedelta(days=row["retention_days"])
                    if row["write_time"] is not None and row["retention_days"] is not None
                    else None
                )
                user["expire_time"] = datetime_to_epoch_ms(expires)
            elif status == "confirmed":
                user["identity_features"] = row["identity_features"]
            count = int(
                rag_counts.get(uid, 0) if rag_counts is not None else row["memory_count"] or 0
            )
            items.append(
                {
                    "end_user_id": uid,
                    "end_user": user,
                    "tags": normalize_tags(row["memory_tags"]),
                    "memory_num": {
                        "total": count,
                        "active_count": count if is_rag else active.get(uid, 0),
                        "memory_limit": limits["end_user_memory_limit"],
                    },
                    "memory_config": config,
                }
            )
        return {"items": items, "page": pagination}

    async def counts(self, context: MemoryPrincipal, ids: list[str] | None = None):
        workspace = await self._workspace(context)
        parsed = None
        if ids is not None:
            if not ids:
                raise ProfileError(
                    BizCode.MISSING_PARAMETER,
                    "end_user_ids 不能为空",
                    detail="end_user_ids is required",
                )
            if len(ids) > 200:
                raise ProfileError(BizCode.INVALID_PARAMETER, "end_user_ids 单次上限 200 个")
            try:
                parsed = [UUID(value) for value in ids]
            except ValueError as exc:
                raise ProfileError(
                    BizCode.INVALID_PARAMETER,
                    "存在非法的 end_user_id",
                    detail="invalid UUID in end_user_ids",
                ) from exc
        rows = await self.repo.memory_counts(context.workspace_id, parsed)
        if parsed is not None:
            found = {row["end_user_id"] for row in rows}
            invalid = [str(uid) for uid in parsed if str(uid) not in found]
            if invalid:
                raise ProfileError(
                    BizCode.INVALID_PARAMETER,
                    "存在不属于当前工作空间或不存在的 end_user_id",
                    detail=f"invalid end_user_ids: {invalid}",
                )
        if workspace.get("storage_type") == "rag":
            counts = await self.rag.counts([row["end_user_id"] for row in rows])
            for row in rows:
                row["memory_count"] = counts.get(row["end_user_id"], 0)
        return {"total": sum(row["memory_count"] for row in rows), "items": rows}

    async def total_memory(self, context: MemoryPrincipal, end_user_id: str | None):
        if end_user_id is not None:
            await self._user(context, end_user_id)
        counts = await self.counts(context, [end_user_id] if end_user_id is not None else None)
        return {
            "total_memory_count": counts["total"],
            "host_count": len(counts["items"]),
            "details": [
                {
                    "end_user_id": row["end_user_id"],
                    "count": row["memory_count"],
                    "name": row["other_name"],
                }
                for row in counts["items"]
            ],
        }

    async def increments(self, context: MemoryPrincipal, start_time: int, end_time: int):
        if start_time > end_time:
            raise ProfileError(
                BizCode.INVALID_PARAMETER,
                "start_time 不能大于 end_time",
                detail=f"start={start_time}, end={end_time}",
            )
        try:
            start = datetime.fromtimestamp(start_time / 1000, UTC).replace(tzinfo=None)
            end = datetime.fromtimestamp(end_time / 1000, UTC).replace(tzinfo=None)
        except (ValueError, OverflowError, OSError) as exc:
            raise ProfileError(BizCode.INVALID_PARAMETER, "时间戳无效") from exc
        workspace = await self._workspace(context)
        if workspace.get("storage_type") == "rag":
            ids = [str(uid) for uid in await self.users.active_end_user_ids(context.workspace_id)]
            return {"items": await self.rag.daily_totals(ids, start, end)}
        rows = await self.repo.increments(context.workspace_id, start, end)
        return {
            "items": [
                {
                    "date": int(
                        datetime.combine(row["date"], datetime.min.time(), tzinfo=UTC).timestamp()
                        * 1000
                    ),
                    "total_num": int(row["total_num"] or 0),
                }
                for row in rows
            ]
        }

    async def rag_content(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        page: int,
        pagesize: int,
        keyword: str | None,
        start_time: int | None,
        end_time: int | None,
        source: list[str] | None,
        order: str,
    ):
        uid = await self._user(context, end_user_id)
        if keyword and len(keyword) > 200:
            raise ProfileError(BizCode.INVALID_PARAMETER, "keyword 长度上限 200")
        if start_time is not None and end_time is not None and start_time > end_time:
            raise ProfileError(BizCode.INVALID_PARAMETER, "start_time 不能晚于 end_time")
        sources = [item for item in source or [] if item and item.strip()]
        if set(sources) - {"agent", "service_api", "mcp", "workflow", "legacy"}:
            raise ProfileError(BizCode.INVALID_PARAMETER, "source 取值非法")
        try:
            return await self.rag.messages(
                [str(uid)],
                page,
                pagesize,
                keyword=keyword,
                sources=sources,
                start=datetime.fromtimestamp(start_time / 1000, UTC).replace(tzinfo=None)
                if start_time is not None
                else None,
                end=datetime.fromtimestamp(end_time / 1000, UTC).replace(tzinfo=None)
                if end_time is not None
                else None,
                order=order,
            )
        except (ValueError, OverflowError, OSError) as exc:
            raise ProfileError(
                BizCode.INVALID_PARAMETER, "分页或时间参数超出限制", detail=str(exc)
            ) from exc

    async def summary(self, context: MemoryPrincipal, end_user_id: str):
        uid = await self._user(context, end_user_id)
        row = await self.repo.chunk_summary(context.workspace_id, uid)
        if not row:
            raise ProfileError(BizCode.USER_NOT_FOUND, "终端用户不存在或不可访问")
        return {
            "summary": row["user_summary"] or "",
            "tags": json.loads(row["rag_tags"]) if row["rag_tags"] else [],
            "personas": json.loads(row["rag_personas"]) if row["rag_personas"] else [],
            "generated": bool(row["user_summary"]),
        }

    async def insight(self, context: MemoryPrincipal, end_user_id: str):
        uid = await self._user(context, end_user_id)
        row = await self.repo.chunk_insight(context.workspace_id, uid)
        if not row:
            raise ProfileError(BizCode.USER_NOT_FOUND, "终端用户不存在或不可访问")
        return {
            "insight": row["memory_insight"] or "",
            "behavior_pattern": row["behavior_pattern"] or "",
            "key_findings": row["key_findings"] or "",
            "growth_trajectory": row["growth_trajectory"] or "",
            "generated": bool(row["memory_insight"]),
        }

    async def dashboard_data(
        self,
        context: MemoryPrincipal,
        end_user_id: str | None,
        start_date: int | None,
        end_date: int | None,
    ):
        workspace = await self._workspace(context)
        user_id = await self._user(context, end_user_id) if end_user_id is not None else None
        storage_type = workspace.get("storage_type") or "neo4j"
        # These parameters were accepted by the aggregate but did not affect its
        # current/yesterday counters. Do not reinterpret them as row filters.
        result = {
            "storage_type": storage_type,
            "memory_update": None,
            "memory_update_change": None,
            "neo4j_data": None,
            "rag_data": None,
            "workspace_statistics": None,
        }
        now = datetime.now(UTC).replace(tzinfo=None)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        ids = None
        if storage_type in ("neo4j", "rag"):
            metrics = {
                key: None
                for key in (
                    "total_memory",
                    "total_app",
                    "total_knowledge",
                    "total_api_call",
                    "total_memory_change",
                    "total_app_change",
                    "total_knowledge_change",
                    "total_api_call_change",
                )
            }
            try:
                if storage_type == "rag":
                    ids = [
                        str(uid)
                        for uid in await self.users.active_end_user_ids(context.workspace_id)
                    ]
                    # Legacy RAG aggregate is workspace-wide even when a user is
                    # selected. Validate the selected user, but retain that scope.
                    metrics["total_memory"] = await self.rag.total(ids)
                else:
                    metrics["total_memory"] = await self.repo.total_only(
                        context.workspace_id, user_id
                    )
            except Exception:
                logger.warning("dashboard total unavailable", exc_info=True)
            try:
                common = await self.repo.common_counters(context.workspace_id, today, now)
                metrics.update(
                    {key: common[key] for key in ("total_app", "total_knowledge", "total_api_call")}
                )
                metrics["total_app_change"] = _ratio(
                    common["app_change_today"], common["app_yesterday"]
                )
                metrics["total_knowledge_change"] = _ratio(
                    common["total_knowledge"], common["knowledge_yesterday"]
                )
                metrics["total_api_call_change"] = _ratio(
                    common["api_until_now"], common["api_yesterday"]
                )
            except Exception:
                logger.warning("dashboard common counters unavailable", exc_info=True)
            try:
                if storage_type == "rag":
                    yesterday = (
                        await self.rag.total(ids, end=today - timedelta(milliseconds=1))
                        if ids
                        else None
                    )
                    day_before = (
                        await self.rag.total(ids, end=today - timedelta(days=1, milliseconds=1))
                        if ids
                        else None
                    )
                else:
                    yesterday = await self.repo.last_memory_total(context.workspace_id, today)
                    rows = await self.repo.increments(
                        context.workspace_id,
                        today - timedelta(days=2),
                        today - timedelta(microseconds=1),
                    )
                    baselines = {row["date"]: int(row["total_num"] or 0) for row in rows}
                    day_before = baselines.get((today - timedelta(days=2)).date())
                metrics["total_memory_change"] = _ratio(metrics["total_memory"], yesterday)
                # Increment display requires a snapshot specifically from yesterday;
                # total-memory ratio accepts the latest earlier snapshot.
                update_baseline = (
                    yesterday
                    if storage_type == "rag"
                    else baselines.get((today - timedelta(days=1)).date())
                )
                if metrics["total_memory"] is not None and update_baseline is not None:
                    result["memory_update"] = metrics["total_memory"] - update_baseline
                    if day_before is not None:
                        result["memory_update_change"] = result["memory_update"] - (
                            update_baseline - day_before
                        )
            except Exception:
                logger.warning("dashboard memory baseline unavailable", exc_info=True)
            result["rag_data" if storage_type == "rag" else "neo4j_data"] = metrics
        try:
            result["workspace_statistics"] = (
                await self.statistics.workspace_statistics(context.workspace_id)
            ).model_dump(mode="json")
        except Exception:
            logger.warning("dashboard workspace statistics unavailable", exc_info=True)
        return result


def _ratio(current: int | None, previous: int | None) -> float | None:
    if current is None or not previous:
        return None
    return round((current - previous) / previous, 4)
