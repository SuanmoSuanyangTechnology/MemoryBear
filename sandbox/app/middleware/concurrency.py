"""
Queue-based concurrency control

N persistent worker coroutines consume tasks from an unbounded
FIFO queue, replacing the semaphore model.
"""
import asyncio

from app.config import get_config
from app.logger import get_logger

logger = get_logger()


class QueueController:
    """Unbounded queue + persistent worker pool."""

    def __init__(self):
        self._queue: asyncio.Queue | None = None
        self._workers: list[asyncio.Task] = []
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self):
        if self._running:
            return

        config = get_config()
        max_workers = config.max_workers

        self._queue = asyncio.Queue()          # unbounded
        self._running = True

        for i in range(max_workers):
            task = asyncio.create_task(self._worker(i))
            self._workers.append(task)

        logger.info("QueueController started: workers=%d", max_workers)

    async def stop(self):
        self._running = False

        for w in self._workers:
            w.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

        # Reject any futures still waiting
        if self._queue:
            while not self._queue.empty():
                try:
                    future, _ = self._queue.get_nowait()
                    if not future.done():
                        future.set_exception(RuntimeError("QueueController shutting down"))
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break

        logger.info("QueueController stopped")

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    async def _worker(self, worker_id: int):
        while self._running:
            try:
                future, coro_factory = await self._queue.get()
                try:
                    result = await coro_factory()
                    if not future.done():
                        future.set_result(result)
                except Exception as exc:
                    if not future.done():
                        future.set_exception(exc)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Worker %d unexpected error", worker_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def submit(self, coro_factory):
        """Enqueue a task and wait for its result."""
        if not self._running:
            self.start()

        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((future, coro_factory))
        return await future

    @property
    def stats(self) -> dict:
        q = self._queue
        return {
            "queue_size": q.qsize() if q else 0,
            "workers": len(self._workers),
        }


# Module-level singleton
queue_controller = QueueController()