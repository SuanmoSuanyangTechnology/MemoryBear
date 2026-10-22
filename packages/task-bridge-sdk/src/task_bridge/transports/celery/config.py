"""Bridge configuration and the bare Celery app built from it.

The HTTP service must not import the worker's application module: that module
registers every task, imports the whole business layer, and creates the
worker's global app. ``send_task`` and result polling only need a broker URL,
a result backend URL and the JSON protocol settings, so the bridge builds its
own minimal ``Celery`` app with ``set_as_current=False`` and leaves the
process-wide current app untouched.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from celery import Celery

from task_bridge.core.errors import BridgeConfigurationError

#: Broker/backend variables exported by the API deployment (see
#: ``api/app/celery_app.py``); shared so producers and workers agree.
_BROKER_ENV_NAMES = ("TASK_BRIDGE_BROKER_URL", "CELERY_BROKER_URL")
_BACKEND_ENV_NAMES = ("TASK_BRIDGE_RESULT_BACKEND", "CELERY_RESULT_BACKEND")

#: Redis logical databases the API uses when no explicit URL is configured.
_DEFAULT_BROKER_DB = 3
_DEFAULT_BACKEND_DB = 4

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off", ""}


@dataclass(frozen=True, slots=True)
class CeleryConfig:
    """Everything the Celery transport needs to publish and poll tasks.

    ``broker_url`` is mandatory. ``result_backend`` is only needed to poll
    task results; submitting works without it.
    """

    broker_url: str
    result_backend: str | None = None
    #: Queue used when ``submit`` does not receive one explicitly. When
    #: unset, Celery's own default (``celery``) applies, which this project's
    #: workers do not consume -- pass ``queue=`` or set this.
    default_queue: str | None = None
    #: task name -> queue, applied producer-side by Celery's router.
    task_routes: Mapping[str, str] = field(default_factory=dict)
    #: Reject payloads the JSON task protocol cannot carry, before publishing.
    validate_payload: bool = True
    #: Socket timeouts for Redis brokers/backends, in seconds. Without one, a
    #: hung Redis pins a worker thread until the OS gives up: cancelling
    #: ``asyncio.to_thread`` does not interrupt the thread. ``None`` leaves
    #: Celery's defaults (no timeout) in place. Values already present in
    #: ``app_options`` win.
    redis_socket_timeout: float | None = 5.0
    redis_socket_connect_timeout: float | None = 5.0
    #: Read Redis results with ``redis.asyncio`` instead of the synchronous
    #: Celery backend in a worker thread (async API only; falls back when the
    #: backend is not Redis or ``redis`` is not installed).
    prefer_async_redis_results: bool = True
    #: Wake ``await_result`` from Celery's Redis result pub/sub channel instead
    #: of waiting for the next poll. Falls back to polling if SUBSCRIBE is
    #: unavailable; only used when the async Redis path is active.
    use_redis_pubsub: bool = True
    #: Cap on the async result client's connection pool. ``None`` leaves
    #: redis-py's unbounded default.
    async_redis_max_connections: int | None = 50
    #: Dedicated thread pool for broker publishes. ``None`` uses the event
    #: loop's shared default executor.
    executor_workers: int | None = None
    #: Celery's producer/connection pool size. The default (10) caps how many
    #: broker connections a cold concurrent burst can open in parallel; set it
    #: to roughly the expected publish concurrency. ``None`` keeps Celery's
    #: default.
    broker_pool_limit: int | None = None
    #: Fraction of ``interval`` added as random jitter to each poll, so many
    #: waiters started together do not all hit Redis on the same tick.
    #: ``0`` disables jitter.
    poll_jitter: float = 0.1
    timezone: str = "UTC"
    enable_utc: bool = True
    task_create_missing_queues: bool = True
    #: Extra Celery settings (for example ``broker_transport_options``).
    #: Keys set explicitly by this class always win.
    app_options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.broker_url, str) or not self.broker_url.strip():
            raise BridgeConfigurationError(
                "broker_url is required; set TASK_BRIDGE_BROKER_URL, "
                "CELERY_BROKER_URL, or pass it to CeleryConfig"
            )
        if self.result_backend is not None and not str(self.result_backend).strip():
            raise BridgeConfigurationError("result_backend must not be empty when set")
        if self.default_queue is not None and not str(self.default_queue).strip():
            raise BridgeConfigurationError("default_queue must not be empty when set")
        for name, queue in self.task_routes.items():
            if not name or not str(queue).strip():
                raise BridgeConfigurationError(f"invalid task route: {name!r} -> {queue!r}")
        if self.async_redis_max_connections is not None and self.async_redis_max_connections < 1:
            raise BridgeConfigurationError("async_redis_max_connections must be at least 1")
        if self.executor_workers is not None and self.executor_workers < 1:
            raise BridgeConfigurationError("executor_workers must be at least 1")
        if self.broker_pool_limit is not None and self.broker_pool_limit < 1:
            raise BridgeConfigurationError("broker_pool_limit must be at least 1")
        if not 0.0 <= self.poll_jitter <= 1.0:
            raise BridgeConfigurationError("poll_jitter must be between 0 and 1")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> CeleryConfig:
        """Read broker/backend settings from the process environment.

        Resolution order:

        1. ``TASK_BRIDGE_BROKER_URL`` / ``TASK_BRIDGE_RESULT_BACKEND``
           (bridge-specific, never intercepted by Celery's CLI);
        2. ``CELERY_BROKER_URL`` / ``CELERY_RESULT_BACKEND`` (shared with the
           API deployment);
        3. a Redis URL derived from ``REDIS_URL`` or
           ``REDIS_HOST``/``REDIS_PORT``/``REDIS_PASSWORD`` using
           ``REDIS_DB_CELERY_BROKER`` (default 3) and
           ``REDIS_DB_CELERY_BACKEND`` (default 4), matching
           ``api/app/celery_app.py``.

        ``TASK_BRIDGE_BROKER_DB`` / ``TASK_BRIDGE_BACKEND_DB`` override the
        logical databases without changing the shared variables.
        """

        env = os.environ if environ is None else environ
        broker_url = _first(env, _BROKER_ENV_NAMES)
        if not broker_url:
            broker_db = _env_int(
                env,
                "TASK_BRIDGE_BROKER_DB",
                _env_int(env, "REDIS_DB_CELERY_BROKER", _DEFAULT_BROKER_DB),
            )
            broker_url = _redis_url_for_db(env, broker_db)

        result_backend = _first(env, _BACKEND_ENV_NAMES)
        if not result_backend:
            backend_db = _env_int(
                env,
                "TASK_BRIDGE_BACKEND_DB",
                _env_int(env, "REDIS_DB_CELERY_BACKEND", _DEFAULT_BACKEND_DB),
            )
            result_backend = _redis_url_for_db(env, backend_db)

        return cls(
            broker_url=broker_url,
            result_backend=result_backend,
            default_queue=_first(env, ("TASK_BRIDGE_DEFAULT_QUEUE",)),
            task_routes=_env_task_routes(env),
            validate_payload=_env_bool(env, "TASK_BRIDGE_VALIDATE_PAYLOAD", True),
            prefer_async_redis_results=_env_bool(
                env, "TASK_BRIDGE_ASYNC_REDIS_RESULTS", True
            ),
            use_redis_pubsub=_env_bool(env, "TASK_BRIDGE_REDIS_PUBSUB", True),
            async_redis_max_connections=_env_optional_int(
                env, "TASK_BRIDGE_ASYNC_REDIS_MAX_CONNECTIONS", 50
            ),
            executor_workers=_env_optional_int(env, "TASK_BRIDGE_EXECUTOR_WORKERS", None),
            broker_pool_limit=_env_optional_int(env, "TASK_BRIDGE_BROKER_POOL_LIMIT", None),
            poll_jitter=_env_float(env, "TASK_BRIDGE_POLL_JITTER", 0.1),
        )

    def build_app(self, name: str = "task-bridge") -> Celery:
        """Create the producer-only Celery app without connecting anywhere.

        ``set_as_current=False`` is deliberate: the bridge must not replace
        ``celery.current_app`` inside the HTTP process.
        """

        conf: dict[str, Any] = dict(self.app_options)
        conf.update(
            {
                "task_serializer": "json",
                "result_serializer": "json",
                "accept_content": ["json"],
                "timezone": self.timezone,
                "enable_utc": self.enable_utc,
                "task_create_missing_queues": self.task_create_missing_queues,
            }
        )
        if self.default_queue:
            conf["task_default_queue"] = self.default_queue
        if self.task_routes:
            conf["task_routes"] = {
                task_name: {"queue": queue_name}
                for task_name, queue_name in self.task_routes.items()
            }
        if self.broker_pool_limit is not None:
            conf["broker_pool_limit"] = self.broker_pool_limit
        self._apply_redis_timeouts(conf)

        app = Celery(
            name,
            broker=self.broker_url,
            backend=self.result_backend,
            set_as_current=False,
        )
        app.conf.update(conf)
        return app

    def _apply_redis_timeouts(self, conf: dict[str, Any]) -> None:
        if _is_redis_url(self.broker_url):
            transport_options = dict(conf.get("broker_transport_options") or {})
            if self.redis_socket_connect_timeout is not None:
                transport_options.setdefault(
                    "socket_connect_timeout", self.redis_socket_connect_timeout
                )
            if self.redis_socket_timeout is not None:
                transport_options.setdefault("socket_timeout", self.redis_socket_timeout)
            if transport_options:
                conf["broker_transport_options"] = transport_options

        if self.result_backend and _is_redis_url(self.result_backend):
            if self.redis_socket_connect_timeout is not None:
                conf.setdefault(
                    "redis_socket_connect_timeout", self.redis_socket_connect_timeout
                )
            if self.redis_socket_timeout is not None:
                conf.setdefault("redis_socket_timeout", self.redis_socket_timeout)


def _first(env: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = env.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise BridgeConfigurationError(f"{name} must be an integer, got {raw!r}") from exc


def _env_optional_int(
    env: Mapping[str, str], name: str, default: int | None
) -> int | None:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise BridgeConfigurationError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise BridgeConfigurationError(f"{name} must be at least 1, got {value}")
    return value


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError as exc:
        raise BridgeConfigurationError(f"{name} must be a number, got {raw!r}") from exc


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise BridgeConfigurationError(f"{name} must be a boolean, got {raw!r}")


def _env_task_routes(env: Mapping[str, str]) -> Mapping[str, str]:
    raw = env.get("TASK_BRIDGE_TASK_ROUTES")
    if raw is None or not str(raw).strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeConfigurationError(
            "TASK_BRIDGE_TASK_ROUTES must be a JSON object of task name -> queue"
        ) from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
    ):
        raise BridgeConfigurationError(
            "TASK_BRIDGE_TASK_ROUTES must be a JSON object of task name -> queue"
        )
    return dict(parsed)


def _is_redis_url(url: str) -> bool:
    return urlsplit(url).scheme in {"redis", "rediss"}


def _redis_url_for_db(env: Mapping[str, str], db: int) -> str:
    """Build a Redis URL for one logical database.

    ``REDIS_URL`` wins as the connection source (it is the single URL the
    profile service already reads); otherwise the discrete ``REDIS_*``
    variables are used. The path is always replaced by ``db`` so the bridge
    never publishes into the business-cache database by accident.
    """

    redis_url = (env.get("REDIS_URL") or "").strip()
    if redis_url:
        parsed = urlsplit(redis_url)
        if parsed.scheme in {"redis", "rediss"} and parsed.hostname:
            return urlunsplit((parsed.scheme, parsed.netloc, f"/{db}", "", ""))

    host = (env.get("REDIS_HOST") or "127.0.0.1").strip()
    port = (env.get("REDIS_PORT") or "6379").strip()
    password = env.get("REDIS_PASSWORD") or ""
    username = env.get("REDIS_USERNAME") or ""
    if password:
        credentials = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    elif username:
        credentials = f"{quote(username, safe='')}@"
    else:
        credentials = ""
    return f"redis://{credentials}{host}:{port}/{db}"


#: Backwards-compatible alias for the pre-abstraction name.
BridgeConfig = CeleryConfig

__all__ = ["BridgeConfig", "CeleryConfig"]
