"""Blocking dependency probe for container and Kubernetes health checks.

Exit code 0 means every dependency answered; 1 means at least one is down.
Workers have no HTTP surface, so probes use ``python -m src.healthcheck``.

The four checks run one after another and each client is configured for real
traffic, not for a probe: a black-holed dependency can hold a single check for
tens of seconds (Neo4j alone allows 30s connect + 30s acquisition). The CLI
therefore enforces a wall-clock deadline and exits on its own, so the container
probe gets a definite ``down`` instead of being killed mid-flight.
"""

from __future__ import annotations

import os
import sys
import threading

from src.bootstrap import get_settings
from src.runtime import get_runtime


def main() -> int:
    runtime = get_runtime()
    deadline_seconds = get_settings().healthcheck_deadline_seconds
    health: dict[str, str] = {}
    finished = threading.Event()

    def probe() -> None:
        nonlocal health
        try:
            health = runtime.dependency_health()
        finally:
            finished.set()

    threading.Thread(target=probe, name="dependency-probe", daemon=True).start()
    if not finished.wait(deadline_seconds):
        print("dependency_probe=timeout", flush=True)
        # The probe thread may still be blocked in a socket read; exit with the
        # status the probe needs instead of waiting for it.
        os._exit(1)

    try:
        print(
            " ".join(f"{name}={status}" for name, status in health.items()),
            flush=True,
        )
    finally:
        runtime.close()
    return 0 if all(status == "up" for status in health.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
