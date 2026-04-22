import json
import threading
import time

import redis

from app.core.config import settings
from celery_app import celery_app
from core.logging_config import get_named_logger

logger = get_named_logger("task_scheduler")

STREAM_KEY = "celery_task_stream"
PENDING_HASH = "scheduler:pending_tasks"
TASK_TIMEOUT = 7800


def health_check_server():
    import uvicorn
    from fastapi import FastAPI

    health_app = FastAPI()

    @health_app.get("/")
    def health():
        return scheduler.health()

    threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": health_app,
            "host": "0.0.0.0",
            "port": 8001,
            "log_config": None
        },
        daemon=True
    ).start()
    logger.info(f"[Health] Server started at http://0.0.0.0:8001")


class RedisTaskScheduler:
    def __init__(self):
        self.redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB_CELERY_BACKEND,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
        )
        self.running = False
        self.dispatched = 0
        self.errors = 0
        self._leader = False

    def push_task(self, task_name, user_id, params):
        try:
            msg_id = self.redis.xadd(
                STREAM_KEY,
                fields={
                    "task_name": task_name,
                    "user_id": user_id,
                    "params": json.dumps(params),
                }
            )
            return msg_id
        except Exception as e:
            logger.error("Push task exception %s", e, exc_info=True)
            raise e

    def _cleanup_finished(self):
        pending = self.redis.hgetall(PENDING_HASH)
        if not pending:
            return

        now = time.time()
        task_ids = list(pending.keys())

        pipe = self.redis.pipeline()
        for task_id in task_ids:
            pipe.get(f"celery-task-meta-{task_id}")
        results = pipe.execute()

        cleanup_pipe = self.redis.pipeline()
        has_cleanup = False

        for task_id, raw_result in zip(task_ids, results):
            try:
                meta = json.loads(pending[task_id])
                lock_key = meta["lock_key"]
                dispatched_at = meta.get("dispatched_at", 0)
                age = now - dispatched_at

                should_cleanup = False
                if raw_result is not None:
                    result_data = json.loads(raw_result)
                    if result_data.get("status") in ("SUCCESS", "FAILURE", "REVOKED"):
                        should_cleanup = True
                        logger.info("Task finished: %s state=%s", task_id, result_data.get("status"))
                elif age > TASK_TIMEOUT:
                    should_cleanup = True
                    logger.warning(
                        "Task expired or lost: %s age=%.0fs, force cleanup",
                        task_id, age,
                    )

                if should_cleanup:
                    cleanup_pipe.delete(lock_key)
                    cleanup_pipe.hdel(PENDING_HASH, task_id)
                    has_cleanup = True
            except Exception as e:
                logger.error("Cleanup error for %s: %s", task_id, e, exc_info=True)
                self.errors += 1
        if has_cleanup:
            cleanup_pipe.execute()

    def _dispatch(self, msg_id, msg_data) -> bool:
        user_id = msg_data['user_id']
        task_name = msg_data['task_name']
        params = json.loads(msg_data.get('params', "{}"))

        lock_key = f"{task_name}:{user_id}"
        try:
            task = celery_app.send_task(task_name, kwargs=params)
            pipe = self.redis.pipeline()
            pipe.set(lock_key, task.id, ex=3600)
            pipe.hset(PENDING_HASH, task.id, json.dumps({
                "lock_key": lock_key,
                "dispatched_at": time.time()
            }))
            pipe.xdel(STREAM_KEY, msg_id)
            pipe.execute()
            self.dispatched += 1
            logger.info("Task dispatched: %s", task.id)
            return True
        except Exception as e:
            self.errors += 1
            logger.error("Task dispatch error for %s: %s", task_name, e, exc_info=True)
            return False

    def _leader_lock_extend(self, lock, interval=20):
        while self._leader:
            try:
                lock.extend(60)
            except redis.exceptions.LockNotOwnedError:
                logger.warning("Lost leader lock during extend")
                self._leader = False
            except Exception as e:
                logger.error("Lock extend error: %s", e)
            for _ in range(interval):
                if not self._leader:
                    break
                time.sleep(1)

    def schedule_loop(self):
        self.running = True
        self._cleanup_finished()
        resp = self.redis.xread(
            streams={STREAM_KEY: '0-0'},
            count=500,
            block=5000,
        )
        if not resp:
            return

        messages = []
        for stream_key, msgs in resp:
            messages.extend(msgs)

        lock_keys = []
        for msg_id, msg_data in messages:
            lock_keys.append(f"{msg_data['task_name']}:{msg_data['user_id']}")

        pipe = self.redis.pipeline()
        for key in lock_keys:
            pipe.exists(key)
        lock_exists = pipe.execute()

        deliver_keys = set()
        for (msg_id, msg_data), locked in zip(messages, lock_exists):
            user_id = msg_data['user_id']
            lock_key = f"{msg_data['task_name']}:{user_id}"

            if locked or lock_key in deliver_keys:
                continue

            key = self._dispatch(msg_id, msg_data)
            if key:
                deliver_keys.add(lock_key)
        time.sleep(0.1)

    def run_server(self):
        health_check_server()

        lock = self.redis.lock(
            "scheduler:leader",
            timeout=60,
            blocking_timeout=10,
            thread_local=False
        )
        while True:
            try:
                if lock.acquire(blocking=True):
                    self._leader = True
                    t = threading.Thread(
                        target=self._leader_lock_extend,
                        args=(lock, 20),
                        daemon=True
                    )
                    t.start()
                    try:
                        while self._leader:
                            self.schedule_loop()
                    finally:
                        self._leader = False
                        t.join(timeout=30)
                        try:
                            lock.release()
                        except redis.exceptions.LockNotOwnedError:
                            pass
                        self.running = False
                else:
                    time.sleep(5)
            except Exception as e:
                logger.error("Scheduler exception %s", e, exc_info=True)
                time.sleep(5)

    def health(self) -> dict:
        return {
            "running": self.running,
            "pending": self.redis.xlen(STREAM_KEY),
            "dispatched": self.dispatched,
            "errors": self.errors
        }


scheduler: RedisTaskScheduler | None = None
if scheduler is None:
    scheduler = RedisTaskScheduler()

if __name__ == '__main__':
    scheduler.run_server()
