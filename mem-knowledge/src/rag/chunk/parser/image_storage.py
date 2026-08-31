"""Thin parser-facing adapter for derived image asset persistence."""

from __future__ import annotations

from ....runtime import get_worker_runtime
from ..image_assets import StoredMinerUImageAsset
from ..image_assets import cleanup_mineru_v3_images as _cleanup
from ..image_assets import store_mineru_v3_image as _store


def store_mineru_v3_image(*, runtime=None, **kwargs) -> StoredMinerUImageAsset | None:
    return _store(runtime or get_worker_runtime(), **kwargs)


def cleanup_mineru_v3_images(document_id, retained_file_ids=None, *, runtime=None) -> int:
    return _cleanup(runtime or get_worker_runtime(), document_id, retained_file_ids)


__all__ = [
    "StoredMinerUImageAsset",
    "cleanup_mineru_v3_images",
    "store_mineru_v3_image",
]
