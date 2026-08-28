"""有界的原地投影重试，带租约取消与终态失败。"""

import asyncio
import logging
from contextlib import AsyncExitStack, suppress

from app.core.memory.storage.outbox.clients import ProjectionClients
from app.core.memory.storage.outbox.exceptions import ClaimLostError, safe_error
from app.core.memory.storage.outbox.repository import create_repository
from app.core.memory.storage.outbox.types import MAX_ATTEMPTS

logger = logging.getLogger(__name__)


async def _consume_claim(event, repo, projector) -> str:
    lost = asyncio.Event()

    async def check_claim():
        if lost.is_set():
            raise ClaimLostError()
        try:
            owned = await repo.heartbeat(event.id, event.claim_token)
        except Exception:
            owned = False
        if not owned:
            lost.set()
            raise ClaimLostError()

    async def renew():
        while True:
            await asyncio.sleep(min(10, repo.processing_timeout / 3))
            try:
                await check_claim()
            except ClaimLostError:
                return

    async def project_with_lease():
        await check_claim()
        await projector(event, check_claim)

    async def run_attempt():
        task = asyncio.create_task(project_with_lease())
        lease_loss = asyncio.create_task(lost.wait())
        try:
            async with asyncio.timeout(min(60, repo.processing_timeout / 2)):
                done, _ = await asyncio.wait((task, lease_loss), return_when=asyncio.FIRST_COMPLETED)
                if lease_loss in done:
                    raise ClaimLostError()
                await task
        finally:
            for pending in (task, lease_loss):
                pending.cancel()
            await asyncio.gather(task, lease_loss, return_exceptions=True)

    heartbeat = asyncio.create_task(renew())
    try:
        for _ in range(MAX_ATTEMPTS):
            if lost.is_set():
                return "lost"
            attempt = await repo.begin_attempt(event.id, event.claim_token)
            if attempt is None:
                return "lost"
            try:
                await run_attempt()
            except ClaimLostError:
                return "lost"
            except Exception as exc:
                error = safe_error(exc, repo.error_max_length)
                if attempt == MAX_ATTEMPTS:
                    updated = await repo.mark_failed(
                        event.id,
                        event.claim_token,
                        error,
                    )
                    logger.error(
                        "Outbox event=%s terminal=%s attempt=%s error=%s",
                        event.id,
                        "failed" if updated else "lost",
                        attempt,
                        error,
                    )
                    return "failed" if updated else "lost"
                logger.warning(
                    "Outbox event=%s attempt=%s error=%s; retry in place",
                    event.id,
                    attempt,
                    error,
                )
            else:
                # 重要：PG 确认失败不在投影重试处理器范围内。
                # 此时 ES 可能已成功；绝不为该事件再次执行 ES。
                updated = await repo.mark_processed(event.id, event.claim_token)
                return "processed" if updated else "lost"
        return "lost"
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def consume_outbox_batch(batch_size: int, worker_id: str, *, repository=None,
                               projector=None) -> dict[str, int]:
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    async with AsyncExitStack() as stack:
        repo = repository or create_repository()
        if projector is None:
            clients = await stack.enter_async_context(ProjectionClients(
                request_timeout=min(30, repo.processing_timeout / 4),
            ))
            projector = clients.project
        stats = dict(claimed=0, processed=0, failed=0, lost=0,
                     expired=await repo.mark_expired_failed(batch_size))
        for _ in range(batch_size):
            # 串行容量为 1：不要为排队中的工作领取租约。
            claimed = await repo.claim_batch(worker_id, 1)
            if not claimed:
                break
            stats["claimed"] += 1
            outcome = await _consume_claim(claimed[0], repo, projector)
            stats[outcome] += 1
        if stats["expired"] or stats["lost"]:
            logger.error("Outbox lease failures: expired=%s lost=%s", stats["expired"], stats["lost"])
        return stats


async def cleanup_outbox_events(batch_size: int, *, repository=None) -> dict[str, int]:
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    async with AsyncExitStack() as stack:
        repo = repository or create_repository()
        totals = dict(processed=0, failed=0)
        # 有界的任务运行时间；每次清理调用提交各自独立的短事务。
        for _ in range(100):
            counts = await repo.cleanup(batch_size)
            for status in totals:
                totals[status] += counts[status]
            if all(counts[status] < batch_size for status in totals):
                break
        return totals
