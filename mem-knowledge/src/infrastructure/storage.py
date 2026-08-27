"""Knowledge service adapter for shared storage backends."""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mem_storage import (
    LocalStorageConfig,
    MinIOStorageConfig,
    OSSStorageConfig,
    S3StorageConfig,
    StorageConfig,
    create_storage,
)

from ..config import KnowledgeSettings

BackendFactory = Callable[[StorageConfig], Any]


class StorageManager:
    """Lazily construct storage and expose a non-mutating readiness probe."""

    def __init__(
        self,
        settings: KnowledgeSettings,
        backend_factory: BackendFactory = create_storage,
    ):
        self._settings = settings
        self._backend_factory = backend_factory
        self._backend: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._backend is not None

    def _config(self) -> StorageConfig:
        if self._settings.storage_type == "local":
            return LocalStorageConfig(root_path=self._settings.file_path)
        if self._settings.storage_type == "oss":
            return OSSStorageConfig(
                endpoint=self._settings.oss_endpoint,
                access_key_id=self._settings.oss_access_key_id,
                access_key_secret=self._settings.oss_access_key_secret,
                bucket_name=self._settings.oss_bucket_name,
            )
        if self._settings.storage_type == "minio":
            return MinIOStorageConfig(
                endpoint_url=self._settings.minio_endpoint_url,
                access_key_id=self._settings.minio_access_key_id,
                secret_access_key=self._settings.minio_secret_access_key,
                bucket_name=self._settings.minio_bucket_name,
                region=self._settings.minio_region,
                ensure_bucket=True,
            )
        return S3StorageConfig(
            region=self._settings.s3_region,
            access_key_id=self._settings.s3_access_key_id,
            secret_access_key=self._settings.s3_secret_access_key,
            bucket_name=self._settings.s3_bucket_name,
            endpoint_url=self._settings.s3_endpoint_url or None,
        )

    async def backend(self) -> Any:
        async with self._lock:
            if self._backend is None:
                self._backend = await asyncio.to_thread(
                    self._backend_factory,
                    self._config(),
                )
            return self._backend

    async def ping(self) -> bool:
        backend = await self.backend()
        try:
            if self._settings.storage_type == "local":
                return await asyncio.to_thread(
                    self._local_path_ready,
                    self._settings.file_path,
                )
            if self._settings.storage_type == "oss":
                await asyncio.to_thread(backend.bucket.get_bucket_info)
                return True
            await asyncio.to_thread(
                backend.client.head_bucket,
                Bucket=backend.bucket_name,
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _local_path_ready(path: Path) -> bool:
        return (
            path.exists()
            and path.is_dir()
            and os.access(path, os.R_OK | os.W_OK | os.X_OK)
        )

    async def aclose(self) -> None:
        backend = self._backend
        self._backend = None
        if backend is None:
            return
        result = backend.aclose()
        if inspect.isawaitable(result):
            await result

    def reset_after_fork(self) -> None:
        self._backend = None
        self._lock = asyncio.Lock()
