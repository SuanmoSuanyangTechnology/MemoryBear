"""Explicit storage backend configuration contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

S3_MIN_MULTIPART_PART_SIZE = 5 * 1024 * 1024
OSS_MIN_MULTIPART_PART_SIZE = 100 * 1024


class StorageType(StrEnum):
    LOCAL = "local"
    OSS = "oss"
    S3 = "s3"
    MINIO = "minio"


class StorageConfigBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LocalStorageConfig(StorageConfigBase):
    storage_type: Literal[StorageType.LOCAL] = StorageType.LOCAL
    root_path: Path

    @field_validator("root_path")
    @classmethod
    def require_absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("local storage root_path must be absolute")
        return value


class RemoteStorageConfig(StorageConfigBase):
    connection_pool_size: int = Field(default=16, ge=1)
    max_concurrency: int = Field(default=16, ge=1)

    @model_validator(mode="after")
    def validate_pool_capacity(self) -> RemoteStorageConfig:
        if self.connection_pool_size < self.max_concurrency:
            raise ValueError("connection_pool_size must be at least max_concurrency")
        return self


class OSSStorageConfig(RemoteStorageConfig):
    storage_type: Literal[StorageType.OSS] = StorageType.OSS
    endpoint: str = Field(min_length=1)
    access_key_id: SecretStr
    access_key_secret: SecretStr
    bucket_name: str = Field(min_length=1)
    connect_timeout_s: int = Field(default=30, ge=1)
    multipart_part_size: int = Field(
        default=5 * 1024 * 1024,
        ge=OSS_MIN_MULTIPART_PART_SIZE,
    )


class S3StorageConfig(RemoteStorageConfig):
    storage_type: Literal[StorageType.S3] = StorageType.S3
    region: str = Field(min_length=1)
    access_key_id: SecretStr
    secret_access_key: SecretStr
    bucket_name: str = Field(min_length=1)
    endpoint_url: str | None = None
    multipart_part_size: int = Field(
        default=S3_MIN_MULTIPART_PART_SIZE,
        ge=S3_MIN_MULTIPART_PART_SIZE,
    )


class MinIOStorageConfig(RemoteStorageConfig):
    storage_type: Literal[StorageType.MINIO] = StorageType.MINIO
    region: str = "us-east-1"
    access_key_id: SecretStr
    secret_access_key: SecretStr
    bucket_name: str = Field(min_length=1)
    endpoint_url: str = Field(min_length=1)
    multipart_part_size: int = Field(
        default=S3_MIN_MULTIPART_PART_SIZE,
        ge=S3_MIN_MULTIPART_PART_SIZE,
    )
    ensure_bucket: bool = True


StorageConfig: TypeAlias = Annotated[
    LocalStorageConfig | OSSStorageConfig | S3StorageConfig | MinIOStorageConfig,
    Field(discriminator="storage_type"),
]
