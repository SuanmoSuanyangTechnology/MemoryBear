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

    memory_auth_mode: Literal["direct", "gateway"] = Field(
        "direct", validation_alias="MEMORY_AUTH_MODE"
    )
    memory_auth_service_name: str = Field(
        "memory-profile",
        min_length=1,
        validation_alias="MEMORY_AUTH_SERVICE_NAME",
    )
    memory_auth_header_prefix: str = Field(
        "X-Memory",
        min_length=1,
        validation_alias="MEMORY_AUTH_HEADER_PREFIX",
    )
    memory_auth_kill_switch_file: str | None = Field(
        None, validation_alias="MEMORY_AUTH_KILL_SWITCH_FILE"
    )
    memory_auth_jwks_url: str | None = Field(None, validation_alias="MEMORY_AUTH_JWKS_URL")

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
    database_name: str = Field(
        "postgres",
        validation_alias=AliasChoices(
            "DATABASE_NAME", "POSTGRES_DB", "POSTGRES_DATABASE", "DB_NAME"
        ),
    )
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

    @property
    def elasticsearch_hosts(self) -> list[str]:
        host = self.elasticsearch_host.rstrip("/")
        parsed = urlparse(host)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ELASTICSEARCH_HOST must be an absolute HTTP(S) URL")
        if parsed.port is not None:
            return [host]
        return [f"{host}:{self.elasticsearch_port}"]
