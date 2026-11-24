"""Beat schedule owned by application-service (workflow trigger scan).

Replaces the monolith entry of the same key/name once
ENABLE_MONOLITH_WORKFLOW_SCAN=false is set on the monolith deployment.
"""

from __future__ import annotations

from celery.schedules import schedule

from .celery_app import WORKFLOW_TRIGGER_QUEUE, celery_app

SCAN_INTERVAL_SECONDS = 60.0


def build_beat_schedule() -> dict:
    return {
        "scan-workflow-schedule-triggers": {
            "task": "app.tasks.scan_workflow_schedule_triggers",
            "schedule": schedule(run_every=SCAN_INTERVAL_SECONDS),
            "options": {"queue": WORKFLOW_TRIGGER_QUEUE, "expires": 55},
        },
    }


# Replace wholesale: never inherit the monolith schedule in this process.
celery_app.conf.beat_schedule = build_beat_schedule()
