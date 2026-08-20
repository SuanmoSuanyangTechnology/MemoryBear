"""记忆萃取异常的安全异步入队封装。"""
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
        logger.error(
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
        logger.error(
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