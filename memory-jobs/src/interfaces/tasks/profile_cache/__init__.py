"""Profile cache tasks, one module per Celery task.

- ``generate``: one user's insight/summary cache (owns the in-flight lock)
- ``generate_workspace``: fan a workspace out into per-user units
- ``scan``: periodic sweep dispatching stale users; its schedule is declared in
  ``src/tasks/schedules.py``

The staggered publisher lives in ``src.interfaces.tasks.publishing`` and the per-user lock
in ``src.infrastructure.lock``; both are application ports with Celery/Redis
adapters. Importing this package registers all three tasks with the Celery app.
"""

from src.interfaces.tasks.profile_cache import (  # noqa: F401
    generate,
    generate_workspace,
    scan,
)
