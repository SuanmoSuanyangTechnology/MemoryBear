"""Workflow schedule trigger tasks, ported from the monolith.

Task names and queues are unchanged (``app.tasks.*`` /
``workflow_trigger_tasks``). The scan runs synchronously on the task thread;
the run task executes the workflow coroutine on the worker's persistent loop
(see async_runtime) while its synchronous session stays on the task thread.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from .async_runtime import run_async
from .celery_app import WORKFLOW_TRIGGER_QUEUE, celery_app
from .db import get_db_context
from .models import App, AppRelease
from .services.workflow_service import WorkflowService
from .utils.datetime_utils import (
    as_utc_aware,
    parse_iso_to_utc_naive,
    to_iso_z,
    utcnow,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.scan_workflow_schedule_triggers",
    queue=WORKFLOW_TRIGGER_QUEUE,
    time_limit=50,
    soft_time_limit=45,
)
def scan_workflow_schedule_triggers() -> dict[str, Any]:
    """扫描并派发已发布工作流中的定时触发器。"""

    now = utcnow()
    triggered = 0

    with get_db_context() as db:
        service = WorkflowService(db)
        due_triggers = service.get_due_schedule_triggers(now)
        logger.info(f"[WorkflowSchedule] 扫描到 {len(due_triggers)} 个待执行触发器")

        for app, release, _config, trigger in due_triggers:
            trigger_id = trigger.get("id")
            try:
                run_workflow_schedule_trigger.apply_async(
                    kwargs={
                        "app_id": str(app.id),
                        "release_id": str(release.id),
                        "trigger_id": trigger_id,
                        "scheduled_at": to_iso_z(now),
                    },
                    queue=WORKFLOW_TRIGGER_QUEUE,
                )
                runtime = {
                    **(trigger.get("runtime") or {}),
                    "dispatch_status": "queued",
                    "last_dispatched_at": to_iso_z(now),
                    "last_scheduled_at": to_iso_z(now),
                    "last_error": None,
                }
                service.update_release_trigger_runtime_state(
                    release.id, trigger_id, runtime
                )
                service.update_trigger_runtime_state(app.id, trigger_id, runtime)
                triggered += 1
                logger.info(
                    f"[WorkflowSchedule] 已派发: app_id={app.id}, "
                    f"release_id={release.id}, trigger_id={trigger_id}"
                )
            except Exception as exc:
                logger.error(
                    f"[WorkflowSchedule] 派发失败: app_id={app.id}, "
                    f"trigger_id={trigger_id}, error={exc}",
                    exc_info=True,
                )

    return {"triggered": triggered, "scanned_at": to_iso_z(now)}


@celery_app.task(
    name="app.tasks.run_workflow_schedule_trigger",
    queue=WORKFLOW_TRIGGER_QUEUE,
)
def run_workflow_schedule_trigger(
    app_id: str,
    release_id: str,
    trigger_id: str,
    scheduled_at: str | None = None,
) -> dict[str, Any]:
    """执行单个已发布的 schedule trigger。"""

    run_at = (
        as_utc_aware(parse_iso_to_utc_naive(scheduled_at))
        if scheduled_at
        else utcnow()
    )
    with get_db_context() as db:
        service = WorkflowService(db)
        app = db.get(App, uuid.UUID(app_id))
        release = db.get(AppRelease, uuid.UUID(release_id))
        if not app or not release:
            logger.warning(
                f"[WorkflowSchedule] 跳过不存在的任务: app_id={app_id}, "
                f"release_id={release_id}, trigger_id={trigger_id}"
            )
            return {"status": "skipped", "reason": "app_or_release_not_found"}

        if app.current_release_id != release.id:
            logger.info(
                f"[WorkflowSchedule] 跳过过期发布版本任务: "
                f"app_id={app_id}, queued_release_id={release_id}, "
                f"current_release_id={app.current_release_id}, "
                f"trigger_id={trigger_id}"
            )
            return {"status": "skipped", "reason": "stale_release"}

        config = service._build_runtime_workflow_config_from_release(
            release,
            real_config_id=(app.workflow_config.id if app.workflow_config else None),
        )
        trigger = service._find_trigger_node(
            config.nodes, trigger_id=trigger_id, trigger_type="schedule"
        )
        if not trigger:
            logger.warning(
                f"[WorkflowSchedule] 跳过不存在的 trigger: trigger_id={trigger_id}"
            )
            return {"status": "skipped", "reason": "trigger_not_found"}

        runtime = trigger.get("runtime") or {}
        running_runtime = {
            **runtime,
            "dispatch_status": "running",
            "last_started_at": to_iso_z(utcnow()),
            "last_scheduled_at": to_iso_z(run_at),
            "last_error": None,
        }
        service.update_release_trigger_runtime_state(
            release.id, trigger_id, running_runtime
        )
        service.update_trigger_runtime_state(app.id, trigger_id, running_runtime)

        try:
            run_async(
                service.invoke_schedule_trigger(
                    app=app,
                    release=release,
                    config=config,
                    trigger=trigger,
                    now=run_at,
                )
            )
            completed_runtime = {
                **running_runtime,
                "dispatch_status": "completed",
                "last_triggered_at": to_iso_z(run_at),
                "last_completed_at": to_iso_z(utcnow()),
                "last_error": None,
            }
            service.update_release_trigger_runtime_state(
                release.id, trigger_id, completed_runtime
            )
            service.update_trigger_runtime_state(app.id, trigger_id, completed_runtime)
            return {
                "status": "completed",
                "trigger_id": trigger_id,
                "scheduled_at": to_iso_z(run_at),
            }
        except Exception as exc:
            failed_runtime = {
                **running_runtime,
                "dispatch_status": "failed",
                "last_failed_at": to_iso_z(utcnow()),
                "last_error": str(exc),
            }
            service.update_release_trigger_runtime_state(
                release.id, trigger_id, failed_runtime
            )
            service.update_trigger_runtime_state(app.id, trigger_id, failed_runtime)
            logger.error(
                f"[WorkflowSchedule] 执行失败: app_id={app_id}, "
                f"release_id={release_id}, trigger_id={trigger_id}, error={exc}",
                exc_info=True,
            )
            raise
