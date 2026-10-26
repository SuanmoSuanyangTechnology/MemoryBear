"""Immutable configuration for the memory-jobs worker processes."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote_plus, urlparse, urlsplit, urlunsplit

from pydantic import AliasChoices, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url


def _as_sync_postgres_url(value: str) -> str:
    """Normalize any PostgreSQL URL, including an asyncpg one, to the sync driver.

    Shared infrastructure env files hand the same ``DATABASE_URL`` to every
    service, and it may be spelled ``postgresql+asyncpg``. Workers deliberately
    run on the blocking driver, so the URL is rewritten instead of rejected.
    """

    url = make_url(value)
    if url.get_backend_name() not in {"postgres", "postgresql"}:
        raise ValueError("PostgreSQL URL required")
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _redis_url_with_db(url: str, db: int) -> str:
    """Return ``url`` pointing at Redis logical database ``db``."""

    parsed = urlsplit(url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise ValueError("REDIS_URL must be an absolute redis:// or rediss:// URL")
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{db}", parsed.query, parsed.fragment))


class JobsSettings(BaseSettings):
    """All knobs of the worker/beat process, resolved once at import time.

    Environment names intentionally reuse the shared names used by the API
    (``DATABASE_*``, ``REDIS_*``, ``ELASTICSEARCH_*``, ``NEO4J_*``) so the same
    deployment config can be injected into every service. Service-only switches
    are prefixed with ``JOBS_``.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", case_sensitive=False)

    service_name: str = Field("memory-jobs", validation_alias="JOBS_SERVICE_NAME")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO", validation_alias="JOBS_LOG_LEVEL"
    )
    log_format: Literal["text", "json"] = Field("text", validation_alias="JOBS_LOG_FORMAT")
    #: Probe PostgreSQL/Redis/Elasticsearch/Neo4j once per worker process at
    #: startup. Opt-in: a transient dependency outage should not keep workers
    #: from booting and picking up work once the dependency recovers.
    startup_dependency_check: bool = Field(
        False, validation_alias="JOBS_STARTUP_DEPENDENCY_CHECK"
    )
    #: Per-request timeout for dependency probes. Probes must stay far below
    #: task timeouts and container probe deadlines even when a dependency is
    #: unreachable (the shared client may retry for minutes on real traffic).
    healthcheck_timeout_seconds: float = Field(
        3.0, gt=0, validation_alias="JOBS_HEALTHCHECK_TIMEOUT_SECONDS"
    )

    # ---------------------------------------------------------------- PostgreSQL
    database_url: str | None = Field(
        None,
        validation_alias=AliasChoices("JOBS_DATABASE_URL", "DATABASE_URL", "ASYNC_DATABASE_URL"),
    )
    database_host: str = Field(
        "localhost", validation_alias=AliasChoices("DATABASE_HOST", "DB_HOST", "POSTGRES_HOST")
    )
    database_port: int = Field(
        5432, validation_alias=AliasChoices("DATABASE_PORT", "DB_PORT", "POSTGRES_PORT")
    )
    database_user: str = Field(
        "postgres",
        validation_alias=AliasChoices(
            "DATABASE_USER", "DATABASE_USERNAME", "DB_USER", "POSTGRES_USER"
        ),
    )
    database_password: str = Field(
        "", validation_alias=AliasChoices("DATABASE_PASSWORD", "DB_PASSWORD", "POSTGRES_PASSWORD")
    )
    database_name: str = Field(
        "postgres",
        validation_alias=AliasChoices("DATABASE_NAME", "DB_NAME", "POSTGRES_DB"),
    )
    database_pool_size: int = Field(5, ge=1, validation_alias="JOBS_DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(10, ge=0, validation_alias="JOBS_DATABASE_MAX_OVERFLOW")
    database_pool_timeout_seconds: float = Field(
        30.0, gt=0, validation_alias="JOBS_DATABASE_POOL_TIMEOUT_SECONDS"
    )
    database_pool_recycle_seconds: int = Field(
        1800, ge=0, validation_alias="JOBS_DATABASE_POOL_RECYCLE_SECONDS"
    )
    database_connect_timeout_seconds: int = Field(
        10, ge=1, validation_alias="JOBS_DATABASE_CONNECT_TIMEOUT_SECONDS"
    )

    # --------------------------------------------------------------------- Redis
    redis_url: str | None = Field(
        None, validation_alias=AliasChoices("JOBS_REDIS_URL", "REDIS_URL")
    )
    redis_host: str = Field("localhost", validation_alias="REDIS_HOST")
    redis_port: int = Field(6379, validation_alias="REDIS_PORT")
    redis_password: str = Field("", validation_alias="REDIS_PASSWORD")
    redis_db: int = Field(1, ge=0, validation_alias="REDIS_DB")
    redis_pool_size: int = Field(100, ge=1, validation_alias="JOBS_REDIS_POOL_SIZE")
    redis_socket_timeout_seconds: float = Field(
        30.0, gt=0, validation_alias="JOBS_REDIS_SOCKET_TIMEOUT_SECONDS"
    )
    redis_connect_timeout_seconds: float = Field(
        5.0, gt=0, validation_alias="JOBS_REDIS_CONNECT_TIMEOUT_SECONDS"
    )

    # -------------------------------------------------------------------- Celery
    #: Optional full broker/pool override. Do NOT also export bare
    #: ``BROKER_URL`` / ``RESULT_BACKEND``: Celery's CLI (Click) claims those.
    celery_broker_url: str | None = Field(None, validation_alias="CELERY_BROKER_URL")
    celery_result_backend: str | None = Field(None, validation_alias="CELERY_RESULT_BACKEND")
    #: Logical Redis DBs for the broker and result backend. They must match the
    #: API's values (defaults 3 and 4) so both services talk to the same queues.
    redis_db_celery_broker: int = Field(3, ge=0, validation_alias="REDIS_DB_CELERY_BROKER")
    redis_db_celery_backend: int = Field(4, ge=0, validation_alias="REDIS_DB_CELERY_BACKEND")
    task_default_queue: str = Field("memory_tasks", validation_alias="JOBS_TASK_DEFAULT_QUEUE")
    task_time_limit_seconds: int = Field(3600, ge=1, validation_alias="JOBS_TASK_TIME_LIMIT")
    task_soft_time_limit_seconds: int = Field(
        3000, ge=1, validation_alias="JOBS_TASK_SOFT_TIME_LIMIT"
    )
    task_acks_late: bool = Field(True, validation_alias="JOBS_TASK_ACKS_LATE")
    task_reject_on_worker_lost: bool = Field(
        True, validation_alias="JOBS_TASK_REJECT_ON_WORKER_LOST"
    )
    worker_prefetch_multiplier: int = Field(
        1, ge=0, validation_alias="JOBS_WORKER_PREFETCH_MULTIPLIER"
    )
    worker_send_task_events: bool = Field(True, validation_alias="JOBS_WORKER_SEND_TASK_EVENTS")
    task_send_sent_event: bool = Field(True, validation_alias="JOBS_TASK_SEND_SENT_EVENT")
    result_expires_seconds: int = Field(3600, ge=0, validation_alias="JOBS_RESULT_EXPIRES")

    # ------------------------------------------------------------ model service
    #: 生成类任务走模型服务 invoke 接缝；凭据、渠道选路与 failover 都在模型服务侧。
    #: 变量名与 memory-profile 完全一致，两个服务可共用同一份环境变量。
    model_service_base_url: str = Field(
        "http://127.0.0.1:8080", validation_alias="MODEL_SERVICE_BASE_URL"
    )
    model_service_connect_timeout_seconds: float = Field(
        5.0, validation_alias="MODEL_SERVICE_INVOKE_CONNECT_TIMEOUT_SECONDS"
    )
    model_service_idle_timeout_seconds: float = Field(
        180.0, validation_alias="MODEL_SERVICE_INVOKE_IDLE_TIMEOUT_SECONDS"
    )
    model_service_write_timeout_seconds: float = Field(
        60.0, validation_alias="MODEL_SERVICE_INVOKE_WRITE_TIMEOUT_SECONDS"
    )
    model_service_pool_timeout_seconds: float = Field(
        5.0, validation_alias="MODEL_SERVICE_POOL_TIMEOUT_SECONDS"
    )
    model_service_max_connections: int = Field(
        100, validation_alias="MODEL_SERVICE_MAX_CONNECTIONS"
    )
    model_service_max_keepalive_connections: int = Field(
        20, validation_alias="MODEL_SERVICE_MAX_KEEPALIVE_CONNECTIONS"
    )

    # ------------------------------------------------------------ Elasticsearch
    elasticsearch_host: str = Field(
        "https://127.0.0.1", validation_alias="ELASTICSEARCH_HOST"
    )
    elasticsearch_port: int = Field(
        9200, ge=1, le=65535, validation_alias="ELASTICSEARCH_PORT"
    )
    elasticsearch_username: str = Field("elastic", validation_alias="ELASTICSEARCH_USERNAME")
    elasticsearch_password: str = Field("", validation_alias="ELASTICSEARCH_PASSWORD")
    elasticsearch_verify_certs: bool = Field(
        False, validation_alias="ELASTICSEARCH_VERIFY_CERTS"
    )
    elasticsearch_ca_certs: str = Field("", validation_alias="ELASTICSEARCH_CA_CERTS")
    elasticsearch_request_timeout_seconds: float = Field(
        120.0, gt=0, validation_alias="ELASTICSEARCH_REQUEST_TIMEOUT"
    )
    elasticsearch_retry_on_timeout: bool = Field(
        True, validation_alias="ELASTICSEARCH_RETRY_ON_TIMEOUT"
    )
    elasticsearch_max_retries: int = Field(
        10, ge=0, validation_alias="ELASTICSEARCH_MAX_RETRIES"
    )
    elasticsearch_connections_per_node: int = Field(
        20, ge=1, validation_alias="JOBS_ELASTICSEARCH_CONNECTIONS_PER_NODE"
    )

    # --------------------------------------------------------------------- Neo4j
    neo4j_uri: str = Field("bolt://localhost:7687", validation_alias="NEO4J_URI")
    neo4j_username: str = Field(
        "neo4j", validation_alias=AliasChoices("NEO4J_USERNAME", "NEO4J_USER")
    )
    neo4j_password: str = Field("", validation_alias="NEO4J_PASSWORD")
    neo4j_database: str | None = Field(None, validation_alias="NEO4J_DATABASE")
    neo4j_max_pool_size: int = Field(30, ge=1, validation_alias="NEO4J_MAX_POOL_SIZE")
    neo4j_acquisition_timeout_seconds: float = Field(
        30.0, gt=0, validation_alias="NEO4J_ACQ_TIMEOUT"
    )
    neo4j_max_connection_lifetime_seconds: int = Field(
        3600, ge=0, validation_alias="NEO4J_MAX_CONN_LIFETIME"
    )
    neo4j_connection_timeout_seconds: float = Field(
        30.0, gt=0, validation_alias="NEO4J_CONN_TIMEOUT"
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("elasticsearch_host")
    @classmethod
    def validate_elasticsearch_host(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ELASTICSEARCH_HOST must be an absolute HTTP(S) URL")
        return value

    @field_validator("model_service_base_url")
    @classmethod
    def validate_model_service_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MODEL_SERVICE_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("MODEL_SERVICE_BASE_URL must not contain query or fragment")
        return value

    @property
    def sync_database_url(self) -> str:
        """SQLAlchemy URL for the blocking psycopg driver."""

        if self.database_url:
            return _as_sync_postgres_url(self.database_url)
        user = quote_plus(self.database_user)
        password = quote_plus(self.database_password)
        auth = f"{user}:{password}" if self.database_password else user
        return (
            f"postgresql+psycopg://{auth}@{self.database_host}:"
            f"{self.database_port}/{self.database_name}"
        )

    @property
    def resolved_redis_url(self) -> str:
        """Redis URL used for business data (not the Celery broker/backend)."""

        if self.redis_url:
            return self.redis_url
        password = f":{quote_plus(self.redis_password)}@" if self.redis_password else ""
        return f"redis://{password}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def resolved_celery_broker_url(self) -> str:
        if self.celery_broker_url:
            return self.celery_broker_url
        return _redis_url_with_db(self.resolved_redis_url, self.redis_db_celery_broker)

    @property
    def resolved_celery_result_backend(self) -> str:
        if self.celery_result_backend:
            return self.celery_result_backend
        return _redis_url_with_db(self.resolved_redis_url, self.redis_db_celery_backend)

    @property
    def elasticsearch_hosts(self) -> list[str]:
        host = self.elasticsearch_host.rstrip("/")
        parsed = urlparse(host)
        if parsed.port is not None:
            return [host]
        return [f"{host}:{self.elasticsearch_port}"]
