"""Repeatable FakeBackend fault-injection acceptance checks.

Run from ``core/sandbox-service``. No live Cube or HTTP service is required. The script
checks backend unavailability, instance loss and context loss, including recovery semantics.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make direct execution independent of the caller's current working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backends import FakeBackend
from src.clock import ManualClock
from src.errors import SandboxError
from src.keys import KernelKey, SandboxKey
from src.services import SandboxManager, SchedulerConfig


class Checks:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, condition: bool, label: str) -> None:
        print(f"[{'PASS' if condition else 'FAIL'}] {label}")
        if not condition:
            self.failures += 1


def make_env() -> tuple[SandboxManager, FakeBackend]:
    clock = ManualClock()
    backend = FakeBackend(clock)
    manager = SandboxManager(backend, SchedulerConfig(), clock=clock)
    return manager, backend


def key() -> KernelKey:
    return KernelKey(SandboxKey("fault-test", "recovery", "user"), "conversation")


async def verify() -> int:
    checks = Checks()

    manager, backend = make_env()
    backend.fail["create"] = 1
    try:
        await manager.execute(key(), "first")
    except SandboxError as exc:
        checks.check(
            exc.kind == "backend_unavailable" and exc.retryable,
            "backend unavailable is explicit and retryable",
        )
    else:
        checks.check(False, "backend unavailable is explicit and retryable")
    recovered = await manager.execute(key(), "recovered")
    checks.check(
        recovered.path == "create" and recovered.verdict.ok,
        "next request recovers after backend availability returns",
    )

    manager, backend = make_env()
    initial = await manager.execute(key(), "initial")
    backend.lose_instance(initial.instance_id)
    try:
        await manager.execute(key(), "after-loss")
    except SandboxError as exc:
        checks.check(
            exc.kind == "instance_lost" and exc.retryable,
            "instance loss returns explicit retryable failure",
        )
    else:
        checks.check(False, "instance loss returns explicit retryable failure")
    recreated = await manager.execute(key(), "recreated")
    checks.check(
        recreated.path == "create" and recreated.workspace_reset,
        "retry recreates the instance and reports workspace_reset",
    )

    manager, backend = make_env()
    initial = await manager.execute(key(), "initial")
    backend.lose_context(initial.instance_id, initial.context_id)
    rebuilt = await manager.execute(key(), "after-context-loss")
    checks.check(
        rebuilt.verdict.ok and rebuilt.kernel_reset,
        "context loss is rebuilt in-call and reports kernel_reset",
    )
    checks.check(
        rebuilt.context_id != initial.context_id,
        "rebuilt context receives a new context id",
    )

    status = "all 3 scenarios passed" if checks.failures == 0 else "one or more checks failed"
    print(f"RESULT: {status}; failures={checks.failures}")
    return 0 if checks.failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    return asyncio.run(verify())


if __name__ == "__main__":
    sys.exit(main())
