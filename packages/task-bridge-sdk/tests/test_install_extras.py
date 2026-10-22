"""A base install must import without the Celery extra."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

_PROBE = textwrap.dedent(
    """
    import sys

    class BlockThirdParty:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {"celery", "kombu", "redis", "billiard", "vine"}:
                raise ImportError(f"blocked: {fullname}")
            return None

    sys.meta_path.insert(0, BlockThirdParty())

    import task_bridge
    from task_bridge import TaskBridge, TaskStatus, TaskTransport

    print("core-ok", TaskBridge.__name__, TaskStatus.__name__, TaskTransport.__name__)

    try:
        task_bridge.CeleryBridge
    except ImportError as exc:
        print("celery-blocked", "task-bridge-sdk[celery]" in str(exc))
    else:
        print("celery-unexpected")
    """
)


def test_core_import_and_celery_hint_without_the_extra():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "core-ok" in result.stdout
    assert "celery-blocked True" in result.stdout
