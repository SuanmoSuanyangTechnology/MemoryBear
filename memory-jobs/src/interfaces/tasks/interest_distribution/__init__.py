"""Interest distribution tasks.

One module per Celery task: ``generate`` (one user) and ``init_for_users`` (the
dashboard pre-warm). Importing this package registers both with the Celery app.
"""

from src.interfaces.tasks.interest_distribution import generate, init_for_users  # noqa: F401
