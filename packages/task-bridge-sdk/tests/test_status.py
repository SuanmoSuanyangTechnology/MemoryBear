"""TaskStatus semantics, pinned without Celery or a result backend."""

from __future__ import annotations

from task_bridge import READY_STATES, TERMINAL_STATES, TaskState, TaskStatus


def test_success_status():
    status = TaskStatus.from_meta("t1", {"status": "SUCCESS", "result": 3})

    assert status.state == TaskState.SUCCESS
    assert status.successful
    assert status.ready
    assert status.terminal
    assert not status.failed
    assert status.result == 3
    assert status.error is None


def test_failure_keeps_the_exception():
    exc = RuntimeError("kaput")
    status = TaskStatus.from_meta(
        "t1", {"status": "FAILURE", "result": exc, "traceback": "tb"}
    )

    assert status.failed
    assert status.ready
    assert status.exception is exc
    assert status.result is None
    assert status.error == "RuntimeError: kaput"
    assert status.traceback == "tb"


def test_failure_without_text_uses_the_exception_type():
    status = TaskStatus.from_meta("t1", {"status": "FAILURE", "result": RuntimeError()})

    assert status.error == "RuntimeError"


def test_failure_with_string_payload():
    status = TaskStatus.from_meta("t1", {"status": "FAILURE", "result": "denied"})

    assert status.exception is None
    assert status.result == "denied"
    assert status.error == "denied"


def test_revoked_is_ready_but_not_failed():
    status = TaskStatus.from_meta("t1", {"status": "REVOKED", "result": None})

    assert status.revoked
    assert status.ready
    assert status.terminal
    assert not status.failed
    assert status.error is None


def test_pending_and_custom_states_are_not_terminal():
    pending = TaskStatus.from_meta("t1", {"status": "PENDING", "result": None})
    progress = TaskStatus.from_meta("t1", {"status": "PROGRESS", "result": {"p": 10}})

    assert pending.pending
    assert not pending.ready
    assert not pending.terminal
    assert not progress.terminal
    assert progress.result == {"p": 10}
    assert progress.state == "PROGRESS"


def test_ignored_is_terminal_but_not_ready():
    status = TaskStatus.from_meta("t1", {"status": "IGNORED", "result": None})

    assert status.terminal
    assert not status.ready


def test_missing_meta_is_pending():
    status = TaskStatus.from_meta("t1", None)

    assert status.state == TaskState.PENDING
    assert status.pending


def test_state_sets_match_celery():
    assert READY_STATES == {"SUCCESS", "FAILURE", "REVOKED"}
    assert TERMINAL_STATES == {"SUCCESS", "FAILURE", "REVOKED", "IGNORED"}
