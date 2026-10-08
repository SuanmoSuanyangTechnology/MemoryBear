from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_logger
from app.core.memory.alerts import (
    END_USER_REBUILD_FAILED as _END_USER_REBUILD_FAILED,
    WORKER_CRASHED as _WORKER_CRASHED,
    report_reembed_job_completion_safely,
    report_reembed_job_failure_safely,
)
from app.core.memory.storage_services.reembedding_engine import job_state
from app.core.utils.datetime_utils import to_timestamp_ms, utcnow_naive
from app.db import get_db_context
from app.models.memory_reembed_job_model import (
    MemoryReembedJob,
    MemoryReembedJobUser,
    ReembedJobStatus,
    ReembedUserStatus,
)
from app.schemas.response_schema import PageMeta
from app.utils.redis_cache import invalidate_cache_sync, redis_cache

logger = get_logger(__name__)

REEMBED_TASK_NAME = "app.tasks.run_reembed_job"
REEMBED_USER_TASK_NAME = "app.tasks.do_reembed_end_user"
REEMBED_RECONCILE_TASK_NAME = "app.tasks.scan_reembed_jobs"
REEMBED_TASK_QUEUE = "memory_heavy_tasks"

#: 单个 end_user 的最大尝试次数（含首次）。超过即视为终态失败，不再重试。
REEMBED_MAX_ATTEMPTS = 3
#: 一个 `running` 行超过这么久没被续期，就认为持有它的 worker 已死，可以重新认领。
#: 子任务每页都会续期，所以正常运行的长时间任务不会被误判。
REEMBED_STALE_RUNNING_SECONDS = 15 * 60

_ACTIVE_STATUSES = (
    ReembedJobStatus.pending.value,
    ReembedJobStatus.running.value,
)


def is_user_terminal(
        status: str,
        attempts: int,
        *,
        stale_running: bool = False,
) -> bool:
    """Whether no further work will be attempted for one end_user row.

    Single policy for the two decisions that must agree:

    - **the job may only finalize once every row is terminal** — a ``failed`` row
      with retry budget left is *not* terminal, otherwise the retry that fixes
      the failure would never be dispatched and the failure would be permanent;
    - **the driver may only claim non-terminal rows** — so a crashed ``running``
      row must stay non-terminal (it still needs re-dispatch), while a fresh
      ``running`` row is simply someone else's in-flight work.

    ``stale_running`` only matters for a row that already spent its attempts:
    that combination means the worker died on its last try, so nothing will
    retry it. Callers that hold the row's Redis claim pass ``False`` — they are
    the ones writing it.
    """
    if status == ReembedUserStatus.done.value:
        return True
    if status == ReembedUserStatus.failed.value:
        return attempts >= REEMBED_MAX_ATTEMPTS
    if status == ReembedUserStatus.running.value:
        return stale_running and attempts >= REEMBED_MAX_ATTEMPTS
    # pending: never attempted yet.
    return False


def retry_exhausted_condition(users, stale_before):
    """SQL condition for "spent the retry budget, and not done".

    The query-side counterpart of :func:`is_user_terminal`, for the callers that
    cannot afford a round trip per row. Two shapes match:

    - ``failed`` with the budget gone — nothing will dispatch it again;
    - ``running``, stale, with the budget gone — its worker died on the final
      attempt. This one is a *failure* even though the status still says
      ``running``: no handler could run (``SIGKILL``, hard time limit, killed
      container), so no ``finish_job_user`` ever wrote a reason.

    :func:`finalize_job_if_complete` uses it in both directions — the condition
    itself counts as failed, its negation (plus "not done") counts as still
    outstanding — so the job's status and its remaining work cannot disagree.

    :param users: the ``MemoryReembedJobUser`` entity.
    :param stale_before: cutoff below which a ``running`` row counts as dead.
    """
    return and_(
        users.attempts >= REEMBED_MAX_ATTEMPTS,
        or_(
            users.status == ReembedUserStatus.failed.value,
            and_(
                users.status == ReembedUserStatus.running.value,
                users.updated_at < stale_before,
            ),
        ),
    )


def _as_str(value) -> str | None:
    return None if value is None else str(value)


def _report_permanent_failure(
        *,
        job_id: str,
        workspace_id: str | None,
        reason_code: str,
        old_model_name: str | None,
        new_model_name: str | None,
        total_end_users: int,
        failed_end_users: int,
        total_nodes: int,
        processed_nodes: int,
        failed_nodes: int,
        error: str | None,
) -> None:
    """把任务终态失败交给通知中心（旁路，绝不影响任务状态）。

    只在 ``mark_job_failed`` / ``finalize_job_if_complete`` **成功写下终态之后**
    调用。「谁写了终态谁负责通知」让通知的一次性由状态机的原子 UPDATE 天然保证
    （``WHERE status IN ('pending','running')`` 只会命中一次），不需要额外的
    去重表，也不会出现「任务说失败、用户永远收不到」的静默缺口。
    """
    if not workspace_id:
        return
    report_reembed_job_failure_safely(
        job_id=job_id,
        workspace_id=str(workspace_id),
        reason_code=reason_code,
        old_model_name=old_model_name,
        new_model_name=new_model_name,
        total_end_users=total_end_users,
        failed_end_users=failed_end_users,
        total_nodes=total_nodes,
        processed_nodes=processed_nodes,
        failed_nodes=failed_nodes,
        error=error,
        failed_at_ms=to_timestamp_ms(utcnow_naive()) or 0,
    )


def _report_completion(
        *,
        job_id: str,
        workspace_id: str | None,
        old_model_name: str | None,
        new_model_name: str | None,
        total_end_users: int,
        total_nodes: int,
        processed_nodes: int,
) -> None:
    """把任务终态成功交给通知中心（旁路，绝不影响任务状态）。

    与 ``_report_permanent_failure`` 同一约束：只在终态 **成功写下之后** 调用，
    一次性由状态机的原子 UPDATE 保证。
    """
    if not workspace_id:
        return
    report_reembed_job_completion_safely(
        job_id=job_id,
        workspace_id=str(workspace_id),
        old_model_name=old_model_name,
        new_model_name=new_model_name,
        total_end_users=total_end_users,
        total_nodes=total_nodes,
        processed_nodes=processed_nodes,
        completed_at_ms=to_timestamp_ms(utcnow_naive()) or 0,
    )


async def create_reembed_job_async(
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        old_embedding_config_id: str | None,
        new_embedding_config_id: str,
        old_model_name: str | None,
        new_model_name: str | None,
        created_by_user_id: uuid.UUID | None = None,
) -> MemoryReembedJob:
    """Add a pending job to ``db``; the caller commits it with its trigger.

    The id is generated here so the caller can dispatch the task right after
    committing, without an extra refresh round trip.
    """
    job = MemoryReembedJob(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        old_embedding_config_id=_as_str(old_embedding_config_id),
        new_embedding_config_id=str(new_embedding_config_id),
        old_model_name=old_model_name,
        new_model_name=new_model_name,
        created_by_user_id=created_by_user_id,
        status=ReembedJobStatus.pending.value,
    )
    db.add(job)
    return job


def dispatch_reembed_job(job_id: uuid.UUID | str) -> str | None:
    """Enqueue the driver task and return its Celery task id.

    :return: ``None`` when the broker rejected the task; the job row stays
        pending and the periodic reconciler picks it up.
    """
    from app.celery_app import celery_app

    try:
        result = celery_app.send_task(
            REEMBED_TASK_NAME,
            kwargs={"job_id": str(job_id)},
            queue=REEMBED_TASK_QUEUE,
        )
    except Exception as exc:
        logger.error(
            "memory re-embed dispatch failed: job=%s error=%s",
            job_id,
            exc,
            exc_info=True,
        )
        return None
    return result.id


def dispatch_end_user_job(
        job_id: uuid.UUID | str,
        end_user_id: str,
) -> str | None:
    """Enqueue one end_user's rebuild by task name.

    Dispatched by name rather than by importing the Celery task object: the
    orchestration lives outside ``app.tasks``, and importing back into it would
    make the two modules circular.
    """
    from app.celery_app import celery_app

    try:
        result = celery_app.send_task(
            REEMBED_USER_TASK_NAME,
            kwargs={"job_id": str(job_id), "end_user_id": end_user_id},
            queue=REEMBED_TASK_QUEUE,
        )
    except Exception as exc:
        logger.error(
            "memory re-embed end_user dispatch failed: job=%s end_user=%s "
            "error=%s",
            job_id,
            end_user_id,
            exc,
            exc_info=True,
        )
        return None
    return result.id


_CURRENT_JOB_EXCLUDED_STATUSES = (ReembedJobStatus.succeeded.value,)


def _current_job_filters(workspace_id: uuid.UUID) -> tuple:
    """两个「当前任务」查询共用的过滤条件。

    async 与 sync 各有一份实现（见 :func:`get_current_reembed_job_async` /
    :func:`get_current_reembed_job`），条件从这里取，两份因此不可能对同一批行给出
    不同的说法——「进度已经结束」的判据必须只有一个来源。
    """
    return (
        MemoryReembedJob.workspace_id == workspace_id,
        MemoryReembedJob.status.notin_(_CURRENT_JOB_EXCLUDED_STATUSES),
    )


async def get_reembed_job_async(
        db: AsyncSession,
        job_id: uuid.UUID,
) -> MemoryReembedJob | None:
    """按 id 精确查询，**不过滤状态**：终态任务也要能直接查到详情。"""
    return (
        await db.execute(
            select(MemoryReembedJob).where(MemoryReembedJob.id == job_id)
        )
    ).scalar_one_or_none()


async def get_current_reembed_job_async(
        db: AsyncSession,
        workspace_id: uuid.UUID,
) -> MemoryReembedJob | None:
    """该工作空间当前的存量向量重算任务；已成功则返回 ``None``。

    返回的是「最近一次仍在展示的任务」，不一定是最新那一条：更新的那次若已成功，
    会被跳过而回退到更早的可见任务（见 :func:`_current_job_filters`）。``None``
    由调用方翻译成既有的"无任务"空响应（``data: {}``）。
    """
    return (
        await db.execute(
            select(MemoryReembedJob)
            .where(*_current_job_filters(workspace_id))
            .order_by(MemoryReembedJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def get_active_reembed_job_id(db: Session, workspace_id: uuid.UUID) -> str | None:
    """该工作空间在途（``pending``/``running``）的存量向量重算任务 id。

    返回非 ``None`` 即"正在切换 embedding 模型"：任务在途期间存量向量只重建了
    一部分（甚至还没开始），此时再切一次会让已重建的向量重新落回旧模型空间。
    返回 id 而非布尔值，是为了让前端直接拿它去查
    ``GET /api/workspaces/workspace_reembed/{job_id}`` 的进度，不必再猜是哪一次任务。

    同一工作空间理论上只可能有一个在途任务（切换入口带排他锁与在途检查），
    若存量数据里存在多个则取最新的——与 :func:`newer_active_job_exists` 同口径。
    """
    job_id = (
        db.query(MemoryReembedJob.id)
        .filter(
            MemoryReembedJob.workspace_id == workspace_id,
            MemoryReembedJob.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(MemoryReembedJob.created_at.desc())
        .limit(1)
        .scalar()
    )
    return None if job_id is None else str(job_id)


async def get_active_reembed_job_id_async(
        db: AsyncSession,
        workspace_id: uuid.UUID,
) -> str | None:
    """``get_active_reembed_job_id`` 的异步版本，供 async 端点使用。

    调用方必须已经持有 workspace 行的排他锁（见
    :func:`app.services.workspace_service.update_workspace_models_configs`），
    否则"检查—建任务"之间会被并发请求插入。
    """
    job_id = (
        await db.execute(
            select(MemoryReembedJob.id)
            .where(
                MemoryReembedJob.workspace_id == workspace_id,
                MemoryReembedJob.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(MemoryReembedJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return None if job_id is None else str(job_id)


@redis_cache(ttl=60, prefix="reembed_degrade", skip_args=["db"], id_arg="end_user_id")
async def should_degrade_semantic(
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        end_user_id: str,
        embedding_model_id: uuid.UUID,
) -> bool:
    """判断某 end_user 的存量向量是否尚未用当前 embedding 模型重建完成。

    读路径在做向量检索前调用。切换 embedding 底层模型后、该 end_user 的重算
    完成之前，存量向量在旧模型空间、查询向量却是新模型，向量检索会召回语义
    不相关（同维）或完全漏掉（异维）的结果，此窗口内应降级为纯全文检索。

    永久失败的行（``failed`` 且重试预算耗尽）永远到不了 ``done``，因此会持续
    降级，直到运维手动重试该任务。

    :return: 存在一个未作废、且目标模型等于 ``embedding_model_id`` 的任务，且
        该 end_user 在其中没有 ``done`` 行时返回 ``True``。
    """
    done_row = select(MemoryReembedJobUser.id).where(
        MemoryReembedJobUser.job_id == MemoryReembedJob.id,
        MemoryReembedJobUser.end_user_id == end_user_id,
        MemoryReembedJobUser.status == ReembedUserStatus.done.value,
    )
    stmt = (
        select(MemoryReembedJob.id)
        .where(
            MemoryReembedJob.workspace_id == workspace_id,
            MemoryReembedJob.new_embedding_config_id == str(embedding_model_id),
            ~exists(done_row),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def build_reembed_job_payload(
        db: AsyncSession,
        job: MemoryReembedJob,
) -> dict:
    """Serialize a job row plus its durable per-user breakdown.

    取调用方的 async session：本函数由 async handler 直接调用，自己再开一个同步
    session 会把整个事件循环挡住（见 :func:`summarize_job_users`）。
    """
    return {
        "id": str(job.id),
        "workspace_id": str(job.workspace_id),
        "status": job.status,
        "old_model_name": job.old_model_name,
        "new_model_name": job.new_model_name,
        "total_end_users": job.total_end_users,
        "processed_end_users": job.processed_end_users,
        # 节点级进度（total_nodes/processed_nodes）不再返回：前端只按 end_user
        # 粒度显示进度，节点数对它没有意义。失败节点数保留，它是 end_user 状态
        # 分布之外唯一能看出"损坏规模"的量。
        "failed_nodes": job.failed_nodes,
        # 按状态分组的 end_user 计数（pending/running/done/failed）
        "end_users": await summarize_job_users(db, str(job.id)),
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


# ── 状态机（同步，供 Celery 任务使用） ─────────────────────────────────────
#
# 权威状态一律落 PG：``done`` / ``failed`` 由 memory_reembed_job_users 表达，
# 任务是否跑完由"所有行都已终态"判定。Redis 挂了也不会让任务停在 running。


def mark_job_failed(
        job_id: str,
        *,
        error: str,
        reason_code: str | None = None,
        notify: bool = True,
) -> bool:
    """Fail a job before any work is written (pre-flight rejections).

    ``reason_code`` 只用于通知中心的原因分类（见
    :mod:`app.core.memory.alerts`），不参与状态机。

    ``notify=False`` 用于"任务本身该停，但没有任何东西失败"的情形（见
    :func:`app.services.memory_reembed_orchestrator` 的扇出自检）：终端状态只剩
    ``failed`` 这一个可用值，为了不把这种情形伪装成用户的失败，选择不发通知，
    原委写进 ``error`` 供排查。
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return False
    with get_db_context() as db:
        job = (
            db.query(MemoryReembedJob)
            .filter(MemoryReembedJob.id == job_uuid)
            .first()
        )
        if job is None:
            return False
        # 上报要用的字段在 update 之前取出：session 关闭后 ORM 实例不可用，
        # 而这次更新本身不改这些列，先后取的语义一致。
        context = {
            "workspace_id": str(job.workspace_id),
            "old_model_name": job.old_model_name,
            "new_model_name": job.new_model_name,
            "total_end_users": int(job.total_end_users or 0),
            "total_nodes": int(job.total_nodes or 0),
        }
        updated = (
            db.query(MemoryReembedJob)
            .filter(
                MemoryReembedJob.id == job_uuid,
                MemoryReembedJob.status.in_(_ACTIVE_STATUSES),
            )
            .update(
                {
                    "status": ReembedJobStatus.failed.value,
                    "finished_at": utcnow_naive(),
                    "error": error,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if not updated:
            # 已被别的调用方写成终态：终态与通知都归它。
            return False

    if not notify:
        logger.info(
            "[MemoryReembed] job stopped without notifying: job=%s error=%s",
            job_id,
            error,
        )
        return True

    if not reason_code:
        # 上报必须带原因码，白名单由 alerts 把关；这是编程错误而非数据问题。
        raise ValueError("reason_code is required when notify=True")

    _report_permanent_failure(
        job_id=job_id,
        reason_code=reason_code,
        # 前置换算没写过任何一行，所以失败用户/节点计数都还是零。
        failed_end_users=0,
        processed_nodes=0,
        failed_nodes=0,
        error=error,
        **context,
    )
    return True


def ensure_job_users(job_id: str, node_counts: dict[str, int]) -> int:
    """Create one row per end_user that actually has nodes to rebuild.

    The denominator comes from the fan-out's inventory (``inventory.py``, a live
    per-label aggregate over the vector-bearing labels): end_users without such
    nodes cost nothing to skip, and counting them would make the job's numbers
    disagree with what the memory library shows for no reason.

    It deliberately does **not** use ``end_users.memory_count``: that column
    counts every memory node of the user, including labels with no re-embeddable
    vector (``AssistantOriginal`` / ``AssistantPruned`` / ``Conversation``), so
    it overstates the work and makes a finished job report ``processed < total``.

    :param node_counts: ``{end_user_id: node_count}`` from the fan-out's
        inventory.
    :return: number of rows the job now covers.
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return 0
    with get_db_context() as db:
        if node_counts:
            # ON CONFLICT DO NOTHING keeps re-dispatch idempotent and never
            # resets an already-finished row.
            db.execute(
                pg_insert(MemoryReembedJobUser)
                .values([
                    {
                        "id": uuid.uuid4(),
                        "job_id": job_uuid,
                        "end_user_id": end_user_id,
                        "status": ReembedUserStatus.pending.value,
                        "total_nodes": total_nodes,
                    }
                    for end_user_id, total_nodes in node_counts.items()
                ])
                .on_conflict_do_nothing(
                    index_elements=["job_id", "end_user_id"],
                )
            )
        counters = (
            db.query(
                func.count(MemoryReembedJobUser.id),
                func.coalesce(func.sum(MemoryReembedJobUser.total_nodes), 0),
            )
            .filter(MemoryReembedJobUser.job_id == job_uuid)
            .one()
        )
        db.query(MemoryReembedJob).filter(
            MemoryReembedJob.id == job_uuid,
        ).update(
            {
                "total_end_users": int(counters[0] or 0),
                "total_nodes": int(counters[1] or 0),
            },
            synchronize_session=False,
        )
        db.commit()
    return int(counters[0] or 0)


def claimable_end_users(job_id: str) -> list[str]:
    """end_users the driver should dispatch work for.

    Three kinds, mirroring :func:`is_user_terminal`:

    - ``pending`` — never attempted;
    - ``failed`` with budget left — the retry that turns a page failure into a
      redo instead of a permanent skip;
    - ``running`` that went stale — its worker died mid-run, so reclaim it.

    ``running`` rows with budget left and a fresh heartbeat are deliberately
    excluded: another worker is on them.
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return []
    stale_before = utcnow_naive() - timedelta(
        seconds=REEMBED_STALE_RUNNING_SECONDS
    )
    users = MemoryReembedJobUser
    with get_db_context() as db:
        rows = (
            db.query(users.end_user_id)
            .filter(
                users.job_id == job_uuid,
                or_(
                    users.status == ReembedUserStatus.pending.value,
                    and_(
                        users.status == ReembedUserStatus.failed.value,
                        users.attempts < REEMBED_MAX_ATTEMPTS,
                    ),
                    and_(
                        users.status == ReembedUserStatus.running.value,
                        users.attempts < REEMBED_MAX_ATTEMPTS,
                        users.updated_at < stale_before,
                    ),
                ),
            )
            .order_by(users.created_at.asc())
            .all()
        )
    return [str(row[0]) for row in rows]


def touch_job_user(job_id: str, end_user_id: str, *, attempts: int) -> bool:
    """Renew one row's liveness, page by page.

    Uses CAS semantics: only updates if the row is still in ``running`` state
    with the expected ``attempts`` count. If another worker has taken over the
    end_user (claim expired, new worker incremented attempts), the update
    returns False to signal that this worker no longer owns the row.

    This is what makes stale-detection meaningful: a worker that dies stops
    renewing, so its row becomes claimable again instead of pinning the job.

    :return: True if the row was updated, False if the row is no longer owned
        by this worker.
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return False
    with get_db_context() as db:
        updated = (
            db.query(MemoryReembedJobUser)
            .filter(
                MemoryReembedJobUser.job_id == job_uuid,
                MemoryReembedJobUser.end_user_id == end_user_id,
                MemoryReembedJobUser.status == ReembedUserStatus.running.value,
                MemoryReembedJobUser.attempts == attempts,
            )
            .update(
                {"updated_at": utcnow_naive()},
                synchronize_session=False,
            )
        )
        db.commit()
    return bool(updated)


def get_job_user(job_id: str, end_user_id: str) -> dict | None:
    """Read one end_user row as a plain dict (or ``None`` when absent)."""
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return None
    with get_db_context() as db:
        row = (
            db.query(MemoryReembedJobUser)
            .filter(
                MemoryReembedJobUser.job_id == job_uuid,
                MemoryReembedJobUser.end_user_id == end_user_id,
            )
            .first()
        )
        if row is None:
            return None
        return {
            "end_user_id": row.end_user_id,
            "status": row.status,
            "attempts": row.attempts,
            "processed_nodes": row.processed_nodes,
            "failed_nodes": row.failed_nodes,
            "last_error": row.last_error,
        }


def start_job_user(job_id: str, end_user_id: str) -> int:
    """Mark one end_user as running and consume one attempt.

    Uses CAS semantics: only updates if the row is in ``pending`` or ``failed``
    state (not ``running`` by another worker, not ``done``). This prevents two
    workers from both incrementing attempts for the same end_user if the Redis
    claim expired and was re-acquired.

    :return: attempts after this one; ``0`` when the row is missing or was not
        in a claimable state (another worker is on it, or it's already done).
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return 0
    with get_db_context() as db:
        updated = (
            db.query(MemoryReembedJobUser)
            .filter(
                MemoryReembedJobUser.job_id == job_uuid,
                MemoryReembedJobUser.end_user_id == end_user_id,
                MemoryReembedJobUser.status.in_(
                    [
                        ReembedUserStatus.pending.value,
                        ReembedUserStatus.failed.value,
                    ]
                ),
            )
            .update(
                {
                    "status": ReembedUserStatus.running.value,
                    "attempts": MemoryReembedJobUser.attempts + 1,
                    "updated_at": utcnow_naive(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if not updated:
            # Row is not in a claimable state (running or done)
            return 0
        attempts = (
            db.query(MemoryReembedJobUser.attempts)
            .filter(
                MemoryReembedJobUser.job_id == job_uuid,
                MemoryReembedJobUser.end_user_id == end_user_id,
            )
            .scalar()
        )
    return int(attempts or 0)


def finish_job_user(
        job_id: str,
        end_user_id: str,
        *,
        attempts: int,
        processed_nodes: int,
        failed_nodes: int,
        error: str | None = None,
) -> str | None:
    """Record one end_user's outcome and refresh the job's counters.

    Uses CAS semantics: only updates if the row is still in ``running`` state
    with the expected ``attempts`` count. If another worker has taken over the
    end_user (claim expired, new worker incremented attempts), the update
    returns None to signal that the result should not be applied.

    :return: the row's new status (``done`` or ``failed``), or ``None`` if the
        row was no longer owned by this worker (another worker took over).
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return ReembedUserStatus.pending.value
    status = (
        ReembedUserStatus.failed.value
        if error is not None
        else ReembedUserStatus.done.value
    )
    with get_db_context() as db:
        updated = (
            db.query(MemoryReembedJobUser)
            .filter(
                MemoryReembedJobUser.job_id == job_uuid,
                MemoryReembedJobUser.end_user_id == end_user_id,
                MemoryReembedJobUser.status == ReembedUserStatus.running.value,
                MemoryReembedJobUser.attempts == attempts,
            )
            .update(
                {
                    "status": status,
                    "processed_nodes": processed_nodes,
                    "failed_nodes": failed_nodes,
                    "last_error": error,
                    "updated_at": utcnow_naive(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
    if not updated:
        # Row was taken over by another worker (claim expired, attempts incremented)
        logger.warning(
            "[MemoryReembed] finish_job_user skipped: row no longer owned "
            "(claim expired or taken over) job=%s end_user=%s expected_attempts=%s",
            job_id,
            end_user_id,
            attempts,
        )
        return None
    refresh_job_counters(job_id)
    if status == ReembedUserStatus.done.value:
        # 该用户向量已重建完成，失效降级缓存，让语义检索立即恢复（不等 TTL）。
        try:
            invalidate_cache_sync(prefix=f"reembed_degrade:{end_user_id}")
        except Exception:
            # 缓存失效失败不影响重算完成落库，仅记录日志。
            logger.warning(
                "[MemoryReembed] degrade cache invalidation failed: end_user=%s",
                end_user_id,
                exc_info=True,
            )
    return status


def fail_job_user(
        job_id: str,
        end_user_id: str,
        *,
        attempts: int,
        error: str,
) -> bool:
    """Record an attempt that died with an exception instead of an outcome.

    :func:`finish_job_user` cannot be used for this: it writes whatever the run
    reported, and a crashed run has nothing to report. Leaving the row alone is
    what used to keep it ``running`` forever — nothing re-dispatches a row that
    spent its budget, and the finalizer would then see no failure and write
    ``succeeded`` for work that never happened.

    Guarded on ``attempts``: a row another worker already took over has consumed
    the next attempt, and overwriting it would fail work that is still running.

    :return: whether the row was updated.
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return False
    with get_db_context() as db:
        updated = (
            db.query(MemoryReembedJobUser)
            .filter(
                MemoryReembedJobUser.job_id == job_uuid,
                MemoryReembedJobUser.end_user_id == end_user_id,
                MemoryReembedJobUser.status == ReembedUserStatus.running.value,
                MemoryReembedJobUser.attempts == attempts,
            )
            .update(
                {
                    "status": ReembedUserStatus.failed.value,
                    "last_error": error,
                    "updated_at": utcnow_naive(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
    if updated:
        refresh_job_counters(job_id)
    return bool(updated)


def release_job_user(job_id: str, end_user_id: str, *, attempts: int) -> bool:
    """Return a claimed end_user to ``pending`` when a job loses ownership.

    Uses CAS semantics: only updates if the row is still in ``running`` state
    with the expected ``attempts`` count. If another worker has taken over the
    end_user (claim expired, new worker incremented attempts), the update
    returns False.

    :return: True if the row was released, False if the row is no longer owned
        by this worker.
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return False
    with get_db_context() as db:
        updated = (
            db.query(MemoryReembedJobUser)
            .filter(
                MemoryReembedJobUser.job_id == job_uuid,
                MemoryReembedJobUser.end_user_id == end_user_id,
                MemoryReembedJobUser.status == ReembedUserStatus.running.value,
                MemoryReembedJobUser.attempts == attempts,
            )
            .update(
                {
                    "status": ReembedUserStatus.pending.value,
                    "updated_at": utcnow_naive(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
    return bool(updated)


def _rebuilt_nodes_expression():
    """Each row's contribution to the job-level ``processed_nodes``.

    任务级数字不是 per-user 列的简单求和。那一列只存**最近一次尝试**的写入量
    （见 ``MemoryReembedEndUserItem.processed_nodes`` 的契约），而重试从失败的页
    续跑（``rebuilder.py`` 的游标），所以一个被重试过的行只贡献它真正重建量的
    一小部分——按列求和会系统性偏低。

    ``done`` 行改用它自己的 ``total_nodes``：扇出分母与扫描、写入用的是同三条
    口径（见 ``inventory.py``），所以跑完的 end_user 重建量恰好等于该分母。未跑完
    的行仍取最后一次尝试的写入量——那是它唯一可用的数字。

    这样才保住分母当初被挑出来要维护的不变量：succeeded 任务（所有行都 done）
    报 ``processed_nodes == total_nodes``，而不是 ``processed < total``。
    """
    return case(
        (
            MemoryReembedJobUser.status == ReembedUserStatus.done.value,
            MemoryReembedJobUser.total_nodes,
        ),
        else_=MemoryReembedJobUser.processed_nodes,
    )


def refresh_job_counters(job_id: str) -> dict:
    """Recompute job counters from the per-user rows.

    Derived rather than incremented: re-running an end_user can never inflate
    the totals, and no Redis state is involved.

    ``processed_nodes`` sums :func:`_rebuilt_nodes_expression` instead of the raw
    column, so a retried end_user that finished still counts in full; see that
    function for why the two differ.
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return {}
    with get_db_context() as db:
        row = (
            db.query(
                func.count(MemoryReembedJobUser.id).label("total"),
                func.count(MemoryReembedJobUser.id)
                .filter(
                    MemoryReembedJobUser.status == ReembedUserStatus.done.value
                )
                .label("done"),
                func.coalesce(
                    func.sum(MemoryReembedJobUser.total_nodes), 0
                ).label("total_nodes"),
                func.coalesce(
                    func.sum(_rebuilt_nodes_expression()), 0
                ).label("processed_nodes"),
                func.coalesce(
                    func.sum(MemoryReembedJobUser.failed_nodes), 0
                ).label("failed_nodes"),
            )
            .filter(MemoryReembedJobUser.job_id == job_uuid)
            .one()
        )
        processed_nodes = int(row.processed_nodes or 0)
        failed_nodes = int(row.failed_nodes or 0)
        counters = {
            "total_end_users": int(row.total or 0),
            "processed_end_users": int(row.done or 0),
            "processed_nodes": processed_nodes,
            "failed_nodes": failed_nodes,
            # 分母是扇出时 inventory 按 label 实时聚合之和（见 inventory.py），
            # 不是 end_users.memory_count 那个手动刷新的缓存。
            "total_nodes": int(row.total_nodes or 0),
        }
        db.query(MemoryReembedJob).filter(
            MemoryReembedJob.id == job_uuid,
        ).update(counters, synchronize_session=False)
        db.commit()
    return counters


def finalize_job_if_complete(
        job_id: str,
        *,
        allow_empty: bool = False,
) -> str | None:
    """Write the job's terminal status once every end_user row is terminal.

    :param allow_empty: whether a job with no rows at all may be finalized. Only
        the fan-out itself may say so, after its inventory legitimately found
        nothing to rebuild; everyone else must leave a row-less job alone, or a
        job that has not fanned out yet would be finalized as ``succeeded`` the
        moment the reconciler looked at it.
    :return: the new status, or ``None`` when work remains.
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        return None
    with get_db_context() as db:
        job = (
            db.query(MemoryReembedJob)
            .filter(MemoryReembedJob.id == job_uuid)
            .first()
        )
        if job is None or job.status not in _ACTIVE_STATUSES:
            return None
        if not allow_empty:
            total_rows = (
                db.query(func.count(MemoryReembedJobUser.id))
                .filter(MemoryReembedJobUser.job_id == job_uuid)
                .scalar()
            )
            if int(total_rows or 0) == 0:
                # 还没扇出（或清单尚未建行）：不能当成"已完成"。
                return None
        # "还没跑完"必须与认领判据同源：有重试余额的失败行仍算未完成，
        # 否则任务会在派发重试之前就收尾，失败页再也不会被重做。
        # 反面同样成立：attempts 耗尽的 stale running 行不再有任何重试，是失败
        # 而不是"在跑"——只数 failed 行会把它漏掉，让 job 假成功。
        stale_before = utcnow_naive() - timedelta(
            seconds=REEMBED_STALE_RUNNING_SECONDS
        )
        users = MemoryReembedJobUser
        outstanding = (
            db.query(func.count(users.id))
            .filter(
                users.job_id == job_uuid,
                users.status != ReembedUserStatus.done.value,
                ~retry_exhausted_condition(users, stale_before),
            )
            .scalar()
        )
        if int(outstanding or 0) > 0:
            return None
        failed_users = (
            db.query(func.count(users.id))
            .filter(
                users.job_id == job_uuid,
                retry_exhausted_condition(users, stale_before),
            )
            .scalar()
        )
        failure = (
            db.query(users.last_error)
            .filter(
                users.job_id == job_uuid,
                users.last_error.isnot(None),
            )
            .order_by(users.updated_at.desc())
            .limit(1)
            .scalar()
        )
        failed_count = int(failed_users or 0)
        # 提前记下「没有原因可写」这一形态：它正是 worker 猝死（SIGKILL、硬超时、
        # 容器被杀）的判据，下面会把 failure 替换成占位文案而丢掉这个信息。
        worker_crashed = bool(failed_count) and failure is None
        if worker_crashed:
            # worker 死在最后一次尝试上：没有任何一次 finish_job_user 写过原因。
            failure = "worker stopped before recording an error"
        status = (
            ReembedJobStatus.failed.value
            if failed_count
            else ReembedJobStatus.succeeded.value
        )
        existing_error = job.error
        final_error = existing_error or (
            f"failed_end_users={failed_count}: {failure}"
            if status == ReembedJobStatus.failed.value
            else None
        )
        # 计数器由 finish_job_user / fail_job_user 收尾时刷新，这里只读；
        # 上报用的快照因此与终态写入的先后无关。两个快照都在此构造：成功与失败
        # 互斥，只有一条会上报，共用这组只读字段。
        common_snapshot = {
            "workspace_id": str(job.workspace_id),
            "old_model_name": job.old_model_name,
            "new_model_name": job.new_model_name,
            "total_end_users": int(job.total_end_users or 0),
            "total_nodes": int(job.total_nodes or 0),
            "processed_nodes": int(job.processed_nodes or 0),
        }
        failure_snapshot = {
            **common_snapshot,
            "failed_end_users": failed_count,
            "failed_nodes": int(job.failed_nodes or 0),
        }
        updated = (
            db.query(MemoryReembedJob)
            .filter(
                MemoryReembedJob.id == job_uuid,
                MemoryReembedJob.status.in_(_ACTIVE_STATUSES),
            )
            .update(
                {
                    "status": status,
                    "finished_at": utcnow_naive(),
                    "error": final_error,
                },
                synchronize_session=False,
            )
        )
        db.commit()
    if not updated:
        return None
    if status == ReembedJobStatus.failed.value:
        _report_permanent_failure(
            job_id=job_id,
            reason_code=(
                _WORKER_CRASHED if worker_crashed else _END_USER_REBUILD_FAILED
            ),
            error=final_error,
            **failure_snapshot,
        )
    else:
        # 终态成功：与失败成对上报，用户切换 embedding 后必收到其一。
        _report_completion(job_id=job_id, **common_snapshot)
    logger.info(
        "[MemoryReembed] job finalized: job=%s status=%s failed_end_users=%s",
        job_id,
        status,
        failed_users,
    )
    return status


def _display_status_count_columns(users, stale_before) -> list:
    """每个展示态一列计数，让一次查询就能出全四个桶。

    复用 :func:`_display_status_condition`——与 ``list_job_end_users`` 的
    ``summary`` 同源，两个端点因此不可能对同一批行给出不同的说法。
    """
    return [
        func.count(users.id)
        .filter(_display_status_condition(bucket, users, stale_before))
        .label(bucket)
        for bucket in REEMBED_DISPLAY_STATUSES
    ]


async def summarize_job_users(db: AsyncSession, job_id: str) -> dict:
    """Per-status end_user counts, for the status endpoint.

    键是**展示四态**（``REEMBED_DISPLAY_STATUSES``），与
    :func:`list_job_end_users` 的 ``summary`` 同一套词汇和谓词。这里曾经直接按
    数据库里的原始状态分组，于是同一个任务在两个端点上各说一套：原始 ``failed``
    包含"还有重试余额、会被对账任务自动重派"的行，而展示 ``failed`` 只含预算耗尽
    的终态失败。**两个键名相同、集合不同**，前端照着 payload 提示用户重试，会撞上
    重试接口的 ``STATE_CONFLICT``（那边只认终态失败）。

    四态判定需要 ``attempts`` 与陈旧度，原始字典带不了这两个信息，所以这不是键名
    映射能修的——只能在这里按同一谓词算。

    Uses the caller's async session on purpose: the callers are async handlers,
    and opening a sync session here would block the event loop for the length of
    the query.
    """
    job_uuid = _as_uuid(job_id)
    if job_uuid is None:
        # 形状照旧是四桶：调用方按固定键读，少一个键就等于少一个页签角标。
        return {bucket: 0 for bucket in REEMBED_DISPLAY_STATUSES}
    users = MemoryReembedJobUser
    row = (
        await db.execute(
            select(*_display_status_count_columns(users, _stale_before()))
            .where(users.job_id == job_uuid)
        )
    ).one()
    return {
        bucket: int(value or 0)
        for bucket, value in zip(REEMBED_DISPLAY_STATUSES, row)
    }


# ── end_user 维度的状态查询与人工重试 ─────────────────────────────────────
#
# 四态的划分标准是**有没有被处理过**，而不是"原始状态叫什么"：
#
#   queued    从未被处理过（``pending``）—— 还没轮到它。
#   running   跑过、但还没到终态：正在跑，或失败/worker 猝死后仍有预算、会被
#             对账任务自动重派。用户视角是"系统还在处理它"。
#   failed    终态失败，重试预算已耗尽 —— **唯一需要人介入**的桶，前端看到它就
#             可以显示重试按钮，不必自己算 attempts。
#   succeeded 已完成。
#
# 这里曾经把"还有重试余额的失败"归入 ``queued``，理由是它会被自动重派、说它"失败"
# 会误导用户去点重试。那个理由的另一半（不能标成 failed）是对的，但落到 ``queued``
# 上就错了：一行明明跑过、失败了、还会被自动重试，却显示成"排队中"——看起来像从没
# 开始过，用户读不出"它正在被处理"。所以改成 ``running``：既不会误导用户点重试
# （那由 ``failed`` 独占），也不再谎称它还没开始。
#
# 权威判定（``is_user_terminal`` / ``retry_exhausted_condition``）**不受这里影响**：
# 改的只是展示词汇，派发资格与任务收尾仍然只看 ``attempts`` 与陈旧度。

REEMBED_DISPLAY_QUEUED = "queued"
REEMBED_DISPLAY_RUNNING = "running"
REEMBED_DISPLAY_SUCCEEDED = "succeeded"
REEMBED_DISPLAY_FAILED = "failed"

#: 枚举顺序即**列表展示顺序**：失败在最前（要人处理），成功在最后。
#: 列表排序用的名次、以及 summary 的键序都取自这里，所以顺序本身就是契约。
REEMBED_DISPLAY_STATUSES = (
    REEMBED_DISPLAY_FAILED,
    REEMBED_DISPLAY_RUNNING,
    REEMBED_DISPLAY_QUEUED,
    REEMBED_DISPLAY_SUCCEEDED,
)

#: 一次人工重试最多重新排队多少行，避免一个请求把 worker 队列打爆。
REEMBED_RETRY_BATCH_LIMIT = 500


def _stale_before():
    return utcnow_naive() - timedelta(seconds=REEMBED_STALE_RUNNING_SECONDS)


def display_status(status: str, attempts: int, *, stale_running: bool) -> str:
    """行的四态语义。

    判定与 :func:`is_user_terminal` 同源——不另立一套规则，否则两边迟早会不一致。

    ``queued`` 只留给**从未被处理过**的行；只要跑过一次且尚未终态（正在跑、失败后
    等自动重派、worker 猝死后等回收），一律是 ``running``。理由见下方常量块。
    """
    if status == ReembedUserStatus.done.value:
        return REEMBED_DISPLAY_SUCCEEDED
    if is_user_terminal(status, attempts, stale_running=stale_running):
        return REEMBED_DISPLAY_FAILED
    if status == ReembedUserStatus.pending.value:
        return REEMBED_DISPLAY_QUEUED
    return REEMBED_DISPLAY_RUNNING


def _display_status_rank(users, stale_before):
    """四态在列表里的排序名次：失败 → 处理中 → 待处理 → 成功。

    复用 :func:`_display_status_condition` 的条件，而不是把判定重写一遍——排序名次
    与过滤口径一旦各写一份，就会出现"筛出来的组和排序顺序对不上"。
    """
    return case(
        *[
            (_display_status_condition(bucket, users, stale_before), rank)
            for rank, bucket in enumerate(REEMBED_DISPLAY_STATUSES, start=1)
        ],
        else_=len(REEMBED_DISPLAY_STATUSES) + 1,
    )


def _display_status_condition(bucket: str, users, stale_before):
    """``display_status`` 的 SQL 对偶。四支互斥且穷尽，由测试固定。"""
    done = users.status == ReembedUserStatus.done.value
    failed = users.status == ReembedUserStatus.failed.value
    running = users.status == ReembedUserStatus.running.value
    pending = users.status == ReembedUserStatus.pending.value
    exhausted = users.attempts >= REEMBED_MAX_ATTEMPTS
    stale = users.updated_at < stale_before
    # 终态失败：failed 且预算耗尽，或 running 陈旧且预算耗尽（worker 死在最后一次）
    terminal_failed = or_(and_(failed, exhausted), and_(running, stale, exhausted))

    if bucket == REEMBED_DISPLAY_SUCCEEDED:
        return done
    if bucket == REEMBED_DISPLAY_FAILED:
        return terminal_failed
    if bucket == REEMBED_DISPLAY_QUEUED:
        # 只含从未被处理过的行（见 ``display_status``）。
        return pending
    if bucket == REEMBED_DISPLAY_RUNNING:
        # 跑过且还没终态的全部：正在跑、失败后等自动重派、猝死后等回收。
        # 写成补集而不是再枚举一遍三种原始状态：迁移到"某些失败也算 running"时
        # 只要上面三支对了，这一支自动跟着对。
        return and_(~done, ~terminal_failed, ~pending)
    # 不认识的取值必须炸出来：静默落到某一支会让 ?status=拼错 时返回一批不相干的行。
    raise ValueError(f"unknown reembed display status: {bucket}")


def _end_user_labels(db: Session, end_user_ids: list[str]) -> dict[str, dict]:
    """行的展示名。``memory_reembed_job_users.end_user_id`` 存的是 end_users.id 的字符串。"""
    from app.models.end_user_model import EndUser

    ids = [value for value in (_as_uuid(raw) for raw in end_user_ids) if value]
    if not ids:
        return {}
    rows = (
        db.query(EndUser.id, EndUser.other_name, EndUser.other_id)
        .filter(EndUser.id.in_(ids))
        .all()
    )
    return {
        str(row[0]): {"name": row[1] or None, "external_id": row[2] or None}
        for row in rows
    }


def get_reembed_job(db: Session, job_id: uuid.UUID) -> MemoryReembedJob | None:
    """同步版任务查询，供只读/人工操作的端点使用。"""
    return db.query(MemoryReembedJob).filter(MemoryReembedJob.id == job_id).first()


def get_current_reembed_job(
        db: Session,
        workspace_id: uuid.UUID,
) -> MemoryReembedJob | None:
    """该工作空间当前的存量向量重算任务（同步版）；已成功则返回 ``None``。

    与 :func:`get_current_reembed_job_async` 同源（共用 :func:`_current_job_filters`）：
    「进度已经结束」的判据只有一个来源，两个端点不会一个清空、另一个还返回旧任务。
    """
    return (
        db.query(MemoryReembedJob)
        .filter(*_current_job_filters(workspace_id))
        .order_by(MemoryReembedJob.created_at.desc())
        .first()
    )


def empty_job_end_user_page(
        *,
        page: int = 1,
        pagesize: int = 20,
) -> dict:
    """没有当前任务时的分页响应，形状与 :func:`list_job_end_users` **完全一致**。

    分页接口的空态也必须是分页结构：``data: {}`` 会让客户端在
    ``data.items.length`` 上直接崩，而调用方不该为"有没有任务"写两套解析。
    项目里空分页的先例同形（``app_controller`` 的 ``PageData(page=..., items=[])``）。

    - ``page``/``pagesize`` 回显请求值，与有任务那条路径同口径（那边也是原样回显）；
    - ``summary`` 仍是**四键全 0**，不是空对象：客户端按固定键读页签角标，少一个键
      就等于少一个角标（同 :func:`summarize_job_users` 的理由）；
    - ``job_id`` 为 ``None``、``job_status`` 为空串——没有任务，这两个字段没有真值，
      调用方靠 ``job_id is None`` 区分"无任务"与"任务里恰好没有行"。
    """
    return {
        "job_id": None,
        "job_status": "",
        "page": PageMeta(
            page=page,
            pagesize=pagesize,
            total=0,
            hasnext=False,
        ),
        "summary": {bucket: 0 for bucket in REEMBED_DISPLAY_STATUSES},
        "items": [],
    }


def list_job_end_users(
        db: Session,
        *,
        job_id: uuid.UUID,
        status: str | None = None,
        page: int = 1,
        pagesize: int = 20,
) -> dict:
    """按 end_user 列出任务内各行的状态，可分页、可按四态过滤。

    ``summary`` 始终是整个任务的四态计数（不受 ``status`` 过滤影响），供前端画
    页签角标。
    """
    if status and status not in REEMBED_DISPLAY_STATUSES:
        raise BusinessException(
            message=f"未知的状态过滤值: {status}",
            code=BizCode.INVALID_PARAMETER,
        )

    users = MemoryReembedJobUser
    stale_before = _stale_before()

    base = db.query(users).filter(users.job_id == job_id)
    if status:
        base = base.filter(_display_status_condition(status, users, stale_before))
    total = base.count()

    # 主排序：四态展示顺序（失败 → 处理中 → 待处理 → 成功）。
    # 副排序：end_user_id —— 它在任务内唯一，是分页稳定的前提；只按状态排的话，
    # 同状态的行在两次查询间次序不定，翻页会重复或漏行。
    offset = max(page - 1, 0) * pagesize
    rows = (
        base.order_by(
            _display_status_rank(users, stale_before).asc(),
            users.end_user_id.asc(),
        )
        .offset(offset)
        .limit(pagesize)
        .all()
    )
    labels = _end_user_labels(db, [str(row.end_user_id) for row in rows])

    items = []
    for row in rows:
        end_user_id = str(row.end_user_id)
        label = labels.get(end_user_id, {})
        items.append({
            "end_user_id": end_user_id,
            "name": label.get("name"),
            "external_id": label.get("external_id"),
            "status": display_status(
                row.status,
                int(row.attempts or 0),
                stale_running=bool(row.updated_at and row.updated_at < stale_before),
            ),
            "attempts": int(row.attempts or 0),
            "max_attempts": REEMBED_MAX_ATTEMPTS,
            "total_nodes": int(row.total_nodes or 0),
            "processed_nodes": int(row.processed_nodes or 0),
            "failed_nodes": int(row.failed_nodes or 0),
            "last_error": row.last_error,
            "updated_at": row.updated_at,
        })

    summary = {
        bucket: int(
            db.query(func.count(users.id))
            .filter(
                users.job_id == job_id,
                _display_status_condition(bucket, users, stale_before),
            )
            .scalar()
            or 0
        )
        for bucket in REEMBED_DISPLAY_STATUSES
    }

    job = get_reembed_job(db, job_id)
    total_count = int(total or 0)
    return {
        "job_id": job_id,
        "job_status": job.status if job else "",
        # 分页元数据走项目统一结构（与 ``api_key_service`` / ``model_service`` 同形），
        # 而不是扁平的 page/pagesize/total：``page`` 是对象不是 int。
        "page": PageMeta(
            page=page,
            pagesize=pagesize,
            total=total_count,
            hasnext=(page * pagesize) < total_count,
        ),
        "summary": summary,
        "items": items,
    }


def _workspace_embedding_config_id(
        db: Session,
        workspace_id: uuid.UUID,
) -> str | None:
    """工作空间当前生效的 embedding 配置 id（None＝查询不到该工作空间）。

    与 orchestrator 的 ``_reembed_workspace_targets_model`` 同一个不变量：任务
    只有在其目标模型仍是工作空间当前模型时才该继续写。
    """
    from app.models.workspace_model import Workspace

    value = (
        db.query(Workspace.embedding).filter(Workspace.id == workspace_id).scalar()
    )
    return None if value is None else str(value)


def empty_job_retry_result() -> dict:
    """没有当前任务时的重试响应（形状与 :func:`retry_job_users` 完全一致）。

    ``current`` 版重试在"没有当前任务"时**不是错误**：调用方只是点了一个按钮，
    而那时已经没有任何任务需要重试（从未重算，或当前任务已成功）。报 404 会让前端
    把"没什么可重试"当成失败去弹错，而 :func:`retry_job_users` 早就把"有任务但
    没有终态失败行"定成 ``retried: 0`` 的非错误语义——两者都是"没重试任何行"，
    不该一个报错一个不报。

    这也是 ``current`` 三个端点统一的口径：``GET /current`` 返回 ``{}``、
    ``GET /current/end_users`` 返回空分页信封、两个 retry 返回这个空结果。
    ``{job_id}`` 版不受影响——那是调用方**指名**的任务，不存在就该 404。
    """
    return {
        "retried": 0,
        "reopened": False,
        # 没有任务，所以没有状态可报（与列表端点的 job_id: null 同一情形）。
        "job_status": "",
        "end_user_ids": [],
    }


def retry_job_users(
        db: Session,
        *,
        job_id: uuid.UUID,
        end_user_ids: list[str] | None = None,
) -> dict:
    """把终态失败的 end_user 重新排队，并把任务重新打开。

    ``end_user_ids`` 为 ``None`` 表示"该任务下所有终态失败的行"（一键重试）。

    重开任务是必需的，不是可选的：子任务入口会拒绝终态任务
    （``_REEMBED_TERMINAL_STATUSES`` → ``ABORTED``），不重开就点了没反应。重开后
    全部行完成时 :func:`finalize_job_if_complete` 会把它重新收尾为 ``succeeded``。
    """
    job = get_reembed_job(db, job_id)
    if job is None:
        raise BusinessException(
            message="重算任务不存在",
            code=BizCode.NOT_FOUND,
        )
    # 任务的目标模型必须仍是工作空间当前的 embedding 配置。否则重试会往图里写回
    # **过期模型**的向量，而读路径用的是当前模型——那正是整套机制要防的事。
    #
    # 这条校验的前身是"拒绝重试被作废的任务"：作废状态已随 409 守卫失去生产者而
    # 移除，过期任务现在只可能停在 failed 上（见 orchestrator 的扇出自检），所以
    # 判据必须回到"目标模型是否还是当前模型"这个不变量本身。
    current_embedding = _workspace_embedding_config_id(db, job.workspace_id)
    if current_embedding != str(job.new_embedding_config_id):
        raise BusinessException(
            message="该重算任务的目标模型已不是当前工作空间的嵌入模型，无法重试",
            code=BizCode.STATE_CONFLICT,
        )

    users = MemoryReembedJobUser
    stale_before = _stale_before()
    query = db.query(users).filter(
        users.job_id == job_id,
        _display_status_condition(REEMBED_DISPLAY_FAILED, users, stale_before),
    )
    if end_user_ids is not None:
        query = query.filter(users.end_user_id.in_(end_user_ids))
    rows = query.limit(REEMBED_RETRY_BATCH_LIMIT).all()

    if end_user_ids is not None and not rows:
        raise BusinessException(
            message="该用户当前不是终态失败状态（可能仍在进行或已经完成），无需重试",
            code=BizCode.STATE_CONFLICT,
        )
    if not rows:
        return {"retried": 0, "reopened": False, "job_status": job.status, "end_user_ids": []}

    reopened = False
    if job.status == ReembedJobStatus.failed.value:
        job.status = ReembedJobStatus.running.value
        job.finished_at = None
        # 旧失败原因必须清掉：finalize_job_if_complete 里是
        # ``existing_error or (...)``，留着它会让重算成功后仍带着上一轮的失败文案。
        job.error = None
        reopened = True
    db.add(job)

    retried_ids: list[str] = []
    for row in rows:
        row.status = ReembedUserStatus.pending.value
        # 人工介入 = 重新开始：给全新预算，并清掉排队中不该再显示的旧错误。
        row.attempts = 0
        row.last_error = None
        db.add(row)
        retried_ids.append(str(row.end_user_id))
    db.commit()

    # 提交后才动 Redis 与派发：状态已经落库，这两步即便失败也会由对账任务兜底
    # （行是 pending，claimable_end_users 会重新认领它）。
    for end_user_id in retried_ids:
        # 上一轮的占位可能还没到期（TTL 5 分钟），不清掉子任务会被判 already_inflight。
        job_state.clear_end_user_claim(str(job_id), end_user_id)
        dispatch_end_user_job(job_id, end_user_id)

    logger.info(
        "[MemoryReembed] manual retry: job=%s requeued=%s reopened=%s",
        job_id,
        len(retried_ids),
        reopened,
    )
    return {
        "retried": len(retried_ids),
        "reopened": reopened,
        "job_status": job.status,
        "end_user_ids": retried_ids,
    }


def newer_active_job_exists(
        workspace_id: uuid.UUID,
        created_at,
        job_id: str,
) -> bool:
    """Whether another, newer job is still active for this workspace."""
    with get_db_context() as db:
        count = (
            db.query(func.count(MemoryReembedJob.id))
            .filter(
                MemoryReembedJob.workspace_id == workspace_id,
                MemoryReembedJob.status.in_(_ACTIVE_STATUSES),
                MemoryReembedJob.id != _as_uuid(job_id),
                MemoryReembedJob.created_at > created_at,
            )
            .scalar()
        )
    return bool(count)


def _as_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "REEMBED_DISPLAY_STATUSES",
    "REEMBED_DISPLAY_FAILED",
    "REEMBED_DISPLAY_QUEUED",
    "REEMBED_DISPLAY_RUNNING",
    "REEMBED_DISPLAY_SUCCEEDED",
    "REEMBED_MAX_ATTEMPTS",
    "REEMBED_RETRY_BATCH_LIMIT",
    "display_status",
    "get_current_reembed_job",
    "get_reembed_job",
    "list_job_end_users",
    "retry_job_users",
    "REEMBED_TASK_NAME",
    "REEMBED_TASK_QUEUE",
    "build_reembed_job_payload",
    "claimable_end_users",
    "create_reembed_job_async",
    "REEMBED_RECONCILE_TASK_NAME",
    "REEMBED_USER_TASK_NAME",
    "dispatch_end_user_job",
    "dispatch_reembed_job",
    "ensure_job_users",
    "fail_job_user",
    "finalize_job_if_complete",
    "get_job_user",
    "get_current_reembed_job_async",
    "get_reembed_job_async",
    "get_active_reembed_job_id",
    "get_active_reembed_job_id_async",
    "is_user_terminal",
    "mark_job_failed",
    "newer_active_job_exists",
    "refresh_job_counters",
    "release_job_user",
    "retry_exhausted_condition",
    "should_degrade_semantic",
    "start_job_user",
    "summarize_job_users",
    "finish_job_user",
    "touch_job_user",
]
