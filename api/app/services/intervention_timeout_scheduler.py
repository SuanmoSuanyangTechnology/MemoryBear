import asyncio
import datetime
import logging

from app.core.utils.datetime_utils import utcnow, utcnow_naive
from app.core.workflow.nodes.human_intervention.node import InterventionRegistry
from app.db import get_db_context

logger = logging.getLogger(__name__)

_SCAN_INTERVAL = 5  # seconds
_task: asyncio.Task | None = None


async def _loop():
    """Background loop that periodically scans for expired human-intervention timeouts.

    Uses dynamic sleep: if the next timeout is within the regular scan interval,
    sleep only until that timeout fires so the frontend countdown and backend
    expiry stay tightly synchronized (max drift ≈ _SCAN_INTERVAL).
    """
    while True:
        try:
            await scan_and_expire_interventions()
        except Exception as e:
            logger.error(f"Intervention timeout scan failed: {e}", exc_info=True)

        # Dynamic sleep: if the earliest timeout_at is closer than
        # _SCAN_INTERVAL, sleep only until that moment.
        next_sleep = _SCAN_INTERVAL
        earliest = _get_earliest_timeout_at()
        if earliest is not None:
            remaining = (earliest - utcnow()).total_seconds()
            if remaining > 0:
                next_sleep = min(_SCAN_INTERVAL, remaining)
            else:
                next_sleep = 0  # already expired, scan immediately

        await asyncio.sleep(next_sleep)


def _get_earliest_timeout_at() -> datetime.datetime | None:
    """Return the earliest timeout_at from InterventionRegistry, or None."""
    entries = InterventionRegistry._timeout_entries
    if not entries:
        return None
    earliest = None
    for entry in entries.values():
        if earliest is None or entry.timeout_at < earliest:
            earliest = entry.timeout_at
    return earliest


def start(interval: int = 5):
    """Start the background timeout scanner. Call once during app startup."""
    global _task, _SCAN_INTERVAL
    _SCAN_INTERVAL = interval
    _task = asyncio.create_task(_loop())
    logger.info(f"Intervention timeout scanner started (interval={interval}s)")


def stop():
    """Stop the background timeout scanner. Call once during app shutdown."""
    global _task
    if _task:
        _task.cancel()
        _task = None
    logger.info("Intervention timeout scanner stopped")


async def scan_and_expire_interventions():
    """Scan all expired intervention timeouts and terminate their workflows.

    Called periodically by the background scheduler started in app lifespan.
    """
    expired = InterventionRegistry.get_expired()
    if not expired:
        return

    logger.info(f"Found {len(expired)} expired intervention entries")

    for entry in expired:
        try:
            await _terminate_timed_out_workflow(entry)
        except Exception as e:
            logger.error(
                f"Timeout terminate failed for execution={entry.execution_id}, "
                f"node={entry.node_id}: {e}",
                exc_info=True,
            )


async def _terminate_timed_out_workflow(entry):
    """Handle a workflow that has timed out waiting for human intervention.

    If an SSE stream is active (signal_timeout succeeds), the run_stream loop
    will resume the workflow via the timeout branch — we only push the signal
    and remove the timeout entry to prevent re-scanning.

    If no SSE stream is listening (process restart, connection dropped), we
    fall back to updating the DB directly and cleaning up everything.
    """
    logger.info(
        f"Handling timed-out workflow: execution={entry.execution_id}, "
        f"node={entry.node_id}"
    )

    # 1. Signal the SSE stream — if active, run_stream will resume via timeout branch
    from app.services.intervention_registry import signal_timeout
    result = signal_timeout(entry.execution_id, entry.node_id)

    # Remove timeout entry so the scan loop doesn't pick this up again.
    InterventionRegistry.remove_timeout(entry.execution_id, entry.node_id)

    if result is not None:
        logger.info(
            f"SSE stream active for {entry.execution_id}, "
            f"run_stream will resume via timeout branch (node={result[0]})"
        )
        return

    # 2. Fallback: no SSE stream listening — update DB directly
    logger.info(
        f"No SSE stream for {entry.execution_id}, updating DB status directly"
    )
    checkpoint_thread_id = ""
    with get_db_context() as db:
        from app.repositories.workflow_repository import WorkflowExecutionRepository
        repo = WorkflowExecutionRepository(db)
        execution = repo.get_by_execution_id(entry.execution_id)

        if not execution:
            logger.warning(f"Execution not found for {entry.execution_id}")
            InterventionRegistry.cleanup(entry.execution_id)
            return

        if execution.status not in ("waiting_human", "running", "pending"):
            logger.info(
                f"Execution {entry.execution_id} already '{execution.status}', skipping"
            )
            InterventionRegistry.cleanup(entry.execution_id)
            return

        timeout_msg = f"人工介入节点 '{entry.node_id}' 超时，工作流已终止"
        execution.status = "timeout"
        execution.error_message = timeout_msg
        execution.error_node_id = entry.node_id
        execution.completed_at = utcnow_naive()
        if execution.started_at:
            execution.elapsed_time = (
                execution.completed_at - execution.started_at
            ).total_seconds()
        # Preserve existing output_data (contains node_outputs from nodes
        # that ran before the intervention node) and add timeout info.
        import copy as _copy
        output_data = _copy.deepcopy(execution.output_data or {})
        node_outputs = output_data.setdefault("node_outputs", {})
        max_exec_order = 0
        for nout in node_outputs.values():
            if isinstance(nout, dict):
                max_exec_order = max(max_exec_order, nout.get("execution_order", 0))
        # Get the real node_name from execution context (the node_id is not the display name)
        intervention_ctx = (execution.context or {}).get("human_intervention", {})
        interventions_list = intervention_ctx.get("interventions") or []
        node_display_name = interventions_list[0].get("node_name", entry.node_id) if interventions_list else entry.node_id
        node_outputs[entry.node_id] = {
            "node_type": "human-intervention",
            "node_name": node_display_name,
            "status": "timeout",
            "input": None,
            "output": None,
            "elapsed_time": None,
            "token_usage": None,
            "error": timeout_msg,
            "execution_order": max_exec_order + 1,
        }
        output_data["status"] = "timeout"
        output_data["output"] = timeout_msg
        output_data["error"] = timeout_msg
        output_data["error_node_id"] = entry.node_id
        output_data["elapsed_time"] = execution.elapsed_time
        execution.output_data = output_data
        db.commit()

        intervention_ctx = (execution.context or {}).get("human_intervention", {})
        checkpoint_thread_id = intervention_ctx.get("checkpoint_thread_id", "")

    # Clean up registries and checkpoint
    InterventionRegistry.cleanup(entry.execution_id)

    if checkpoint_thread_id:
        from app.core.workflow.engine.graph_builder import remove_checkpointer
        remove_checkpointer(checkpoint_thread_id)

    logger.info(
        f"Workflow terminated due to timeout (fallback): execution={entry.execution_id}, "
        f"node={entry.node_id}"
    )
