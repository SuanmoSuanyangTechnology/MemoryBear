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
    # Per-node execution timeout of the workflow runtime (host
    # `settings.WORKFLOW_NODE_TIMEOUT`); same env name and default.
    workflow_node_timeout: int = Field(
        default=600, ge=1, validation_alias="WORKFLOW_NODE_TIMEOUT"
    )
    # Largest file the workflow runtime accepts for download/processing (host
    # `settings.MAX_FILE_SIZE`); same env name and default.
    max_file_size: int = Field(
        default=52428800, ge=1, validation_alias="MAX_FILE_SIZE"
    )
    max_file_count: int = Field(
        default=20, ge=1, validation_alias="MAX_FILE_COUNT"
    )
    # Sandbox HTTP API used by the code node (host `settings.SANDBOX_URL` /
    # `SANDBOX_API_KEY`); an empty URL fails the code node closed.
    sandbox_url: str = Field(default="", validation_alias="SANDBOX_URL")
    sandbox_api_key: SecretStr = Field(
        default=SecretStr("redbear-sandbox"), validation_alias="SANDBOX_API_KEY"
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
    # Usage-event stream key. Leave empty to derive ``model:usage:<DB_NAME>`` so
    # deployments that share one Redis but own separate databases consume only
    # their own events; set explicitly only when those deployments share a
    # database name as well.
    model_usage_stream: str = Field(default="", validation_alias="MODEL_USAGE_STREAM")

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

    # LangGraph checkpoint store (host `settings.LANGGRAPH_CHECKPOINT_*`); the
    # checkpoint Redis DB falls back to the shared REDIS_DB when unset.
    langgraph_checkpoint_backend: str = Field(
        default="redis", validation_alias="LANGGRAPH_CHECKPOINT_BACKEND"
    )
    langgraph_checkpoint_redis_db: int | None = Field(
        default=None, ge=0, validation_alias="LANGGRAPH_CHECKPOINT_REDIS_DB"
    )
    langgraph_checkpoint_pool_size: int = Field(
        default=20, ge=1, validation_alias="LANGGRAPH_CHECKPOINT_POOL_SIZE"
    )
    langgraph_checkpoint_ttl: int = Field(
        default=7 * 24 * 3600, ge=1, validation_alias="LANGGRAPH_CHECKPOINT_TTL"
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
    # ACL rule key and hot-reload channel. Empty derives
    # acl:rules:<DB_NAME> / acl:rules:changed:<DB_NAME> to match identity's
    # write side; deployments sharing one Redis but owning separate databases
    # stay isolated (pub/sub channels are global across logical DBs).
    acl_rules_key: str = Field(default="", validation_alias="ACL_RULES_KEY")
    acl_rules_changed_channel: str = Field(
        default="",
        validation_alias="ACL_RULES_CHANGED_CHANNEL",
    )

    # Monolith upstream channel (coexistence period): cross-domain writes and
    # memory/context-engine operations are forwarded to the monolith's
    # internal callback endpoints over the internal network, authenticated
    # with a dedicated shared secret (no platform credential involved). Unset
    # or unreachable upstreams fail the forward closed.
    application_service_upstream_base_url: str | None = Field(
        default=None,
        validation_alias="APPLICATION_SERVICE_UPSTREAM_BASE_URL",
    )
    application_service_callback_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="APPLICATION_SERVICE_CALLBACK_SECRET",
    )
    application_service_upstream_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias="APPLICATION_SERVICE_UPSTREAM_TIMEOUT_SECONDS",
    )
    # East-west direct dependency on model-service (annotation embeddings),
    # never routed through the gateway; same env name as the monolith's
    # outbound knob. Unset or unreachable degrades annotation writes to a
    # missing vector (the monolith's behavior when the model call fails).
    model_service_base_url: str | None = Field(
        default=None,
        validation_alias="MODEL_SERVICE_BASE_URL",
    )

    # East-west clients to mem-knowledge (KB retrieval face) and model-service
    # (runtime invocation face); same env names and defaults as the monolith
    # settings. Unset or unreachable clients fail the ported call sites closed.
    enable_mem_knowledge: bool = Field(
        default=False, validation_alias="ENABLE_MEM_KNOWLEDGE"
    )
    mem_knowledge_base_url: str = Field(default="", validation_alias="MEM_KNOWLEDGE_BASE_URL")
    mem_knowledge_connect_timeout_seconds: float = Field(
        default=5.0, gt=0, validation_alias="MEM_KNOWLEDGE_CONNECT_TIMEOUT_SECONDS"
    )
    mem_knowledge_pool_timeout_seconds: float = Field(
        default=5.0, gt=0, validation_alias="MEM_KNOWLEDGE_POOL_TIMEOUT_SECONDS"
    )
    mem_knowledge_read_timeout_seconds: float = Field(
        default=120.0, gt=0, validation_alias="MEM_KNOWLEDGE_READ_TIMEOUT_SECONDS"
    )
    mem_knowledge_write_timeout_seconds: float = Field(
        default=600.0, gt=0, validation_alias="MEM_KNOWLEDGE_WRITE_TIMEOUT_SECONDS"
    )
    mem_knowledge_stream_read_timeout_seconds: float = Field(
        default=600.0, gt=0, validation_alias="MEM_KNOWLEDGE_STREAM_READ_TIMEOUT_SECONDS"
    )
    mem_knowledge_max_connections: int = Field(
        default=100, ge=1, validation_alias="MEM_KNOWLEDGE_MAX_CONNECTIONS"
    )
    mem_knowledge_max_keepalive_connections: int = Field(
        default=20, ge=0, validation_alias="MEM_KNOWLEDGE_MAX_KEEPALIVE_CONNECTIONS"
    )
    mem_knowledge_health_timeout_seconds: float = Field(
        default=3.0, gt=0, validation_alias="MEM_KNOWLEDGE_HEALTH_TIMEOUT_SECONDS"
    )
    model_service_connect_timeout_seconds: float = Field(
        default=5.0, gt=0, validation_alias="MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS"
    )
    model_service_pool_timeout_seconds: float = Field(
        default=5.0, gt=0, validation_alias="MODEL_SERVICE_POOL_TIMEOUT_SECONDS"
    )
    model_service_read_timeout_seconds: float = Field(
        default=120.0, gt=0, validation_alias="MODEL_SERVICE_READ_TIMEOUT_SECONDS"
    )
    model_service_write_timeout_seconds: float = Field(
        default=60.0, gt=0, validation_alias="MODEL_SERVICE_WRITE_TIMEOUT_SECONDS"
    )
    model_service_max_connections: int = Field(
        default=100, ge=1, validation_alias="MODEL_SERVICE_MAX_CONNECTIONS"
    )
    model_service_max_keepalive_connections: int = Field(
        default=20, ge=0, validation_alias="MODEL_SERVICE_MAX_KEEPALIVE_CONNECTIONS"
    )
    model_service_invoke_connect_timeout_seconds: float = Field(
        default=5.0, gt=0, validation_alias="MODEL_SERVICE_INVOKE_CONNECT_TIMEOUT_SECONDS"
    )
    model_service_invoke_idle_timeout_seconds: float = Field(
        default=180.0, gt=0, validation_alias="MODEL_SERVICE_INVOKE_IDLE_TIMEOUT_SECONDS"
    )
    model_service_invoke_write_timeout_seconds: float = Field(
        default=60.0, gt=0, validation_alias="MODEL_SERVICE_INVOKE_WRITE_TIMEOUT_SECONDS"
    )
    model_service_invoke_media_idle_timeout_seconds: float = Field(
        default=720.0, gt=0, validation_alias="MODEL_SERVICE_INVOKE_MEDIA_IDLE_TIMEOUT_SECONDS"
    )
    # Model-channel credential master key (host `settings.MODEL_CREDENTIALS_KEY`);
    # base64 32-byte key backing the envelope cipher for channel secrets. An
    # empty value fails the cipher closed the moment a credential is built
    # (same behavior as the monolith).
    model_credentials_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="MODEL_CREDENTIALS_KEY"
    )
    # Least-used channel routing knobs (host `settings.MODEL_USAGE_*`); same env
    # names and defaults.
    model_usage_least_used_enabled: bool = Field(
        default=True, validation_alias="MODEL_USAGE_LEAST_USED_ENABLED"
    )
    model_usage_load_window_minutes: int = Field(
        default=15, ge=1, validation_alias="MODEL_USAGE_LOAD_WINDOW_MINUTES"
    )
    # Channel-resolution mode (host `settings.MODEL_CHANNEL_RESOLUTION`): a
    # migration-window switch consumed by the model config service.
    model_channel_resolution: str = Field(
        default="prefer", validation_alias="MODEL_CHANNEL_RESOLUTION"
    )
    # Agent history window (host `settings.AGENT_MAX_HISTORY`); same env name
    # and default.
    agent_max_history: int = Field(
        default=20, ge=1, validation_alias="AGENT_MAX_HISTORY"
    )
    # In-process batch persistence queue tuning (host `settings.BATCH_PERSIST_*`);
    # same env names and defaults.
    batch_persist_queue_size: int = Field(
        default=10000, ge=1, validation_alias="BATCH_PERSIST_QUEUE_SIZE"
    )
    batch_persist_max_batch: int = Field(
        default=50, ge=1, validation_alias="BATCH_PERSIST_MAX_BATCH"
    )
    batch_persist_max_wait_ms: int = Field(
        default=500, ge=0, validation_alias="BATCH_PERSIST_MAX_WAIT_MS"
    )
    batch_persist_put_timeout_ms: int = Field(
        default=100, ge=0, validation_alias="BATCH_PERSIST_PUT_TIMEOUT_MS"
    )
    workflow_node_execution_retention_enabled: bool = Field(
        default=True, validation_alias="WORKFLOW_NODE_EXECUTION_RETENTION_ENABLED"
    )
    # Reply-side emotion detection (host `settings.FAST_WRITE_EMOTION_*`); an
    # empty URL/API key/model disables the feature and degrades to no-op, same
    # as the monolith.
    fast_write_emotion_url: str = Field(
        default="", validation_alias="FAST_WRITE_EMOTION_URL"
    )
    fast_write_emotion_model: str = Field(
        default="", validation_alias="FAST_WRITE_EMOTION_MODEL"
    )
    fast_write_emotion_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="FAST_WRITE_EMOTION_API_KEY"
    )
    fast_write_emotion_http_timeout_seconds: float = Field(
        default=2.0, gt=0, validation_alias="FAST_WRITE_EMOTION_HTTP_TIMEOUT_SECONDS"
    )
    # E2B sandbox callback settings (host `settings.E2B_CALLBACK_*`); kept for
    # config-shape parity, the sandbox execution path itself is fail-closed.
    e2b_callback_url: str = Field(
        default="http://host.docker.internal:8000", validation_alias="E2B_CALLBACK_URL"
    )
    e2b_callback_secret: SecretStr = Field(
        default=SecretStr("sandbox-callback-secret"), validation_alias="E2B_CALLBACK_SECRET"
    )
    llm_network_retry_attempts: int = Field(
        default=3, ge=1, validation_alias="LLM_NETWORK_RETRY_ATTEMPTS"
    )
    knowledge_retrieval_graph_max_concurrency: int = Field(
        default=2, ge=1, validation_alias="KNOWLEDGE_RETRIEVAL_GRAPH_MAX_CONCURRENCY"
    )
    knowledge_graph_extract_max_concurrency: int = Field(
        default=4, ge=1, le=16, validation_alias="KNOWLEDGE_GRAPH_EXTRACT_MAX_CONCURRENCY"
    )
    knowledge_graph_retrieval_timeout_ms: int = Field(
        default=15000, ge=1000, le=30000, validation_alias="KNOWLEDGE_GRAPH_RETRIEVAL_TIMEOUT_MS"
    )

    # Object storage backend (host parity): local filesystem, MinIO, Aliyun OSS
    # or S3. Files land in the deployment's existing storage so the monolith
    # and this service read the same objects during the coexistence period.
    secret_key: SecretStr = Field(
        default=SecretStr("a_default_secret_key_that_is_long_and_random"),
        validation_alias="SECRET_KEY",
    )
    file_path: str = Field(default="/files", validation_alias="FILE_PATH")
    storage_type: str = Field(default="local", validation_alias="STORAGE_TYPE")
    minio_endpoint_url: str = Field(default="", validation_alias="MINIO_ENDPOINT_URL")
    minio_access_key_id: str = Field(default="", validation_alias="MINIO_ACCESS_KEY_ID")
    minio_secret_access_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="MINIO_SECRET_ACCESS_KEY"
    )
    minio_bucket_name: str = Field(default="", validation_alias="MINIO_BUCKET_NAME")
    minio_region: str = Field(default="", validation_alias="MINIO_REGION")
    oss_endpoint: str = Field(default="", validation_alias="OSS_ENDPOINT")
    oss_access_key_id: str = Field(default="", validation_alias="OSS_ACCESS_KEY_ID")
    oss_access_key_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="OSS_ACCESS_KEY_SECRET"
    )
    oss_bucket_name: str = Field(default="", validation_alias="OSS_BUCKET_NAME")
    s3_region: str = Field(default="", validation_alias="S3_REGION")
    s3_access_key_id: str = Field(default="", validation_alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="S3_SECRET_ACCESS_KEY"
    )
    s3_bucket_name: str = Field(default="", validation_alias="S3_BUCKET_NAME")
    s3_endpoint_url: str = Field(default="", validation_alias="S3_ENDPOINT_URL")
    speedbear_base_url: str = Field(
        default="https://testspeedbear.redbearai.com",
        validation_alias="SPEEDBEAR_BASE_URL",
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
    def check_usage_stream(self) -> AppServiceSettings:
        """Fail fast on an unusable usage stream key instead of writing to a wrong one."""

        stream = self.model_usage_stream_name
        if not stream:
            raise ValueError("MODEL_USAGE_STREAM resolved to an empty key")
        return self

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

    def _deployment_scoped_name(self, explicit: str, base: str, env_name: str) -> str:
        """Resolve a deployment-scoped Redis name from an override or DB_NAME."""

        explicit = explicit.strip()
        if explicit:
            if any(ch.isspace() for ch in explicit):
                raise ValueError(f"{env_name} must not contain whitespace")
            return explicit
        db_name = self.db_name.strip()
        if not db_name or any(ch.isspace() for ch in db_name):
            raise ValueError(
                f"DB_NAME must be non-empty without whitespace to derive {env_name}"
            )
        return f"{base}:{db_name}"

    @property
    def model_usage_stream_name(self) -> str:
        """Resolve the deployment-scoped usage stream key."""

        explicit = self.model_usage_stream.strip()
        if explicit:
            if any(ch.isspace() for ch in explicit):
                raise ValueError("MODEL_USAGE_STREAM must not contain whitespace")
            return explicit
        db_name = self.db_name.strip()
        if not db_name or any(ch.isspace() for ch in db_name):
            raise ValueError(
                "DB_NAME must be non-empty without whitespace to derive MODEL_USAGE_STREAM"
            )
        return f"model:usage:{db_name}"

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
