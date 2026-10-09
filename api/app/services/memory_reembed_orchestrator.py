from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from app.core.logging_config import get_logger
from app.core.utils.datetime_utils import utcnow_naive
from app.db import get_db_context, get_db_read

logger = get_logger(__name__)


_REEMBED_PENDING = "pending"
_REEMBED_RUNNING = "running"
_REEMBED_ACTIVE_STATUSES = (_REEMBED_PENDING, _REEMBED_RUNNING)
_REEMBED_TERMINAL_STATUSES = ("succeeded", "failed")
# pending 超过该时长仍未开跑，视为派发丢失（broker/worker 当时不可用）。
_REEMBED_PENDING_REDISPATCH_SECONDS = 5 * 60


def _reembed_job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    """把 job 行读成普通 dict，避免 ORM 实例逃出 session。"""
    from app.models.memory_reembed_job_model import MemoryReembedJob

    try:
        job_uuid = uuid.UUID(str(job_id))
    except (TypeError, ValueError):
        return None
    with get_db_read() as db:
        job = (
            db.query(MemoryReembedJob)
            .filter(MemoryReembedJob.id == job_uuid)
            .first()
        )
        if job is None:
            return None
        return {
            "id": str(job.id),
            "workspace_id": job.workspace_id,
            "tenant_id": job.tenant_id,
            "status": job.status,
            "new_embedding_config_id": job.new_embedding_config_id,
            "created_at": job.created_at,
        }


def _reembed_workspace_targets_model(
        workspace_id,
        target_config_id: str,
) -> bool:
    """工作空间当前 embedding 槽位是否仍是本任务的目标模型。"""
    from app.models.workspace_model import Workspace

    with get_db_read() as db:
        current = (
            db.query(Workspace.embedding)
            .filter(Workspace.id == workspace_id)
            .scalar()
        )
    return current is not None and str(current) == str(target_config_id)


def _reembed_is_current_job(snapshot: Dict[str, Any]) -> bool:
    """本任务是否仍是该工作空间该做的那个任务。

    两条判据缺一不可：
    - 工作空间仍指向本任务的目标模型（否则写入的就是过期模型的向量）；
    - 没有更新的活动任务（并发切换可能留下两个都未被作废的任务，新的说了算）。
    """
    from app.services.memory_reembed_service import newer_active_job_exists

    if not _reembed_workspace_targets_model(
        snapshot["workspace_id"],
        snapshot["new_embedding_config_id"],
    ):
        return False
    return not newer_active_job_exists(
        snapshot["workspace_id"],
        snapshot["created_at"],
        snapshot["id"],
    )


async def _reembed_probe_dimension(
        snapshot: Dict[str, Any],
) -> tuple[Any, Optional[str], Optional[str]]:
    """构造 embedder 并探测其向量维度。

    :return: ``(embedder, None, None)`` 可用；``(None, 原因, failure_reason_code)``
        维度不受索引支持或探测本身失败。不受支持的维度若等到写入才暴露，配置
        已经生效：ES 投影会对每个节点抛错，而图侧写入是成功的，两边就此不一致。
        因此在写任何向量之前先拦掉。

        第三项交给通知中心做原因分类，见
        :mod:`app.core.memory.alerts`：探测失败（模型不可用）与维度
        不受支持（配置本身有问题）对运维是两件事，处置方式不同。
    """
    from app.core.memory.pipelines.base_pipeline import ModelClientMixin
    from app.core.memory.alerts import (
        EMBEDDING_PROBE_FAILED,
        UNSUPPORTED_EMBEDDING_DIMENSION,
    )
    from app.core.memory.storage.provider.elasticsearch.index.definitions import (
        EMBEDDING_DIMENSIONS,
        is_supported_embedding_dimension,
    )

    with get_db_context() as db:
        embedder = ModelClientMixin.get_embedding_client(
            db,
            uuid.UUID(str(snapshot["new_embedding_config_id"])),
            snapshot["tenant_id"],
        )
    try:
        vectors = await embedder.aembed_documents(["dimension probe"])
    except Exception as exc:
        return None, f"embedding probe failed: {exc}", EMBEDDING_PROBE_FAILED
    dimension = len(vectors[0]) if vectors and vectors[0] is not None else 0
    if not is_supported_embedding_dimension(dimension):
        return None, (
            f"unsupported embedding dimension {dimension}; "
            f"expected one of {EMBEDDING_DIMENSIONS}"
        ), UNSUPPORTED_EMBEDDING_DIMENSION
    return embedder, None, None


async def run_job(job_id: str, owner: str) -> Dict[str, Any]:
    """驱动任务：自检 → 排空 → 枚举 end_user → 逐个扇出重算子任务。

    可重复执行：已终态的 end_user 由 PG 行状态跳过，并发扇出由派发占位拦住，
    因此崩溃或超时后重新派发即可从断点继续。

    Args:
        job_id: memory_reembed_jobs.id
    """
    from app.core.memory.storage_services.reembedding_engine import job_state
    from app.core.memory.storage_services.reembedding_engine.inventory import (
        inventory_end_users,
    )
    from app.models.memory_reembed_job_model import MemoryReembedJob
    from app.models.end_user_model import EndUser
    from app.services import memory_reembed_service
    from sqlalchemy import func

    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        snapshot = _reembed_job_snapshot(job_id)
        if snapshot is None:
            logger.warning("[MemoryReembed] job not found: job=%s", job_id)
            return {"status": "MISSING", "job_id": job_id}
        if snapshot["status"] in _REEMBED_TERMINAL_STATUSES:
            return {
                "status": "ALREADY_TERMINAL",
                "job_status": snapshot["status"],
            }

        # 自检：发现自己已经不是该工作空间该做的任务就停在终态并退出。否则败者会
        # 永远停在 running，被对账任务反复重派（僵尸循环）。
        #
        # ``notify=False``：这里什么都没失败（一次写入都没发生），不应给用户发失败
        # 告警。终端状态只剩 failed 一个可用值，原委写进 error 供排查。
        if not _reembed_is_current_job(snapshot):
            memory_reembed_service.mark_job_failed(
                job_id,
                error=(
                    "本任务已不再对应当前工作空间的嵌入模型（或已有更新的在途任务），"
                    "因此未执行重建"
                ),
                notify=False,
            )
            logger.info(
                "[MemoryReembed] job stopped itself at fan-out (no longer current): "
                "job=%s",
                job_id,
            )
            return {"status": "OBSOLETE", "job_id": job_id}

        holder = job_state.claim_job(job_id, owner)
        if holder != owner:
            return {"status": "ALREADY_RUNNING", "owner": holder}

        # 写入前先确认新模型的维度受索引支持，避免写出图与 ES 不一致的数据。
        _embedder, dimension_error, failure_reason = await _reembed_probe_dimension(
            snapshot
        )
        if dimension_error is not None:
            memory_reembed_service.mark_job_failed(
                job_id,
                error=dimension_error,
                reason_code=failure_reason,
            )
            logger.error(
                "[MemoryReembed] embedding dimension rejected: job=%s error=%s",
                job_id,
                dimension_error,
            )
            return {"status": "REJECTED", "error": dimension_error}

        # 只取 id 一列：避免把大工作空间下所有 end_user 行拉进 identity map。
        with get_db_context() as db:
            active_end_user_ids = [
                str(row[0])
                for row in db.query(EndUser.id)
                .filter(
                    EndUser.workspace_id == snapshot["workspace_id"],
                    EndUser.is_active.is_(True),
                )
                .all()
            ]
            active_end_users = len(active_end_user_ids)
            db.query(MemoryReembedJob).filter(
                MemoryReembedJob.id == uuid.UUID(job_id),
                MemoryReembedJob.status.in_(_REEMBED_ACTIVE_STATUSES),
            ).update(
                {
                    "status": _REEMBED_RUNNING,
                    # 重复派发不覆盖首次开始时间。
                    "started_at": func.coalesce(
                        MemoryReembedJob.started_at,
                        utcnow_naive(),
                    ),
                },
                synchronize_session=False,
            )
            db.commit()

        node_counts = await inventory_end_users(active_end_user_ids)

        if not node_counts:
            memory_reembed_service.refresh_job_counters(job_id)
            status = memory_reembed_service.finalize_job_if_complete(
                job_id,
                allow_empty=True,
            )
            logger.info(
                "[MemoryReembed] nothing to rebuild: job=%s end_users=%s "
                "job_status=%s",
                job_id,
                active_end_users,
                status,
            )
            return {
                "status": "NOTHING_TO_DO",
                "total_end_users": 0,
                "dispatched": 0,
            }

        job_state.touch_job_heartbeat(job_id)
        memory_reembed_service.ensure_job_users(job_id, node_counts)
        pending = memory_reembed_service.claimable_end_users(job_id)
        for end_user_id in pending:
            memory_reembed_service.dispatch_end_user_job(job_id, end_user_id)

        logger.info(
            "[MemoryReembed] fan-out done: job=%s active_end_users=%s "
            "with_nodes=%s dispatches=%s",
            job_id,
            active_end_users,
            len(node_counts),
            len(pending),
        )
        return {
            "status": "DISPATCHED",
            "total_end_users": len(node_counts),
            "dispatched": len(pending),
        }

    try:
        result = await _run()
    finally:
        # 占位只护住扇出；释放后对账任务才能重新派发进来补漏。
        job_state.release_job_claim(job_id, owner)
    result["job_id"] = job_id
    result["elapsed_time"] = time.time() - start_time
    return result


def _record_crashed_end_user(
        job_id: str,
        end_user_id: str,
        attempts: int,
        exc: BaseException,
) -> None:
    from app.core.memory.storage_services.reembedding_engine import job_state
    from app.services import memory_reembed_service

    error = f"unhandled {type(exc).__name__}: {exc}"
    try:
        recorded = memory_reembed_service.fail_job_user(
            job_id,
            end_user_id,
            attempts=attempts,
            error=error,
        )
        final = memory_reembed_service.finalize_job_if_complete(job_id)
    except Exception:
        logger.error(
            "[MemoryReembed] could not record a crashed attempt: job=%s "
            "end_user=%s attempts=%s error=%s",
            job_id,
            end_user_id,
            attempts,
            error,
            exc_info=True,
        )
        return
    if recorded and final is None:
        # 同 process_end_user：这次尝试已经结束，不会再有续页去续期心跳，
        # 别让残留的心跳把重试挡满一个 TTL。
        job_state.clear_job_heartbeat(job_id)
    logger.error(
        "[MemoryReembed] end_user attempt crashed: job=%s end_user=%s "
        "attempts=%s recorded=%s job_status=%s error=%s",
        job_id,
        end_user_id,
        attempts,
        recorded,
        final,
        error,
        exc_info=True,
    )


async def process_end_user(
        job_id: str,
        end_user_id: str,
        owner: str,
) -> Dict[str, Any]:
    """重算单个 end_user 下全部带向量节点的向量。

    写入走 Neo4j（权威）+ outbox 投影：向量回写到图中不带维度后缀的属性上，
    ES 侧由既有的维度路由与整档替换自行收敛。
    """
    from app.core.memory.exceptions import JobSupersededError
    from app.core.memory.memory_service import MemoryService
    from app.core.memory.pipelines.base_pipeline import ModelClientMixin
    from app.core.memory.storage_services.reembedding_engine import job_state
    from app.models.memory_reembed_job_model import ReembedUserStatus
    from app.services import memory_reembed_service

    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        snapshot = _reembed_job_snapshot(job_id)
        if snapshot is None:
            return {"status": "MISSING", "job_id": job_id}
        if snapshot["status"] in _REEMBED_TERMINAL_STATUSES:
            return {
                "status": "ABORTED",
                "reason": f"job_{snapshot['status']}",
            }
        row = memory_reembed_service.get_job_user(job_id, end_user_id)
        if row is None:
            return {"status": "SKIPPED", "reason": "no_job_user_row"}
        if row["status"] == ReembedUserStatus.done.value:
            return {"status": "SKIPPED", "reason": "already_done"}
        if memory_reembed_service.is_user_terminal(
            row["status"],
            row["attempts"],
        ):
            # Done, or out of retry budget: leave the row as it is.
            return {"status": "SKIPPED", "reason": "user_terminal"}

        if not job_state.claim_end_user(job_id, end_user_id, owner):
            return {"status": "SKIPPED", "reason": "already_inflight"}

        attempts = memory_reembed_service.start_job_user(job_id, end_user_id)
        if attempts == 0:
            # Row is not in a claimable state (another worker is on it, or it's done)
            job_state.release_end_user_claim(job_id, end_user_id, owner)
            return {"status": "SKIPPED", "reason": "row_not_claimable"}

        try:
            with get_db_context() as db:
                embedder = ModelClientMixin.get_embedding_client(
                    db,
                    uuid.UUID(str(snapshot["new_embedding_config_id"])),
                    snapshot["tenant_id"],
                )

            def should_continue() -> bool:
                # 每页续期并把心跳往前推：心跳同时也是"占位该活多久"的依据，
                # 失去占位说明已有别的 worker 接手，继续写就会重复且可能覆盖。
                if not job_state.refresh_end_user_claim(
                    job_id,
                    end_user_id,
                    owner,
                ):
                    return False
                job_state.touch_job_heartbeat(job_id)
                # PG 侧续期（CAS）：如果行已被其他 worker 接管（attempts 不匹配），停止工作
                if not memory_reembed_service.touch_job_user(
                    job_id, end_user_id, attempts=attempts
                ):
                    logger.warning(
                        "[MemoryReembed] touch_job_user failed (row taken over): "
                        "job=%s end_user=%s attempts=%s",
                        job_id,
                        end_user_id,
                        attempts,
                    )
                    return False
                return _reembed_workspace_targets_model(
                    snapshot["workspace_id"],
                    snapshot["new_embedding_config_id"],
                )

            try:
                outcome = await MemoryService.reembed_end_user_vectors(
                    embedder=embedder,
                    job_id=job_id,
                    end_user_id=end_user_id,
                    should_continue=should_continue,
                )
            except JobSupersededError:
                memory_reembed_service.release_job_user(
                    job_id, end_user_id, attempts=attempts
                )
                logger.info(
                    "[MemoryReembed] lost ownership of the rebuild, stop writing: "
                    "job=%s end_user=%s attempts=%s",
                    job_id,
                    end_user_id,
                    attempts,
                )
                return {"status": "ABORTED", "reason": "lost_ownership"}

            status = memory_reembed_service.finish_job_user(
                job_id,
                end_user_id,
                attempts=attempts,
                processed_nodes=outcome.processed_nodes,
                failed_nodes=outcome.failed_nodes,
                # 未跑完 → 记失败原因；attempts 未用尽时仍会被重新派发重试。
                error=outcome.error,
            )
            if status is None:
                # 行已被其他 worker 接管（claim 过期、新 worker 递增了 attempts），
                # 我们的结果不应覆盖它的写入。直接返回，不再推进 job 终态。
                logger.warning(
                    "[MemoryReembed] finish_job_user skipped (row taken over): "
                    "job=%s end_user=%s expected_attempts=%s",
                    job_id,
                    end_user_id,
                    attempts,
                )
                return {
                    "status": "SKIPPED",
                    "reason": "row_taken_over",
                    "job_id": job_id,
                    "end_user_id": end_user_id,
                }
            final = memory_reembed_service.finalize_job_if_complete(job_id)
            if status == ReembedUserStatus.failed.value and final is None:
                # 这一页失败了、重试预算还没用尽：本行不会再有续期，但残留的心跳
                # 还会以"还有人在这条 job 上"的姿态挡满一个 TTL，把重试拖到
                # 「心跳 TTL + 对账周期」。主动撤销，交给下一拍对账重派。
                # 仍在跑的其他 end_user 会按页把心跳写回来，故不误伤并发中的工作。
                job_state.clear_job_heartbeat(job_id)

            logger.info(
                "[MemoryReembed] end_user %s: job=%s end_user=%s "
                "processed=%s failed=%s incomplete=%s attempts=%s job_status=%s",
                status,
                job_id,
                end_user_id,
                outcome.processed_nodes,
                outcome.failed_nodes,
                outcome.incomplete,
                attempts,
                final,
            )
            return {
                "status": "SUCCESS" if status == "done" else "INCOMPLETE",
                "user_status": status,
                "processed_nodes": outcome.processed_nodes,
                "failed_nodes": outcome.failed_nodes,
                "attempts": attempts,
                "job_status": final,
            }
        except Exception as exc:
            # 非「失去所有权」的执行异常（客户端构造、DB/Redis/Neo4j、超时……）此前
            # 不留任何痕迹：行停在 running、last_error 不写、job 可能被写成 succeeded。
            # 记账之后照旧抛出，Celery 侧仍然看得到这次失败。
            _record_crashed_end_user(job_id, end_user_id, attempts, exc)
            raise
        finally:
            job_state.release_end_user_claim(job_id, end_user_id, owner)

    result = await _run()
    result["job_id"] = job_id
    result["end_user_id"] = end_user_id
    result["elapsed_time"] = time.time() - start_time
    return result


def reconcile_active_jobs() -> Dict[str, Any]:
    from app.core.memory.storage_services.reembedding_engine import job_state
    from app.models.memory_reembed_job_model import MemoryReembedJob
    from app.services import memory_reembed_service

    start_time = time.time()
    with get_db_read() as db:
        rows = (
            db.query(
                MemoryReembedJob.id,
                MemoryReembedJob.status,
                MemoryReembedJob.created_at,
                MemoryReembedJob.started_at,
            )
            .filter(MemoryReembedJob.status.in_(_REEMBED_ACTIVE_STATUSES))
            .order_by(MemoryReembedJob.created_at.asc())
            .limit(200)
            .all()
        )

    now = utcnow_naive()
    finalized = 0
    redispatched = 0
    skipped = 0
    for job_id, status, created_at, started_at in rows:
        job_id_str = str(job_id)
        if memory_reembed_service.finalize_job_if_complete(job_id_str) is not None:
            finalized += 1
            continue

        heartbeat = job_state.has_job_heartbeat(job_id_str)
        if heartbeat is None:
            # Redis 不可用，无法区分"在跑"与"已停"，不动。
            skipped += 1
            continue
        if heartbeat:
            # 驱动任务或子任务刚续过期，说明仍在推进。
            continue

        reference = started_at or created_at
        age = (now - reference).total_seconds() if reference else 0
        if status == _REEMBED_PENDING and age < _REEMBED_PENDING_REDISPATCH_SECONDS:
            continue
        if memory_reembed_service.dispatch_reembed_job(job_id_str) is not None:
            redispatched += 1
            logger.info(
                "[MemoryReembed] redispatched job=%s status=%s age=%.0fs",
                job_id_str,
                status,
                age,
            )

    return {
        "status": "SUCCESS",
        "active_jobs": len(rows),
        "finalized": finalized,
        "redispatched": redispatched,
        "skipped": skipped,
        "elapsed_time": time.time() - start_time,
    }
