"""Celery worker loader and prefork lifecycle hooks."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from celery.signals import (
    celeryd_after_setup,
    task_failure,
    task_postrun,
    task_prerun,
    task_received,
    task_retry,
    worker_process_init,
    worker_process_shutdown,
    worker_ready,
    worker_shutdown,
    worker_shutting_down,
)
from celery.signals import (
    setup_logging as celery_setup_logging,
)
from celery.worker.state import active_requests

from ..bootstrap import get_settings
from ..logging import setup_logging as setup_knowledge_logging
from ..runtime import reset_worker_runtime_after_fork, shutdown_worker_runtime_sync
from . import document, evidence_graph, legacy_compat, qa_import
from .celery_app import celery_app
from .observability import record_task_prerun, reset_observability_after_fork
from .worker_health import (
    WorkerPhase,
    build_worker_state,
    remove_worker_state_if_owned,
    transition_worker_state,
    write_worker_state,
)

logger = logging.getLogger(__name__)

ROLE_QUEUE = {
    "document_worker": "document_tasks",
    "graphrag_worker": "graphrag_tasks",
    "qa_import_worker": "qa_import",
}


class WorkerConfigurationError(RuntimeError):
    """The declared worker role does not match its active queues."""


def validate_worker_role_queues(role: str, queues: set[str]) -> None:
    """Require each knowledge worker role to consume exactly one queue."""

    expected = ROLE_QUEUE.get(role)
    if expected is None or queues != {expected}:
        raise WorkerConfigurationError(
            f"role={role} queues={sorted(queues)} expected={expected or 'none'}"
        )


def _active_queue_names(app: object) -> set[str]:
    queues = getattr(getattr(app, "amqp", None), "queues", {})
    consume_from = getattr(queues, "consume_from", queues)
    return set(consume_from)


def _health_state_file() -> Path:
    return get_settings().kb_worker_health_state_file


def _log_health_state_failure(
    *,
    event: str,
    phase: WorkerPhase,
    error: Exception,
) -> None:
    try:
        role = get_settings().kb_process_role
    except Exception:
        role = "unknown"
    logger.warning(
        "event=%s role=%s pid=%s phase=%s error_type=%s",
        event,
        role,
        os.getpid(),
        phase,
        type(error).__name__,
    )


def _publish_initial_health_state(*, role: str, queues: set[str]) -> None:
    try:
        state = build_worker_state(
            role=role,
            queues=queues,
            phase=WorkerPhase.STARTING,
        )
        write_worker_state(_health_state_file(), state)
    except Exception as error:
        _log_health_state_failure(
            event="kb_worker_health_state_write_failed",
            phase=WorkerPhase.STARTING,
            error=error,
        )


def _transition_health_state(phase: WorkerPhase) -> None:
    try:
        transition_worker_state(_health_state_file(), phase)
    except Exception as error:
        _log_health_state_failure(
            event="kb_worker_health_state_transition_failed",
            phase=phase,
            error=error,
        )


def _remove_health_state() -> None:
    try:
        remove_worker_state_if_owned(_health_state_file())
    except Exception as error:
        _log_health_state_failure(
            event="kb_worker_health_state_cleanup_failed",
            phase=WorkerPhase.STOPPING,
            error=error,
        )


@celeryd_after_setup.connect
def validate_worker_configuration(
    *,
    sender: object,
    instance: object,
    **kwargs: object,
) -> None:
    """Fail before consumption when the declared role and queues differ."""

    del kwargs
    queues = _active_queue_names(instance.app)
    role = get_settings().kb_process_role
    try:
        validate_worker_role_queues(role, queues)
    except WorkerConfigurationError:
        logger.critical(
            "event=kb_worker_configuration_invalid role=%s queues=%s hostname=%s",
            role,
            ",".join(sorted(queues)),
            sender,
        )
        raise SystemExit(78) from None
    _publish_initial_health_state(role=role, queues=queues)


@celery_setup_logging.connect
def configure_worker_logging(**kwargs: object) -> None:
    """Install the same redacting formatter used by the Knowledge API."""

    del kwargs
    setup_knowledge_logging(get_settings())


@worker_ready.connect
def handle_worker_ready(sender: object, **kwargs: object) -> None:
    """Report the resolved runtime shape once the consumer is ready."""

    del kwargs
    _transition_health_state(WorkerPhase.READY)
    app = getattr(sender, "app", celery_app)
    queue_names = sorted(_active_queue_names(app))
    controller = getattr(sender, "controller", None)
    pool = getattr(controller, "pool", None)
    logger.info(
        "event=kb_worker_ready role=%s hostname=%s main_pid=%s queues=%s "
        "pool=%s concurrency=%s prefetch_multiplier=%s",
        get_settings().kb_process_role,
        getattr(sender, "hostname", "unknown"),
        os.getpid(),
        ",".join(queue_names),
        type(pool).__name__ if pool is not None else "unknown",
        getattr(controller, "concurrency", "unknown"),
        get_settings().kb_worker_prefetch_multiplier,
    )


@worker_shutting_down.connect
def handle_worker_shutting_down(
    sender: object,
    sig: str,
    how: str,
    exitcode: int,
    **kwargs: object,
) -> None:
    """Report why the worker main process is stopping."""

    del kwargs
    _transition_health_state(WorkerPhase.STOPPING)
    logger.info(
        "event=kb_worker_shutdown_requested role=%s hostname=%s main_pid=%s "
        "signal=%s mode=%s exitcode=%s active_tasks=%s",
        get_settings().kb_process_role,
        sender,
        os.getpid(),
        sig,
        how,
        exitcode,
        len(active_requests),
    )


@worker_shutdown.connect
def handle_worker_shutdown(
    sender: object | None = None,
    **kwargs: object,
) -> None:
    """Remove local health state owned by the exiting worker main process."""

    del sender, kwargs
    _remove_health_state()


@worker_process_init.connect
def initialize_worker_process(**kwargs: object) -> None:
    """Create fresh lazy resource owners after prefork."""

    started_at = time.perf_counter()
    del kwargs
    reset_observability_after_fork()
    reset_worker_runtime_after_fork()
    logger.info(
        "event=kb_worker_child_ready role=%s pid=%s init_duration_ms=%s",
        get_settings().kb_process_role,
        os.getpid(),
        int((time.perf_counter() - started_at) * 1000),
    )


@worker_process_shutdown.connect
def shutdown_worker_process(**kwargs: object) -> None:
    """Release resources owned by the exiting worker process."""

    started_at = time.perf_counter()
    exitcode = kwargs.get("exitcode")
    shutdown_worker_runtime_sync()
    logger.info(
        "event=kb_worker_child_stopped role=%s pid=%s exitcode=%s cleanup_duration_ms=%s",
        get_settings().kb_process_role,
        os.getpid(),
        exitcode if exitcode is not None else "unknown",
        int((time.perf_counter() - started_at) * 1000),
    )


@task_received.connect
def handle_task_received(sender: object, request: object | None = None, **kwargs: object) -> None:
    """Report broker receipt in the worker main process without task payloads."""

    del kwargs
    request = request or sender
    delivery_info = getattr(request, "delivery_info", None)
    routing_key = delivery_info.get("routing_key") if isinstance(delivery_info, dict) else None
    logger.info(
        "event=kb_task_received role=%s main_pid=%s task_name=%s task_id=%s queue=%s",
        get_settings().kb_process_role,
        os.getpid(),
        getattr(request, "name", "unknown"),
        getattr(request, "id", "unknown"),
        routing_key or "unknown",
    )


@task_prerun.connect
def handle_task_prerun(
    sender: object,
    task_id: str,
    **kwargs: object,
) -> None:
    """Capture child start before the task envelope performs lazy imports."""

    del sender, kwargs
    record_task_prerun(str(task_id))


@task_postrun.connect
def handle_task_postrun(
    sender: object,
    task_id: str,
    state: str | None = None,
    **kwargs: object,
) -> None:
    """Report Celery's terminal state separately from business outcome."""

    del kwargs
    logger.info(
        "event=kb_task_postrun role=%s pid=%s task_name=%s task_id=%s "
        "celery_outcome=%s",
        get_settings().kb_process_role,
        os.getpid(),
        getattr(sender, "name", "unknown"),
        task_id,
        str(state or "unknown").lower(),
    )


@task_retry.connect
def handle_task_retry(
    sender: object,
    request: object | None = None,
    reason: BaseException | None = None,
    **kwargs: object,
) -> None:
    """Report retries without logging exception messages or task payloads."""

    del kwargs
    logger.warning(
        "event=kb_task_retry role=%s pid=%s task_name=%s task_id=%s "
        "error_type=%s attempt=%s",
        get_settings().kb_process_role,
        os.getpid(),
        getattr(sender, "name", "unknown"),
        getattr(request, "id", "unknown"),
        type(reason).__name__ if reason is not None else "unknown",
        getattr(request, "retries", 0),
    )


@task_failure.connect
def handle_task_failure(
    sender: object,
    task_id: str,
    exception: BaseException | None = None,
    **kwargs: object,
) -> None:
    """Report task failures without serializing exception messages."""

    del kwargs
    logger.error(
        "event=kb_task_failure role=%s pid=%s task_name=%s task_id=%s error_type=%s",
        get_settings().kb_process_role,
        os.getpid(),
        getattr(sender, "name", "unknown"),
        task_id,
        type(exception).__name__ if exception is not None else "unknown",
    )


__all__ = [
    "celery_app",
    "document",
    "evidence_graph",
    "legacy_compat",
    "qa_import",
    "WorkerConfigurationError",
    "handle_task_postrun",
    "handle_worker_shutdown",
    "handle_worker_shutting_down",
    "validate_worker_configuration",
    "validate_worker_role_queues",
]
