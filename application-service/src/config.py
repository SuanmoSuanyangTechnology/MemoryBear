"""Validated configuration contracts for the application service."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class AppServiceSettings(BaseSettings):
    """Immutable settings constructed only from the bootstrap merge."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    service_name: Literal["application-service"] = "application-service"

    # Shared environment variables
    deployment_mode: str = Field(default="community", validation_alias="DEPLOYMENT_MODE")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    enable_sensitive_data_filter: bool = Field(
        default=True,
        validation_alias="ENABLE_SENSITIVE_DATA_FILTER",
    )
    i18n_default_language: str = Field(default="zh", validation_alias="I18N_DEFAULT_LANGUAGE")
    # Caps FileUploadConfig.max_file_count in the app feature schemas; the host
    # validator reads the monolith `settings.MAX_FILE_COUNT` (same env name).
    max_file_count: int = Field(default=20, ge=1, validation_alias="MAX_FILE_COUNT")
    # DSL export metadata version (host `settings.SYSTEM_VERSION`); same env
    # name and default as the monolith.
    system_version: str = Field(default="v0.2.1", validation_alias="SYSTEM_VERSION")
    # TTL of the staged workflow-import payload in the shared Redis (host
    # `settings.WORKFLOW_IMPORT_CACHE_TIMEOUT`); same env name and default.
    workflow_import_cache_timeout: int = Field(
        default=1800, ge=1, validation_alias="WORKFLOW_IMPORT_CACHE_TIMEOUT"
    )
    # Base URL used to rebuild local file URLs for resolved workflow variable
    # defaults; same env name and default as the monolith settings.
    file_local_server_url: str = Field(
        default="http://localhost:8000/api",
        validation_alias="FILE_LOCAL_SERVER_URL",
    )
    db_host: str = Field(default="127.0.0.1", validation_alias="DB_HOST")
    db_port: int = Field(default=5432, ge=1, le=65535, validation_alias="DB_PORT")
    db_user: str = Field(default="postgres", validation_alias="DB_USER")
    db_password: SecretStr = Field(default=SecretStr("password"), validation_alias="DB_PASSWORD")
    db_name: str = Field(default="redbear-mem", validation_alias="DB_NAME")
    redis_host: str = Field(default="127.0.0.1", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, ge=1, le=65535, validation_alias="REDIS_PORT")
    redis_password: SecretStr = Field(default=SecretStr(""), validation_alias="REDIS_PASSWORD")
    # Must match the host api's Redis DB: acl:rules (identity-written) is read
    # from the shared database.
    redis_db: int = Field(default=0, ge=0, validation_alias="REDIS_DB")

    # Application service environment variables
    application_service_host: str = Field(
        default="0.0.0.0", validation_alias="APPLICATION_SERVICE_HOST"
    )
    application_service_port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        validation_alias="APPLICATION_SERVICE_PORT",
    )
    application_service_log_level: str = Field(
        default="INFO",
        validation_alias="APPLICATION_SERVICE_LOG_LEVEL",
    )
    application_service_db_pool_size: int = Field(
        default=20,
        ge=1,
        validation_alias="APPLICATION_SERVICE_DB_POOL_SIZE",
    )
    application_service_db_max_overflow: int = Field(
        default=10,
        ge=0,
        validation_alias="APPLICATION_SERVICE_DB_MAX_OVERFLOW",
    )
    application_service_db_pool_recycle: int = Field(
        default=1800,
        ge=1,
        validation_alias="APPLICATION_SERVICE_DB_POOL_RECYCLE",
    )
    application_service_db_pool_timeout: int = Field(
        default=30,
        ge=1,
        validation_alias="APPLICATION_SERVICE_DB_POOL_TIMEOUT",
    )
    application_service_db_pool_pre_ping: bool = Field(
        default=True,
        validation_alias="APPLICATION_SERVICE_DB_POOL_PRE_PING",
    )
    application_service_db_statement_timeout_ms: int = Field(
        default=60000,
        ge=1,
        validation_alias="APPLICATION_SERVICE_DB_STATEMENT_TIMEOUT_MS",
    )
    application_service_redis_pool_size: int = Field(
        default=50,
        ge=1,
        validation_alias="APPLICATION_SERVICE_REDIS_POOL_SIZE",
    )
    application_service_health_probe_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        validation_alias="APPLICATION_SERVICE_HEALTH_PROBE_TIMEOUT_SECONDS",
    )
    # Key namespace of this service's own Redis entries (the instance is shared
    # with the monolith's Celery broker and cache during the coexistence period).
    application_service_redis_key_prefix: str = Field(
        default="app:",
        validation_alias="APPLICATION_SERVICE_REDIS_KEY_PREFIX",
    )

    # SSE contract: the service must finish or heartbeat strictly before the
    # upstream idle close; the gateway cuts idle streams at 300s, so both
    # counters stay far below it. Contract only for now; no SSE endpoint
    # exists yet.
    application_service_sse_idle_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        validation_alias="APPLICATION_SERVICE_SSE_IDLE_TIMEOUT_SECONDS",
    )
    application_service_sse_heartbeat_interval_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias="APPLICATION_SERVICE_SSE_HEARTBEAT_INTERVAL_SECONDS",
    )

    # Internal-face auth: direct (community default, trusts the X-App-* identity
    # headers injected by the gateway after credential termination + NetworkPolicy);
    # gateway (enterprise, internal-token verification through the private
    # enterprise-extensions package).
    application_service_auth_mode: Literal["direct", "gateway"] = Field(
        default="direct",
        validation_alias="APPLICATION_SERVICE_AUTH_MODE",
    )
    application_service_internal_name: str = Field(
        default="application-service",
        validation_alias="APPLICATION_SERVICE_INTERNAL_NAME",
    )
    application_service_jwks_url: str | None = Field(
        default=None,
        validation_alias="APPLICATION_SERVICE_JWKS_URL",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use only the mapping already merged by bootstrap."""

        del cls, settings_cls, env_settings, dotenv_settings, file_secret_settings
        return (init_settings,)

    @field_validator("application_service_log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("APPLICATION_SERVICE_LOG_LEVEL is invalid")
        return normalized

    @model_validator(mode="after")
    def check_sse_timeout_ordering(self) -> AppServiceSettings:
        """Heartbeat must fire strictly before the idle close (and both stay
        below the gateway's 300s idle cutoff)."""

        if (
            self.application_service_sse_heartbeat_interval_seconds
            >= self.application_service_sse_idle_timeout_seconds
        ):
            raise ValueError(
                "APPLICATION_SERVICE_SSE_HEARTBEAT_INTERVAL_SECONDS must be smaller than "
                "APPLICATION_SERVICE_SSE_IDLE_TIMEOUT_SECONDS"
            )
        if self.application_service_sse_idle_timeout_seconds >= 300:
            raise ValueError(
                "APPLICATION_SERVICE_SSE_IDLE_TIMEOUT_SECONDS must stay below the "
                "gateway's 300s idle timeout"
            )
        return self

    @property
    def database_url_sync(self) -> str:
        return self._database_url("postgresql+psycopg")

    @property
    def database_url_async(self) -> str:
        return self._database_url("postgresql+asyncpg")

    def _database_url(self, scheme: str) -> str:
        user = quote_plus(self.db_user)
        password = quote_plus(self.db_password.get_secret_value())
        database = quote_plus(self.db_name)
        return f"{scheme}://{user}:{password}@{self.db_host}:{self.db_port}/{database}"

    def redis_url_for_db(self, database: int) -> str:
        password = self.redis_password.get_secret_value()
        auth = f":{quote_plus(password)}@" if password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{database}"

    @property
    def redis_url(self) -> str:
        return self.redis_url_for_db(self.redis_db)

    def safe_summary(self) -> dict[str, Any]:
        """Return only non-sensitive startup metadata."""

        return {
            "service": self.service_name,
            "deployment_mode": self.deployment_mode,
            "host": self.application_service_host,
            "port": self.application_service_port,
            "auth_mode": self.application_service_auth_mode,
            "db_host": self.db_host,
            "db_port": self.db_port,
            "redis_host": self.redis_host,
            "redis_port": self.redis_port,
            "db_pool_size": self.application_service_db_pool_size,
        }


_configured_settings: AppServiceSettings | None = None


def configure_settings(settings: AppServiceSettings) -> None:
    """Record the process-level settings at assembly time.

    The monolith relies on the module-level `app.core.config.settings` singleton;
    services inject settings explicitly at assembly so `create_app(settings)`
    keeps test injection and runtime reads consistent.
    """

    global _configured_settings
    _configured_settings = settings


def current_settings() -> AppServiceSettings:
    """Return the effective settings; falls back to env construction when the
    app has not been assembled (script / bare unit-test scenarios)."""

    if _configured_settings is not None:
        return _configured_settings
    from .bootstrap import get_settings

    return get_settings()
