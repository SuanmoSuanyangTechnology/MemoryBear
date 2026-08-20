"""Validated configuration contracts for the knowledge service."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus, urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class KnowledgeSettings(BaseSettings):
    """Immutable settings constructed only from the bootstrap merge."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    service_name: Literal["mem-knowledge"] = "mem-knowledge"

    # Shared environment variables
    deployment_mode: str = Field(default="community", validation_alias="DEPLOYMENT_MODE")
    db_host: str = Field(default="127.0.0.1", validation_alias="DB_HOST")
    db_port: int = Field(default=5432, ge=1, le=65535, validation_alias="DB_PORT")
    db_user: str = Field(default="postgres", validation_alias="DB_USER")
    db_password: SecretStr = Field(default=SecretStr("password"), validation_alias="DB_PASSWORD")
    db_name: str = Field(default="redbear-mem", validation_alias="DB_NAME")
    redis_host: str = Field(default="127.0.0.1", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, ge=1, le=65535, validation_alias="REDIS_PORT")
    redis_password: SecretStr = Field(default=SecretStr(""), validation_alias="REDIS_PASSWORD")
    redis_db: int = Field(default=1, ge=0, validation_alias="REDIS_DB")
    redis_db_celery_broker: int = Field(
        default=3,
        ge=0,
        validation_alias="REDIS_DB_CELERY_BROKER",
    )
    redis_db_celery_backend: int = Field(
        default=4,
        ge=0,
        validation_alias="REDIS_DB_CELERY_BACKEND",
    )
    celery_broker_url_value: SecretStr | None = Field(
        default=None,
        validation_alias="CELERY_BROKER_URL",
    )
    celery_result_backend_value: SecretStr | None = Field(
        default=None,
        validation_alias="CELERY_RESULT_BACKEND",
    )

    elasticsearch_host: str = Field(
        default="https://127.0.0.1",
        validation_alias="ELASTICSEARCH_HOST",
    )
    elasticsearch_port: int = Field(
        default=9200,
        ge=1,
        le=65535,
        validation_alias="ELASTICSEARCH_PORT",
    )
    elasticsearch_username: str = Field(
        default="elastic",
        validation_alias="ELASTICSEARCH_USERNAME",
    )
    elasticsearch_password: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="ELASTICSEARCH_PASSWORD",
    )
    elasticsearch_verify_certs: bool = Field(
        default=False,
        validation_alias="ELASTICSEARCH_VERIFY_CERTS",
    )
    elasticsearch_ca_certs: str = Field(
        default="",
        validation_alias="ELASTICSEARCH_CA_CERTS",
    )
    elasticsearch_request_timeout: float = Field(
        default=100.0,
        gt=0,
        validation_alias="ELASTICSEARCH_REQUEST_TIMEOUT",
    )
    elasticsearch_retry_on_timeout: bool = Field(
        default=True,
        validation_alias="ELASTICSEARCH_RETRY_ON_TIMEOUT",
    )
    elasticsearch_max_retries: int = Field(
        default=10,
        ge=0,
        validation_alias="ELASTICSEARCH_MAX_RETRIES",
    )
    storage_type: Literal["local", "oss", "s3", "minio"] = Field(
        default="local",
        validation_alias="STORAGE_TYPE",
    )
    file_path: Path = Field(default=Path("/files"), validation_alias="FILE_PATH")
    oss_endpoint: str = Field(default="", validation_alias="OSS_ENDPOINT")
    oss_access_key_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="OSS_ACCESS_KEY_ID",
    )
    oss_access_key_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="OSS_ACCESS_KEY_SECRET",
    )
    oss_bucket_name: str = Field(default="", validation_alias="OSS_BUCKET_NAME")
    s3_region: str = Field(default="us-east-1", validation_alias="S3_REGION")
    s3_access_key_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="S3_ACCESS_KEY_ID",
    )
    s3_secret_access_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="S3_SECRET_ACCESS_KEY",
    )
    s3_bucket_name: str = Field(default="", validation_alias="S3_BUCKET_NAME")
    s3_endpoint_url: str = Field(default="", validation_alias="S3_ENDPOINT_URL")
    minio_endpoint_url: str = Field(default="", validation_alias="MINIO_ENDPOINT_URL")
    minio_access_key_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="MINIO_ACCESS_KEY_ID",
    )
    minio_secret_access_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="MINIO_SECRET_ACCESS_KEY",
    )
    minio_bucket_name: str = Field(default="", validation_alias="MINIO_BUCKET_NAME")
    minio_region: str = Field(default="us-east-1", validation_alias="MINIO_REGION")

    speedbear_base_url: str = Field(
        default="https://testspeedbear.redbearai.com",
        validation_alias="SPEEDBEAR_BASE_URL",
    )
    speedbear_auth_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="SPEEDBEAR_AUTH_KEY",
    )
    llm_timeout: float = Field(default=120.0, gt=0, validation_alias="LLM_TIMEOUT")
    llm_max_retries: int = Field(default=2, ge=0, validation_alias="LLM_MAX_RETRIES")
    embedding_batch_size: int = Field(
        default=10,
        ge=1,
        validation_alias="EMBEDDING_BATCH_SIZE",
    )
    model_concurrency: int = Field(default=5, ge=1, validation_alias="MODEL_CONCURRENCY")
    model_http_max_connections: int = Field(
        default=300,
        ge=1,
        validation_alias="MODEL_HTTP_MAX_CONNECTIONS",
    )
    model_http_max_keepalive_connections: int = Field(
        default=50,
        ge=0,
        validation_alias="MODEL_HTTP_MAX_KEEPALIVE_CONNECTIONS",
    )
    model_http_trust_env: bool = Field(
        default=True,
        validation_alias="MODEL_HTTP_TRUST_ENV",
    )
    bedrock_max_pool_connections: int = Field(
        default=50,
        ge=1,
        validation_alias="BEDROCK_MAX_POOL_CONNECTIONS",
    )
    bedrock_max_retries: int = Field(
        default=2,
        ge=0,
        validation_alias="BEDROCK_MAX_RETRIES",
    )

    # Knowledge service environment variables
    kb_host: str = Field(default="0.0.0.0", validation_alias="KB_HOST")
    kb_port: int = Field(default=8080, ge=1, le=65535, validation_alias="KB_PORT")
    kb_process_role: Literal[
        "api",
        "document_worker",
        "graphrag_worker",
        "qa_import_worker",
    ] = Field(default="api", validation_alias="KB_PROCESS_ROLE")
    kb_log_level: str = Field(default="INFO", validation_alias="KB_LOG_LEVEL")
    kb_db_pool_size: int = Field(
        default=20,
        ge=1,
        validation_alias="KB_DB_POOL_SIZE",
    )
    kb_db_max_overflow: int = Field(
        default=10,
        ge=0,
        validation_alias="KB_DB_MAX_OVERFLOW",
    )
    kb_db_pool_recycle: int = Field(
        default=1800,
        ge=1,
        validation_alias="KB_DB_POOL_RECYCLE",
    )
    kb_db_pool_timeout: int = Field(
        default=30,
        ge=1,
        validation_alias="KB_DB_POOL_TIMEOUT",
    )
    kb_db_pool_pre_ping: bool = Field(
        default=True,
        validation_alias="KB_DB_POOL_PRE_PING",
    )
    kb_db_statement_timeout_ms: int = Field(
        default=60000,
        ge=1,
        validation_alias="KB_DB_STATEMENT_TIMEOUT_MS",
    )
    kb_redis_pool_size: int = Field(
        default=50,
        ge=1,
        validation_alias="KB_REDIS_POOL_SIZE",
    )
    kb_es_connections_per_node: int = Field(
        default=10,
        ge=1,
        validation_alias="KB_ES_CONNECTIONS_PER_NODE",
    )
    kb_health_probe_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        validation_alias="KB_HEALTH_PROBE_TIMEOUT_SECONDS",
    )
    kb_worker_prefetch_multiplier: int = Field(
        default=1,
        ge=1,
        validation_alias="KB_WORKER_PREFETCH_MULTIPLIER",
    )
    kb_task_time_limit_seconds: int = Field(
        default=3600,
        ge=1,
        validation_alias="KB_TASK_TIME_LIMIT_SECONDS",
    )
    kb_task_soft_time_limit_seconds: int = Field(
        default=3000,
        ge=1,
        validation_alias="KB_TASK_SOFT_TIME_LIMIT_SECONDS",
    )
    kb_result_expires_seconds: int = Field(
        default=3600,
        ge=1,
        validation_alias="KB_RESULT_EXPIRES_SECONDS",
    )

    # Knowledge business environment variables
    max_file_size: int = Field(
        default=52428800,
        ge=1,
        validation_alias="MAX_FILE_SIZE",
    )
    max_chunk_batch_size: int = Field(
        default=8,
        ge=1,
        validation_alias="MAX_CHUNK_BATCH_SIZE",
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

    @field_validator("kb_log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("KB_LOG_LEVEL is invalid")
        return normalized

    @model_validator(mode="after")
    def validate_storage(self) -> KnowledgeSettings:
        if self.storage_type == "local" and not self.file_path.is_absolute():
            raise ValueError("FILE_PATH must be absolute for local storage")
        required: dict[str, tuple[tuple[str, str | SecretStr], ...]] = {
            "oss": (
                ("OSS_ENDPOINT", self.oss_endpoint),
                ("OSS_ACCESS_KEY_ID", self.oss_access_key_id),
                ("OSS_ACCESS_KEY_SECRET", self.oss_access_key_secret),
                ("OSS_BUCKET_NAME", self.oss_bucket_name),
            ),
            "s3": (
                ("S3_REGION", self.s3_region),
                ("S3_ACCESS_KEY_ID", self.s3_access_key_id),
                ("S3_SECRET_ACCESS_KEY", self.s3_secret_access_key),
                ("S3_BUCKET_NAME", self.s3_bucket_name),
            ),
            "minio": (
                ("MINIO_ENDPOINT_URL", self.minio_endpoint_url),
                ("MINIO_ACCESS_KEY_ID", self.minio_access_key_id),
                ("MINIO_SECRET_ACCESS_KEY", self.minio_secret_access_key),
                ("MINIO_BUCKET_NAME", self.minio_bucket_name),
            ),
        }
        for field_name, value in required.get(self.storage_type, ()):
            raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
            if not raw_value:
                raise ValueError(f"{field_name} is required for {self.storage_type} storage")
        if self.kb_task_soft_time_limit_seconds >= self.kb_task_time_limit_seconds:
            raise ValueError(
                "KB_TASK_SOFT_TIME_LIMIT_SECONDS must be less than "
                "KB_TASK_TIME_LIMIT_SECONDS"
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

    @property
    def celery_broker_url(self) -> str:
        if self.celery_broker_url_value:
            value = self.celery_broker_url_value.get_secret_value()
            if value:
                return value
        return self.redis_url_for_db(self.redis_db_celery_broker)

    @property
    def celery_result_backend(self) -> str:
        if self.celery_result_backend_value:
            value = self.celery_result_backend_value.get_secret_value()
            if value:
                return value
        return self.redis_url_for_db(self.redis_db_celery_backend)

    @property
    def elasticsearch_hosts(self) -> list[str]:
        parsed = urlparse(self.elasticsearch_host)
        if parsed.scheme:
            host = parsed.hostname or self.elasticsearch_host
            scheme = parsed.scheme
        else:
            host = self.elasticsearch_host
            scheme = "https"
        return [f"{scheme}://{host}:{self.elasticsearch_port}"]

    def safe_summary(self) -> dict[str, Any]:
        """Return only non-sensitive startup metadata."""

        return {
            "service": self.service_name,
            "deployment_mode": self.deployment_mode,
            "process_role": self.kb_process_role,
            "host": self.kb_host,
            "port": self.kb_port,
            "db_host": self.db_host,
            "db_port": self.db_port,
            "redis_host": self.redis_host,
            "redis_port": self.redis_port,
            "elasticsearch_hosts": self.elasticsearch_hosts,
            "storage_type": self.storage_type,
            "db_pool_size": self.kb_db_pool_size,
        }
