"""Immutable configuration for the profile API process."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote_plus, urlparse

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url


def _as_asyncpg_url(value: str) -> str:
    url = make_url(value)
    if url.get_backend_name() not in {"postgres", "postgresql"}:
        raise ValueError("PostgreSQL URL required")
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


class ProfileSettings(BaseSettings):
    model_config = ConfigDict(frozen=True, extra="ignore", case_sensitive=False)

    service_name: str = Field("memory-profile", validation_alias="PROFILE_SERVICE_NAME")
    host: str = Field("0.0.0.0", validation_alias="PROFILE_HOST")
    port: int = Field(8082, validation_alias="PROFILE_PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO", validation_alias="PROFILE_LOG_LEVEL"
    )
    log_format: Literal["text", "json"] = Field("text", validation_alias="PROFILE_LOG_FORMAT")
    access_log_enabled: bool = Field(True, validation_alias="PROFILE_ACCESS_LOG_ENABLED")
    health_probe_timeout_seconds: float = Field(
        2.0, validation_alias="PROFILE_HEALTH_PROBE_TIMEOUT_SECONDS"
    )

    # Runtime monitoring: tracemalloc is intentionally opt-in because allocation
    # tracing has measurable CPU/memory overhead and snapshots are expensive.
    memory_tracemalloc_enabled: bool = Field(
        False, validation_alias="PROFILE_MEMORY_TRACEMALLOC_ENABLED"
    )
    memory_tracemalloc_frames: int = Field(
        10, ge=1, le=50, validation_alias="PROFILE_MEMORY_TRACEMALLOC_FRAMES"
    )
    # 0 disables the RSS warning; set it to a container-aware alert threshold.
    memory_rss_warn_bytes: int = Field(
        0, ge=0, validation_alias="PROFILE_MEMORY_RSS_WARN_BYTES"
    )

    i18n_default_language: str = Field("zh", validation_alias="I18N_DEFAULT_LANGUAGE")
    i18n_fallback_language: str = Field("zh", validation_alias="I18N_FALLBACK_LANGUAGE")
    i18n_supported_languages: str = Field(
        "zh,en", validation_alias="I18N_SUPPORTED_LANGUAGES"
    )
    i18n_locales_dir: str | None = Field(None, validation_alias="I18N_LOCALES_DIR")

    memory_auth_mode: Literal["direct", "gateway"] = Field(
        "direct", validation_alias="MEMORY_AUTH_MODE"
    )
    memory_auth_service_name: str = Field(
        "memory-profile",
        min_length=1,
        validation_alias="MEMORY_AUTH_SERVICE_NAME",
    )
    memory_auth_kill_switch_file: str | None = Field(
        None, validation_alias="MEMORY_AUTH_KILL_SWITCH_FILE"
    )
    memory_auth_jwks_url: str | None = Field(None, validation_alias="MEMORY_AUTH_JWKS_URL")
    # ACL rule key and hot-reload channel. Empty derives
    # acl:rules:<DB_NAME> / acl:rules:changed:<DB_NAME> to match identity's
    # write side; deployments sharing one Redis but owning separate databases
    # stay isolated (pub/sub channels are global across logical DBs).
    acl_rules_key: str = Field(default="", validation_alias="ACL_RULES_KEY")
    acl_rules_changed_channel: str = Field(
        default="",
        validation_alias="ACL_RULES_CHANGED_CHANNEL",
    )

    # 生成类用例（memory insight / user summary / interest distribution）走模型服务
    # invoke 接缝；凭据、渠道选路与 failover 都在模型服务侧（与 api 同口径）。
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

    # 缓存生成由 memory-jobs 的 Celery worker 执行：本进程只按任务名投递并等待结果，
    # 不注册任务、不启动 worker、不 import worker 的 Celery 应用（task-bridge-sdk 的接缝）。
    # 任务名、队列名与等待预算都是与 worker 注册表/路由、以及 api 侧调用预算绑定的契约，
    # 不做环境变量配置，见 src/infrastructure/task_bridge.py 的常量。
    #: 留空时由 task-bridge 从 CELERY_* / REDIS_* 派生（broker DB 3、backend DB 4）。
    task_bridge_broker_url: str | None = Field(
        None, validation_alias=AliasChoices("TASK_BRIDGE_BROKER_URL", "CELERY_BROKER_URL")
    )
    task_bridge_result_backend: str | None = Field(
        None,
        validation_alias=AliasChoices("TASK_BRIDGE_RESULT_BACKEND", "CELERY_RESULT_BACKEND"),
    )
    #: 兴趣分布缓存 TTL（api ``INTEREST_CACHE_EXPIRE`` 同值）。
    interest_cache_expire_seconds: int = Field(
        24 * 60 * 60, validation_alias="PROFILE_INTEREST_CACHE_EXPIRE_SECONDS"
    )
    migration_database_url: str | None = Field(
        None,
        validation_alias="PROFILE_MIGRATION_DATABASE_URL",
    )
    database_url: str | None = Field(
        None,
        validation_alias=AliasChoices("PROFILE_DATABASE_URL", "DATABASE_URL", "ASYNC_DATABASE_URL"),
    )
    database_host: str = Field(
        "localhost", validation_alias=AliasChoices("DATABASE_HOST", "POSTGRES_HOST", "DB_HOST")
    )
    database_port: int = Field(
        5432, validation_alias=AliasChoices("DATABASE_PORT", "POSTGRES_PORT", "DB_PORT")
    )
    database_user: str = Field(
        "postgres",
        validation_alias=AliasChoices(
            "DATABASE_USER", "DATABASE_USERNAME", "POSTGRES_USER", "DB_USER"
        ),
    )
    database_password: str = Field(
        "",
        validation_alias=AliasChoices("DATABASE_PASSWORD", "POSTGRES_PASSWORD", "DB_PASSWORD"),
    )
    # Single source for the database name; the same value scopes the derived
    # ACL rule names, which must match identity's DB_NAME-derived names.
    database_name: str = Field("postgres", validation_alias="DB_NAME")
    database_pool_size: int = Field(5, validation_alias="PROFILE_DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(5, validation_alias="PROFILE_DATABASE_MAX_OVERFLOW")
    database_pool_timeout_seconds: float = Field(
        30.0, validation_alias="PROFILE_DATABASE_POOL_TIMEOUT_SECONDS"
    )
    database_pool_recycle_seconds: int = Field(
        1800, validation_alias="PROFILE_DATABASE_POOL_RECYCLE_SECONDS"
    )

    neo4j_uri: str = Field("bolt://localhost:7687", validation_alias="NEO4J_URI")
    neo4j_username: str = Field(
        "neo4j", validation_alias=AliasChoices("NEO4J_USERNAME", "NEO4J_USER")
    )
    neo4j_password: str = Field("", validation_alias="NEO4J_PASSWORD")
    neo4j_database: str | None = Field(None, validation_alias="NEO4J_DATABASE")
    neo4j_max_pool_size: int = Field(30, validation_alias="NEO4J_MAX_POOL_SIZE")
    neo4j_acquisition_timeout_seconds: float = Field(30.0, validation_alias="NEO4J_ACQ_TIMEOUT")
    neo4j_max_connection_lifetime_seconds: int = Field(
        3600, validation_alias="NEO4J_MAX_CONN_LIFETIME"
    )
    neo4j_connection_timeout_seconds: float = Field(30.0, validation_alias="NEO4J_CONN_TIMEOUT")

    redis_url: str | None = Field(None, validation_alias="REDIS_URL")
    redis_host: str = Field("localhost", validation_alias="REDIS_HOST")
    redis_port: int = Field(6379, validation_alias="REDIS_PORT")
    redis_password: str | None = Field(None, validation_alias="REDIS_PASSWORD")
    redis_db: int = Field(1, validation_alias="REDIS_DB")
    redis_pool_size: int = Field(100, validation_alias="PROFILE_REDIS_POOL_SIZE")

    elasticsearch_host: str = Field("https://127.0.0.1", validation_alias="ELASTICSEARCH_HOST")
    elasticsearch_port: int = Field(9200, validation_alias="ELASTICSEARCH_PORT")
    elasticsearch_username: str = Field("elastic", validation_alias="ELASTICSEARCH_USERNAME")
    elasticsearch_password: str = Field("", validation_alias="ELASTICSEARCH_PASSWORD")
    elasticsearch_verify_certs: bool = Field(False, validation_alias="ELASTICSEARCH_VERIFY_CERTS")
    elasticsearch_ca_certs: str = Field("", validation_alias="ELASTICSEARCH_CA_CERTS")
    elasticsearch_request_timeout_seconds: float = Field(
        120.0, validation_alias="ELASTICSEARCH_REQUEST_TIMEOUT"
    )
    elasticsearch_retry_on_timeout: bool = Field(
        True, validation_alias="ELASTICSEARCH_RETRY_ON_TIMEOUT"
    )
    elasticsearch_max_retries: int = Field(10, validation_alias="ELASTICSEARCH_MAX_RETRIES")
    elasticsearch_connections_per_node: int = Field(
        20, validation_alias="PROFILE_ELASTICSEARCH_CONNECTIONS_PER_NODE"
    )

    @field_validator("model_service_base_url")
    @classmethod
    def validate_model_service_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MODEL_SERVICE_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("MODEL_SERVICE_BASE_URL must not contain query or fragment")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_auth_configuration(self) -> ProfileSettings:
        if self.memory_auth_mode == "gateway" and not self.memory_auth_jwks_url:
            raise ValueError("MEMORY_AUTH_MODE=gateway requires MEMORY_AUTH_JWKS_URL")
        return self

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("PROFILE_PORT must be between 1 and 65535")
        return value

    @property
    def i18n_language_list(self) -> list[str]:
        return [
            language.strip()
            for language in self.i18n_supported_languages.split(",")
            if language.strip()
        ]

    @property
    def async_database_url(self) -> str:
        if self.database_url:
            return _as_asyncpg_url(self.database_url)
        user = quote_plus(self.database_user)
        password = quote_plus(self.database_password)
        auth = f"{user}:{password}" if self.database_password else user
        return (
            f"postgresql+asyncpg://{auth}@{self.database_host}:"
            f"{self.database_port}/{self.database_name}"
        )

    @property
    def alembic_database_url(self) -> str:
        """Use optional DDL credentials without sharing the runtime read-only engine."""

        if self.migration_database_url:
            return _as_asyncpg_url(self.migration_database_url)
        return self.async_database_url

    @property
    def resolved_redis_url(self) -> str:
        if self.redis_url:
            return self.redis_url
        password = f":{quote_plus(self.redis_password)}@" if self.redis_password else ""
        return f"redis://{password}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def _deployment_scoped_name(self, explicit: str, base: str, env_name: str) -> str:
        """Resolve a deployment-scoped Redis name from an override or DB_NAME."""

        explicit = explicit.strip()
        if explicit:
            if any(ch.isspace() for ch in explicit):
                raise ValueError(f"{env_name} must not contain whitespace")
            return explicit
        db_name = self.database_name.strip()
        if not db_name or any(ch.isspace() for ch in db_name):
            raise ValueError(
                f"DB_NAME must be non-empty without whitespace to derive {env_name}"
            )
        return f"{base}:{db_name}"

    @property
    def acl_rules_key_name(self) -> str:
        """Resolve the deployment-scoped ACL rule blob key."""

        return self._deployment_scoped_name(self.acl_rules_key, "acl:rules", "ACL_RULES_KEY")

    @property
    def acl_rules_changed_channel_name(self) -> str:
        """Resolve the deployment-scoped ACL change pub/sub channel."""

        return self._deployment_scoped_name(
            self.acl_rules_changed_channel,
            "acl:rules:changed",
            "ACL_RULES_CHANGED_CHANNEL",
        )

    @property
    def elasticsearch_hosts(self) -> list[str]:
        host = self.elasticsearch_host.rstrip("/")
        parsed = urlparse(host)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ELASTICSEARCH_HOST must be an absolute HTTP(S) URL")
        if parsed.port is not None:
            return [host]
        return [f"{host}:{self.elasticsearch_port}"]
