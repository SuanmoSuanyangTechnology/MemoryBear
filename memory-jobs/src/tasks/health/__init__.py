"""Health and smoke-test tasks.

One module per Celery task: ``ping``. Importing this package registers it with
the Celery app, so the module is reachable as ``src.tasks.health.ping``.
"""

from src.tasks.health import ping  # noqa: F401
