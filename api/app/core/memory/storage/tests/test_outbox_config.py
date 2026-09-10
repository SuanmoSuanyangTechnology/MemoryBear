"""Validate only the relevant settings expressions, without loading deployment secrets."""

import ast
import asyncio
import logging
import os
import socket
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Annotated
from unittest.mock import AsyncMock

import pytest
from celery import Celery
from pydantic import Field, TypeAdapter, ValidationError

from app.core.memory.storage.outbox.exceptions import safe_error

API = Path(__file__).resolve().parents[5]
BOUNDS = {
    "OUTBOX_SCAN_INTERVAL_SECONDS": (5, 1, 3600),
    "OUTBOX_BATCH_SIZE": (100, 1, 1000),
    "OUTBOX_PROCESSING_TIMEOUT_SECONDS": (300, 30, 3600),
    "OUTBOX_RETENTION_DAYS": (30, 1, 3650),
    "OUTBOX_FAILED_RETENTION_DAYS": (60, 1, 3650),
    "OUTBOX_CLEANUP_HOUR": (18, 0, 23),
    "OUTBOX_ERROR_MAX_LENGTH": (4096, 64, 16384),
}


def load_outbox_tasks():
    source = ast.parse((API / "app/tasks.py").read_text())
    names = {"scan_outbox_projection", "cleanup_outbox"}
    definitions = [
        node
        for node in source.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ModuleType("isolated_outbox_tasks")
    module.__dict__.update(
        asyncio=asyncio,
        celery_app=Celery("isolated_outbox_tasks"),
        cleanup_outbox_events=AsyncMock(),
        consume_outbox_batch=AsyncMock(),
        logger=logging.getLogger("isolated_outbox_tasks"),
        os=os,
        safe_error=safe_error,
        settings=SimpleNamespace(
            OUTBOX_BATCH_SIZE=100,
            OUTBOX_ERROR_MAX_LENGTH=4096,
        ),
        socket=socket,
        uuid=uuid,
    )
    exec(
        compile(ast.Module(body=definitions, type_ignores=[]), "app/tasks.py", "exec"),
        module.__dict__,
    )
    return module


@pytest.mark.parametrize("name", BOUNDS)
def test_config_defaults_and_boundaries(monkeypatch, name):
    module = ast.parse((API / "app/core/config.py").read_text())
    settings = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Settings")
    node = next(node for node in settings.body if isinstance(node, ast.AnnAssign) and node.target.id == name)
    expression = compile(ast.Expression(node.value), "config_outbox", "eval")
    namespace = dict(os=os, Annotated=Annotated, Field=Field, TypeAdapter=TypeAdapter)
    default, low, high = BOUNDS[name]
    monkeypatch.delenv(name, raising=False)
    assert eval(expression, namespace) == default
    for value in (low, high):
        monkeypatch.setenv(name, str(value))
        assert eval(expression, namespace) == value
    for value in (low - 1, high + 1):
        monkeypatch.setenv(name, str(value))
        with pytest.raises(ValidationError):
            eval(expression, namespace)


def test_only_two_registered_tasks_and_beat_entries():
    tasks = load_outbox_tasks()
    assert tasks.scan_outbox_projection.name == "app.tasks.scan_outbox_projection"
    assert tasks.cleanup_outbox.name == "app.tasks.cleanup_outbox"
    for task in (tasks.scan_outbox_projection, tasks.cleanup_outbox):
        assert task.max_retries == 0
        assert task.queue == "memory_projection"
    source = ast.parse((API / "app/celery_app.py").read_text())
    beat = next(node.value for node in source.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "beat_schedule_config" for target in node.targets))
    names = [key.value for key in beat.keys if "outbox" in key.value]
    assert names == ["scan-outbox-projection", "cleanup-outbox"]
    assert 'include=["app.core.memory.storage.outbox.tasks"]' not in (API / "app/celery_app.py").read_text()


def test_task_wrapper_does_not_retry_or_leak_errors(monkeypatch):
    tasks = load_outbox_tasks()
    scan = AsyncMock(return_value={"processed": 1})
    monkeypatch.setattr(tasks, "consume_outbox_batch", scan)
    assert tasks.scan_outbox_projection.run() == {"processed": 1}
    scan.assert_awaited_once()
    cleanup = AsyncMock(side_effect=ConnectionError("password=secret, private SQL payload"))
    monkeypatch.setattr(tasks, "cleanup_outbox_events", cleanup)
    with pytest.raises(RuntimeError) as caught:
        tasks.cleanup_outbox.run()
    assert "secret" not in str(caught.value)
    cleanup.assert_awaited_once()
