"""Profile cache tasks, one module per Celery task.

- ``generate``: one user's insight/summary cache (owns the in-flight lock)
- ``generate_workspace``: fan a workspace out into per-user units
- ``scan``: periodic sweep dispatching stale users; its schedule is declared in
  ``src/tasks/schedules.py``

Shared helpers live here too: ``dispatch`` (staggered enqueue) and ``inflight``
(the per-user lock shared with the API's scanner). Importing this package
registers all three tasks with the Celery app.
"""

from src.tasks.profile_cache import (  # noqa: F401
    generate,
    generate_workspace,
    scan,
)
