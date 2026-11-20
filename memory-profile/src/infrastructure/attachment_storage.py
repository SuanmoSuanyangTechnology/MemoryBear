"""Bind service settings and lifecycle to the shared file storage package."""

from pathlib import Path

from mem_storage import (
    LocalStorageConfig,
    MinIOStorageConfig,
    OSSStorageConfig,
    S3StorageConfig,
    StorageBackend,
    create_storage,
)

from src.config import ProfileSettings


class AttachmentStorageManager:
    def __init__(self, settings: ProfileSettings) -> None:
        self.settings = settings
        self._backend: StorageBackend | None = None

    @property
    def backend(self) -> StorageBackend:
        if self._backend is None:
            settings = self.settings
            storage_type = settings.storage_type.lower()
            if storage_type == "local":
                config = LocalStorageConfig(
                    root_path=(Path("storage") / settings.file_path.lstrip("/")).resolve()
                )
            elif storage_type == "oss":
                config = OSSStorageConfig(
                    endpoint=settings.oss_endpoint,
                    access_key_id=settings.oss_access_key_id,
                    access_key_secret=settings.oss_access_key_secret,
                    bucket_name=settings.oss_bucket_name,
                )
            elif storage_type == "s3":
                config = S3StorageConfig(
                    region=settings.s3_region,
                    access_key_id=settings.s3_access_key_id,
                    secret_access_key=settings.s3_secret_access_key,
                    bucket_name=settings.s3_bucket_name,
                    endpoint_url=settings.s3_endpoint_url or None,
                )
            elif storage_type == "minio":
                config = MinIOStorageConfig(
                    region=settings.minio_region,
                    access_key_id=settings.minio_access_key_id,
                    secret_access_key=settings.minio_secret_access_key,
                    bucket_name=settings.minio_bucket_name,
                    endpoint_url=settings.minio_endpoint_url,
                )
            else:
                raise ValueError(f"Unsupported storage type: {storage_type}")
            self._backend = create_storage(config)
        return self._backend

    async def aclose(self) -> None:
        if self._backend is not None:
            await self._backend.aclose()
            self._backend = None
