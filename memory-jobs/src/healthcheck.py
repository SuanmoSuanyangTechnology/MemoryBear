"""Blocking dependency probe for container and Kubernetes health checks.

Exit code 0 means every dependency answered; 1 means at least one is down.
Workers have no HTTP surface, so probes use ``python -m src.healthcheck``.
"""

from __future__ import annotations

import sys

from src.runtime import get_runtime


def main() -> int:
    runtime = get_runtime()
    try:
        health = runtime.dependency_health()
    finally:
        runtime.close()
    print(" ".join(f"{name}={status}" for name, status in health.items()), flush=True)
    return 0 if all(status == "up" for status in health.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
