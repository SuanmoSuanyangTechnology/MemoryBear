"""Prevent concurrent duplicate uploads without holding a database session during storage IO."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..errors import KnowledgeError
from ..models.owned import FILE_ROLE_SOURCE, File, UploadFingerprint
from ..runtime import ProcessRuntime
from . import file as file_service
from .knowledge_file_storage import KnowledgeFileStorage

logger = logging.getLogger(__name__)
LEASE_TTL_SECONDS = 120
LEASE_RENEW_SECONDS = 30
LEASE_WAIT_SECONDS = 10


class DuplicateUploadError(Exception):
    def __init__(self, existing_file_name: str) -> None:
        super().__init__("KB_FILE_DUPLICATE")
        self.existing_file_name = existing_file_name


async def require_new_content(db: AsyncSession, plan: file_service.UploadPlan) -> None:
    filename = await db.scalar(
        select(File.file_name)
        .join(UploadFingerprint, UploadFingerprint.file_id == File.id)
        .where(
            UploadFingerprint.kb_id == plan.kb_id,
            UploadFingerprint.digest == plan.digest,
            File.kb_id == plan.kb_id,
            File.file_role == FILE_ROLE_SOURCE,
        )
        .order_by(File.created_at, File.id)
        .limit(1)
    )
    if filename is not None:
        raise DuplicateUploadError(filename)


@asynccontextmanager
async def upload_lease(
    runtime: ProcessRuntime,
    plan: file_service.UploadPlan,
) -> AsyncIterator[Callable[[], Awaitable[None]]]:
    redis = await runtime.redis.client()
    key = f"kb:upload:{runtime.settings.db_name}:{plan.kb_id}:{plan.digest}"
    lock = redis.lock(
        key, timeout=LEASE_TTL_SECONDS, blocking_timeout=LEASE_WAIT_SECONDS, thread_local=False,
    )
    try:
        acquired = await lock.acquire()
    except RedisError as exc:
        raise KnowledgeError.from_code("KB_HTTP_UNAVAILABLE") from exc
    if not acquired:
        raise KnowledgeError.from_code("KB_HTTP_UNAVAILABLE", "Upload lease wait timed out")

    lost = False

    async def renew() -> None:
        nonlocal lost
        while True:
            await asyncio.sleep(LEASE_RENEW_SECONDS)
            try:
                if await lock.extend(LEASE_TTL_SECONDS, replace_ttl=True):
                    continue
            except RedisError:
                logger.exception(
                    "续期上传判重租约失败 kb_id=%s file_id=%s", plan.kb_id, plan.file_id,
                )
            lost = True
            return

    async def ensure_owned() -> None:
        try:
            if not lost and await lock.owned():
                return
        except RedisError as exc:
            raise KnowledgeError.from_code("KB_HTTP_UNAVAILABLE") from exc
        raise KnowledgeError.from_code("KB_HTTP_UNAVAILABLE", "Upload lease lost")

    renewal = asyncio.create_task(renew())
    try:
        yield ensure_owned
    finally:
        renewal.cancel()
        with suppress(asyncio.CancelledError):
            await renewal
        try:
            await lock.release()
        except RedisError:
            logger.exception("释放上传判重租约失败 kb_id=%s file_id=%s", plan.kb_id, plan.file_id)


async def save_new_upload(
    runtime: ProcessRuntime,
    storage: KnowledgeFileStorage,
    plan: file_service.UploadPlan,
    principal: Principal,
    content: bytes,
    content_type: str | None,
) -> file_service.UploadOutcome:
    async with upload_lease(runtime, plan) as ensure_owned:
        async with runtime.database.async_session() as db:
            await require_new_content(db, plan)
        await storage.upload(plan.file_key, content, content_type)
        try:
            await ensure_owned()
            async with runtime.database.async_session() as db:
                return await file_service.persist_uploaded_content(db, plan, principal)
        except Exception:
            await file_service.compensate_storage_upload(storage, plan.file_key)
            raise
