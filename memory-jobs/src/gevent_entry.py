"""Celery entrypoint for the gevent pool.

Celery's gevent pool only cooperates if the process is monkey-patched **before**
any socket or threading client is imported. This module patches first and only
then imports the Celery app, so every SDK client (psycopg, redis-py, neo4j,
httpx, urllib3) is built over gevent's green sockets.

``psycopg`` cooperates on its own: it detects the patched ``select``
(``psycopg.waiting._is_select_patched``) and stops using its C-level
``wait_c``, falling back to the Python wait that yields to the hub. A blocking
call implemented in C anywhere else would stall every greenlet, so keep the
generation paths on the clients this service already uses.

Run it with the matching pool::

    celery -A src.gevent_entry:celery_app worker --pool=gevent --concurrency=32

The unpatched ``src.entry:celery_app`` stays the entry for the threads and
prefork pools.
"""

from gevent import monkey

monkey.patch_all()

from src.celery_app import celery_app  # noqa: E402  (must follow the patch)

__all__ = ["celery_app"]
