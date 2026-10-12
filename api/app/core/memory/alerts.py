"""记忆模块到企业版通知中心的桥接。"""
from __future__ import annotations

import asyncio
import logging

from app.plugins import get_plugin

from .exceptions import (
    MemoryExtractionBusinessError,
    MemoryRetrievalBusinessError,
)

logger = logging.getLogger(__name__)

_ALERT_ENQUEUE_TIMEOUT_SECONDS = 1.0


async def enqueue_memory_extraction_alert_safely(
    *,
    error: MemoryExtractionBusinessError,
    memory_message_id: str,
    workspace_id: str,
    end_user_id: str,
    source: str,
    task_id: str = "",
) -> bool:
    """建立异步告警义务；任何通知链路异常都不影响萃取结果。"""
    try:
        if not memory_message_id:
            logger.error(
                "[MemoryExtractionAlert] task has no stable memory_message_id; "
                "alert skipped task_id=%s error_code=%s",
                task_id,
                error.code,
            )
            return False
        if error.code not in {
            "MODEL_CALL_FAILED",
            "STRUCTURED_RESULT_PARSE_FAILED",
        } or error.model_type not in {"llm", "embedding", "rerank"}:
            logger.error(
                "[MemoryExtractionAlert] unsupported anomaly; alert skipped "
                "task_id=%s error_code=%s model_type=%s",
                task_id,
                error.code,
                error.model_type,
            )
            return False

        reporter = get_plugin("memory_extraction_failure_reporter")
        if reporter is None:
            logger.error(
                "[MemoryExtractionAlert] reporter unavailable; alert skipped "
                "task_id=%s error_code=%s",
                task_id,
                error.code,
            )
            return False

        result = await asyncio.wait_for(
            reporter.report(
                workspace_id=workspace_id,
                end_user_id=end_user_id,
                operation_id=memory_message_id,
                error_code=error.code,
                stage=error.stage,
                impact=error.impact,
                model_type=error.model_type,
            ),
            timeout=_ALERT_ENQUEUE_TIMEOUT_SECONDS,
        )
        logger.info(
            "[MemoryExtractionAlert] business anomaly enqueued task_id=%s "
            "error_code=%s obligation_id=%s dispatched=%s",
            task_id,
            error.code,
            result.obligation_id,
            result.dispatched,
        )
        return True
    except Exception:
        logger.exception(
            "[MemoryExtractionAlert] alert enqueue failed without changing extraction result "
            "task_id=%s error_code=%s source=%s",
            task_id,
            error.code,
            source,
        )
        return False


async def enqueue_memory_retrieval_alert_safely(
    error: MemoryRetrievalBusinessError,
    *,
    operation_id: str,
    tenant_id: str,
    workspace_id: str,
    end_user_id: str,
) -> bool:
    """建立异步告警义务；任何通知链路异常都不影响检索。"""
    try:
        if error.code not in {
            "MODEL_CALL_FAILED",
            "STRUCTURED_RESULT_PARSE_FAILED",
        } or error.model_type not in {"llm", "embedding", "rerank"}:
            logger.error(
                "[MemoryRetrievalAlert] unsupported anomaly; alert skipped "
                "operation_id=%s error_code=%s model_type=%s",
                operation_id,
                error.code,
                error.model_type,
            )
            return False
        reporter = get_plugin("memory_retrieval_failure_reporter")
        if reporter is None:
            logger.error(
                "[MemoryRetrievalAlert] reporter unavailable; alert skipped "
                "operation_id=%s error_code=%s",
                operation_id,
                error.code,
            )
            return False

        result = await asyncio.wait_for(
            reporter.report(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                end_user_id=end_user_id,
                operation_id=operation_id,
                error_code=error.code,
                stage=error.stage,
                impact=error.impact,
                model_type=error.model_type,
            ),
            timeout=_ALERT_ENQUEUE_TIMEOUT_SECONDS,
        )
        logger.info(
            "[MemoryRetrievalAlert] business anomaly enqueued operation_id=%s "
            "error_code=%s obligation_id=%s dispatched=%s",
            operation_id,
            error.code,
            result.obligation_id,
            result.dispatched,
        )
        return True
    except Exception:
        logger.exception(
            "[MemoryRetrievalAlert] alert enqueue failed without changing retrieval result "
            "operation_id=%s error_code=%s",
            operation_id,
            error.code,
        )
        return False


# ── reembed errors ──────────────────────────────────────────────────────
UNSUPPORTED_EMBEDDING_DIMENSION = "UNSUPPORTED_EMBEDDING_DIMENSION"
EMBEDDING_PROBE_FAILED = "EMBEDDING_PROBE_FAILED"
END_USER_REBUILD_FAILED = "END_USER_REBUILD_FAILED"
WORKER_CRASHED = "WORKER_CRASHED"

REEMBED_FAILURE_REASONS = frozenset({
    UNSUPPORTED_EMBEDDING_DIMENSION,
    EMBEDDING_PROBE_FAILED,
    END_USER_REBUILD_FAILED,
    WORKER_CRASHED,
})


def report_reembed_job_failure_safely(
    *,
    job_id: str,
    workspace_id: str,
    reason_code: str,
    old_model_name: str | None,
    new_model_name: str | None,
    total_end_users: int,
    failed_end_users: int,
    total_nodes: int,
    processed_nodes: int,
    failed_nodes: int,
    error: str | None,
    failed_at_ms: int,
) -> bool:
    """把「任务已永久失败」交给可选的通知中心插件。

    调用方必须**已经提交**终态（见 ``mark_job_failed`` /
    ``finalize_job_if_complete`` 的原子 UPDATE），否则通知会先于事实到达用户。

    :return: 是否成功建立通知义务。``False`` 只说明这次上报没走通（社区版未
        注册插件、原因不在白名单、通知链路故障），任务状态不受影响。
    """
    if reason_code not in REEMBED_FAILURE_REASONS:
        logger.error(
            "[MemoryReembedAlert] unsupported failure reason; alert skipped "
            "job=%s reason_code=%s",
            job_id,
            reason_code,
        )
        return False

    reporter = get_plugin("memory_reembed_failure_reporter")
    if reporter is None:
        # 社区版没有通知中心，静默跳过。
        logger.debug(
            "[MemoryReembedAlert] reporter unavailable; alert skipped job=%s",
            job_id,
        )
        return False

    try:
        reporter.report(
            job_id=job_id,
            workspace_id=workspace_id,
            reason_code=reason_code,
            old_model_name=old_model_name,
            new_model_name=new_model_name,
            total_end_users=int(total_end_users or 0),
            failed_end_users=int(failed_end_users or 0),
            total_nodes=int(total_nodes or 0),
            processed_nodes=int(processed_nodes or 0),
            failed_nodes=int(failed_nodes or 0),
            error=error,
            failed_at_ms=int(failed_at_ms),
        )
    except Exception:
        logger.exception(
            "[MemoryReembedAlert] alert report failed without changing job state "
            "job=%s reason_code=%s",
            job_id,
            reason_code,
        )
        return False

    logger.info(
        "[MemoryReembedAlert] job failure handed to notification center "
        "job=%s reason_code=%s",
        job_id,
        reason_code,
    )
    return True


def report_reembed_job_completion_safely(
    *,
    job_id: str,
    workspace_id: str,
    old_model_name: str | None,
    new_model_name: str | None,
    total_end_users: int,
    total_nodes: int,
    processed_nodes: int,
    completed_at_ms: int,
) -> bool:
    """把「任务已成功完成」交给可选的通知中心插件。

    与 :func:`report_reembed_job_failure_safely` 成对：同一个任务只会走其中一条。
    调用方必须**已经提交**终态（见 ``finalize_job_if_complete`` 的原子 UPDATE），
    否则通知会先于事实到达用户。

    没有 reason code 白名单——完成只有一种结局。

    :return: 是否成功建立通知义务。``False`` 只说明这次上报没走通（社区版未
        注册插件、身份缺失、通知链路故障），任务状态不受影响。
    """
    if not job_id or not workspace_id:
        logger.error(
            "[MemoryReembedAlert] completion without identity; alert skipped "
            "job=%s workspace=%s",
            job_id,
            workspace_id,
        )
        return False

    reporter = get_plugin("memory_reembed_success_reporter")
    if reporter is None:
        # 社区版没有通知中心，静默跳过。
        logger.debug(
            "[MemoryReembedAlert] completion reporter unavailable; alert skipped "
            "job=%s",
            job_id,
        )
        return False

    try:
        reporter.report(
            job_id=job_id,
            workspace_id=workspace_id,
            old_model_name=old_model_name,
            new_model_name=new_model_name,
            total_end_users=int(total_end_users or 0),
            total_nodes=int(total_nodes or 0),
            processed_nodes=int(processed_nodes or 0),
            completed_at_ms=int(completed_at_ms),
        )
    except Exception:
        logger.exception(
            "[MemoryReembedAlert] completion report failed without changing job "
            "state job=%s",
            job_id,
        )
        return False

    logger.info(
        "[MemoryReembedAlert] job completion handed to notification center job=%s",
        job_id,
    )
    return True