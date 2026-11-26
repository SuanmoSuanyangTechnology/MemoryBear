"""End-user reads/writes the identity management plane needs.

D13 write exceptions: identity marks ``end_users.grace_period_until``
(retention grace) and creates ``end_users`` + ``end_user_info`` rows from the
API-key MCP get-or-create path; ``end_user_merge`` is read by that path.
Everything else in these tables stays owned elsewhere.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import update
from sqlalchemy.orm import Session

from src.models.end_user_model import EndUser, EndUserInfo, EndUserMerge
from src.models.user_model import User

logger = logging.getLogger(__name__)

_GRACE_PERIOD_UPDATE_BATCH_SIZE = 1000


def mark_grace_period_for_expired_users(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    retention_days: int,
    grace_period_until: datetime,
    reference_time: datetime,
) -> int:
    """Mark currently expired temporary identities with a grace deadline.

    Keyset pagination on ``(write_time, id)`` keeps each round bounded; the
    update re-checks the expiry predicate so a concurrent write that lands
    between the scan and the update is never marked.
    """

    cutoff_time = reference_time - timedelta(days=retention_days)
    affected_count = 0
    last_write_time: datetime | None = None
    last_end_user_id: uuid.UUID | None = None

    while True:
        stmt = sa.select(EndUser.id, EndUser.write_time).where(
            EndUser.workspace_id == workspace_id,
            EndUser.is_active == sa.true(),
            EndUser.identity_status == "temporary",
            EndUser.write_time.is_not(None),
            EndUser.write_time < cutoff_time,
        )
        if last_write_time is not None and last_end_user_id is not None:
            stmt = stmt.where(
                sa.tuple_(EndUser.write_time, EndUser.id)
                > sa.tuple_(last_write_time, last_end_user_id)
            )

        candidates = db.execute(
            stmt.order_by(EndUser.write_time.asc(), EndUser.id.asc()).limit(
                _GRACE_PERIOD_UPDATE_BATCH_SIZE
            )
        ).all()
        if not candidates:
            break

        candidate_ids = [candidate.id for candidate in candidates]
        result = db.execute(
            update(EndUser)
            .where(
                EndUser.id.in_(candidate_ids),
                EndUser.workspace_id == workspace_id,
                EndUser.is_active == sa.true(),
                EndUser.identity_status == "temporary",
                EndUser.write_time.is_not(None),
                EndUser.write_time < cutoff_time,
            )
            .values(grace_period_until=grace_period_until)
        )
        affected_count += max(result.rowcount or 0, 0)
        last_write_time = candidates[-1].write_time
        last_end_user_id = candidates[-1].id

        if len(candidates) < _GRACE_PERIOD_UPDATE_BATCH_SIZE:
            break

    return affected_count


def get_temporary_end_users_count_by_workspace(
    db: Session, workspace_id: uuid.UUID
) -> int:
    """有效临时 EndUser 中至少有一条记忆的数量（host 同口径）。"""

    try:
        return db.execute(
            sa.select(sa.func.count())
            .select_from(EndUser)
            .where(
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                EndUser.identity_status == "temporary",
                EndUser.memory_count >= 1,
            )
        ).scalar_one()
    except Exception as exc:
        db.rollback()
        logger.error(
            "查询工作空间 %s 下有记忆的有效临时终端用户时出错: %s",
            workspace_id,
            exc,
        )
        raise


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


def get_or_create_end_user_mcp(
    db: Session, workspace_id: uuid.UUID, user_id: str
) -> tuple[uuid.UUID, str | None]:
    """MCP get-or-create for end users (D13 exception, advisory lock inside).

    UUID input also denotes an MCP runtime user: a live platform user with the
    same id is rejected. Lookup order matches the host: active end user by
    id/other_id, then merge routing, then a fresh EndUser + EndUserInfo.
    Commit/rollback timing mirrors the host exactly.
    """

    try:
        db.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{workspace_id}|{user_id}"},
        )

        if is_uuid(user_id):
            platform_user_stmt = sa.select(User.id).where(
                User.is_active.is_(True),
                User.id == user_id,
            )
            if db.scalar(platform_user_stmt):
                raise RuntimeError("不可对草稿运行用户创建mcp user")

            end_user_stmt = (
                sa.select(EndUser)
                .where(
                    EndUser.is_active.is_(True),
                    EndUser.workspace_id == workspace_id,
                    sa.or_(EndUser.id == user_id, EndUser.other_id == user_id),
                )
                .order_by(EndUser.created_at.asc())
                .limit(1)
            )
        else:
            end_user_stmt = (
                sa.select(EndUser)
                .where(
                    EndUser.is_active.is_(True),
                    EndUser.workspace_id == workspace_id,
                    EndUser.other_id == user_id,
                )
                .order_by(EndUser.created_at.asc())
                .limit(1)
            )

        existing: EndUser | None = db.scalar(end_user_stmt)
        if existing:
            return existing.id, existing.other_id

        merge_filters = [EndUserMerge.workspace_id == workspace_id]
        if is_uuid(user_id):
            merge_filters.append(
                sa.or_(
                    EndUserMerge.origin_id == user_id,
                    EndUserMerge.origin_other_id == user_id,
                )
            )
        else:
            merge_filters.append(EndUserMerge.origin_other_id == user_id)
        merge_record = (
            db.query(EndUserMerge)
            .filter(*merge_filters)
            .order_by(EndUserMerge.id.desc())
            .first()
        )
        if merge_record:
            target_user = (
                db.query(EndUser)
                .filter(
                    EndUser.id == merge_record.target_id,
                    EndUser.workspace_id == workspace_id,
                    EndUser.is_active.is_(True),
                )
                .first()
            )
            if target_user:
                logger.info(
                    "MCP 合并路由: user_id=%s → target=%s", user_id, target_user.id
                )
                return target_user.id, target_user.other_id

        end_user = EndUser(workspace_id=workspace_id, other_id=user_id)
        db.add(end_user)
        db.flush()

        end_user_info = EndUserInfo(
            end_user_id=end_user.id,
            other_name="",
            aliases=[],
            meta_data={},
        )
        db.add(end_user_info)

        db.commit()
        db.refresh(end_user)
        return end_user.id, end_user.other_id
    except Exception as exc:
        db.rollback()
        logger.error("获取或创建终端用户出错: %s", exc)
        raise


__all__ = [
    "get_or_create_end_user_mcp",
    "get_temporary_end_users_count_by_workspace",
    "is_uuid",
    "mark_grace_period_for_expired_users",
]
