"""记忆萃取异常告警的幂等标识和安全上报封装。"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Optional

from sqlalchemy import text

from app.db import get_db_read
from app.plugins import get_plugin

from .exceptions import MemoryExtractionBusinessError

logger = logging.getLogger(__name__)

ALERT_METRIC = "memory_extraction_failure"
_ALERT_IDEMPOTENCY_NAMESPACE = "memory-extraction-failure"


def _build_alert_identity(
    *, workspace_id: str, end_user_id: str, source: str, memory_message_id: str
) -> tuple[str, str]:
    """由落库消息生成 exact 幂等值和不可逆 12 位用户请求编号。"""
    values = (workspace_id, end_user_id, source, memory_message_id)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("alert identity requires non-blank components")
    extraction_request_id = f"{workspace_id}:{end_user_id}:{source}:{memory_message_id}"
    digest = hashlib.sha256(
        (_ALERT_IDEMPOTENCY_NAMESPACE + ":" + extraction_request_id).encode("utf-8")
    ).hexdigest()
    return digest, "ME-" + digest[:12].upper()


def _verified_alert_context(
    workspace_id: str, end_user_id: str
) -> Optional[tuple[uuid.UUID, str, str]]:
    """联合校验归属，返回租户、空间名称和完整 end-user ID。"""
    workspace_uuid = uuid.UUID(str(workspace_id))
    end_user_uuid = uuid.UUID(str(end_user_id))
    with get_db_read() as db:
        row = db.execute(
            text(
                """
                SELECT w.tenant_id, w.name
                FROM workspaces w
                JOIN tenants t ON t.id = w.tenant_id
                JOIN end_users eu ON eu.workspace_id = w.id
                WHERE w.id = :workspace_id
                  AND eu.id = :end_user_id
                  AND w.is_active = true
                  AND t.is_active = true
                  AND eu.is_active = true
                LIMIT 1
                """
            ),
            {"workspace_id": str(workspace_uuid), "end_user_id": str(end_user_uuid)},
        ).first()
    if row is None:
        return None
    return row[0], str(row[1]), str(end_user_uuid)


def emit_memory_extraction_alert_safely(
    *,
    error: MemoryExtractionBusinessError,
    memory_message_id: str,
    workspace_id: str,
    end_user_id: str,
    source: str,
    task_id: str = "",
) -> bool:
    """安全上报失败或结果降级；通知异常不改变原业务结果。"""
    try:
        if not memory_message_id:
            logger.error(
                "[MemoryExtractionAlert] task has no stable memory_message_id; "
                "alert skipped task_id=%s error_code=%s",
                task_id,
                error.code,
            )
            return False
        idempotency_key, request_ref = _build_alert_identity(
            workspace_id=workspace_id,
            end_user_id=end_user_id,
            source=source,
            memory_message_id=memory_message_id,
        )
        alert_context = _verified_alert_context(workspace_id, end_user_id)
        if alert_context is None:
            logger.error(
                "[MemoryExtractionAlert] ownership verification failed; alert skipped "
                "task_id=%s request_ref=%s",
                task_id,
                request_ref,
            )
            return False
        tenant_id, workspace_name, affected_user = alert_context

        alert_engine = get_plugin("alert_engine")
        if alert_engine is None:
            logger.error(
                "[MemoryExtractionAlert] alert_engine unavailable; alert skipped "
                "task_id=%s request_ref=%s",
                task_id,
                request_ref,
            )
            return False

        result = alert_engine.emit(
            metric=ALERT_METRIC,
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            trigger_value={
                "failure_count": 1,
                "error_code": error.code,
                "stage": error.stage,
                "impact": error.impact,
                "retryable": error.retryable,
                "model_type": error.model_type,
                "source": source,
                "workspace_name": workspace_name,
                "affected_user": affected_user,
            },
        )
        logger.error(
            "[MemoryExtractionAlert] business anomaly reported task_id=%s request_ref=%s "
            "error_code=%s created=%s",
            task_id,
            request_ref,
            error.code,
            result.created,
        )
        return True
    except Exception:
        logger.exception(
            "[MemoryExtractionAlert] alert reporting failed without changing extraction result "
            "task_id=%s error_code=%s",
            task_id,
            error.code,
        )
        return False
