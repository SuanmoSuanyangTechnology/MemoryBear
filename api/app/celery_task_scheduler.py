import hashlib
import json
import os
import socket
import threading
import time
import uuid

import redis

from app.core.config import settings
from app.core.logging_config import get_named_logger
from app.celery_app import celery_app

logger = get_named_logger("task_scheduler")

# per-user queue scheduler:uq:{user_id}
USER_QUEUE_PREFIX = "scheduler:uq:"
# User Collection of Pending Messages
ACTIVE_USERS = "scheduler:active_users"
# Set of users that can dispatch (ready signal)
READY_SET = "scheduler:ready_users"
# Metadata of tasks that have been dispatched and are pending completion
PENDING_HASH = "scheduler:pending_tasks"
# Dynamic Sharding: Instance Registry
REGISTRY_KEY = "scheduler:instances"

TASK_TIMEOUT = 7800  # Task timeout (seconds), considered lost if exceeded
HEARTBEAT_INTERVAL = 10  # Heartbeat interval (seconds)
INSTANCE_TTL = 30  # Instance timeout (seconds)

LUA_ATOMIC_LOCK = """
local dispatch_lock = KEYS[1]
local lock_key = KEYS[2]
local instance_id = ARGV[1]
local dispatch_ttl = tonumber(ARGV[2])
local lock_ttl = tonumber(ARGV[3])

if redis.call('SET', dispatch_lock, instance_id, 'NX', 'EX', dispatch_ttl) == false then
    return 0
end

if redis.call('EXISTS', lock_key) == 1 then
    redis.call('DEL', dispatch_lock)
    return -1
end

redis.call('SET', lock_key, 'dispatching', 'EX', lock_ttl)
return 1
"""

LUA_SAFE_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def stable_hash(value: str) -> int:
    return int.from_bytes(
        hashlib.md5(value.encode("utf-8")).digest(),
        "big"
    )


def health_check_server(scheduler_ref):
    import uvicorn
    from fastapi import FastAPI

    health_app = FastAPI()

    @health_app.get("/")
    def health():
        return scheduler_ref.health()

    port = int(os.environ.get("SCHEDULER_HEALTH_PORT", "8001"))
    threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": health_app,
            "host": "0.0.0.0",
            "port": port,
            "log_config": None,
        },
        daemon=True,
    ).start()
    logger.info("[Health] Server started at http://0.0.0.0:%s", port)


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

        self.instance_id = f"{socket.gethostname()}-{os.getpid()}"
        self._shard_index = 0
        self._shard_count = 1
        self._last_heartbeat = 0.0

    def push_task(self, task_name, user_id, params):
        try:
            msg_id = str(uuid.uuid4())
            msg = json.dumps({
                "msg_id": msg_id,
                "task_name": task_name,
                "user_id": user_id,
                "params": json.dumps(params),
            })

            lock_key = f"{task_name}:{user_id}"
            queue_key = f"{USER_QUEUE_PREFIX}{user_id}"

            pipe = self.redis.pipeline()
            pipe.rpush(queue_key, msg)
            pipe.sadd(ACTIVE_USERS, user_id)
            pipe.set(
                f"task_tracker:{msg_id}",
                json.dumps({"status": "QUEUED", "task_id": None}),
                ex=86400,
            )
            pipe.execute()

            if not self.redis.exists(lock_key):
                self.redis.sadd(READY_SET, user_id)

            logger.info("Task pushed: msg_id=%s task=%s user=%s", msg_id, task_name, user_id)
            return msg_id
        except Exception as e:
            logger.error("Push task exception %s", e, exc_info=True)
            raise

    def get_task_status(self, msg_id: str) -> dict:
        raw = self.redis.get(f"task_tracker:{msg_id}")
        if raw is None:
            return {"status": "NOT_FOUND"}

        tracker = json.loads(raw)
        status = tracker["status"]
        task_id = tracker.get("task_id")
        result_content = tracker.get("result") or {}

        if status == "DISPATCHED" and task_id:
            result_raw = self.redis.get(f"celery-task-meta-{task_id}")
            if result_raw:
                result_data = json.loads(result_raw)
                status = result_data.get("status", status)
                result_content = result_data.get("result")

        return {"status": status, "task_id": task_id, "result": result_content}

    def _cleanup_finished(self):
        cursor = 0
        all_pending = {}
        while True:
            cursor, batch = self.redis.hscan(PENDING_HASH, cursor=cursor, count=100)
            all_pending.update(batch)
            if cursor == 0:
                break

        if not all_pending:
            return

        now = time.time()
        task_ids = list(all_pending.keys())

        pipe = self.redis.pipeline()
        for task_id in task_ids:
            pipe.get(f"celery-task-meta-{task_id}")
        results = pipe.execute()

        cleanup_pipe = self.redis.pipeline()
        has_cleanup = False
        ready_user_ids = set()

        for task_id, raw_result in zip(task_ids, results):
            try:
                meta = json.loads(all_pending[task_id])
                lock_key = meta["lock_key"]
                dispatched_at = meta.get("dispatched_at", 0)
                age = now - dispatched_at

                should_cleanup = False
                result_data = {}

                if raw_result is not None:
                    result_data = json.loads(raw_result)
                    if result_data.get("status") in ("SUCCESS", "FAILURE", "REVOKED"):
                        should_cleanup = True
                        logger.info(
                            "Task finished: %s state=%s", task_id,
                            result_data.get("status"),
                        )
                elif age > TASK_TIMEOUT:
                    should_cleanup = True
                    logger.warning(
                        "Task expired or lost: %s age=%.0fs, force cleanup",
                        task_id, age,
                    )

                if should_cleanup:
                    final_status = (
                        result_data.get("status", "UNKNOWN") if result_data else "EXPIRED"
                    )

                    self.redis.eval(LUA_SAFE_DELETE, 1, lock_key, task_id)

                    cleanup_pipe.hdel(PENDING_HASH, task_id)

                    tracker_msg_id = meta.get("msg_id")
                    if tracker_msg_id:
                        cleanup_pipe.set(
                            f"task_tracker:{tracker_msg_id}",
                            json.dumps({
                                "status": final_status,
                                "task_id": task_id,
                                "result": result_data.get("result") or {},
                            }),
                            ex=86400,
                        )
                    has_cleanup = True

                    parts = lock_key.split(":", 1)
                    if len(parts) == 2:
                        ready_user_ids.add(parts[1])

            except Exception as e:
                logger.error("Cleanup error for %s: %s", task_id, e, exc_info=True)
                self.errors += 1

        if has_cleanup:
            cleanup_pipe.execute()

        if ready_user_ids:
            self.redis.sadd(READY_SET, *ready_user_ids)

    def _heartbeat(self):
        now = time.time()
        if now - self._last_heartbeat < HEARTBEAT_INTERVAL:
            return
        self._last_heartbeat = now

        self.redis.hset(REGISTRY_KEY, self.instance_id, str(now))

        all_instances = self.redis.hgetall(REGISTRY_KEY)

        alive = []
        dead = []
        for iid, ts in all_instances.items():
            if now - float(ts) < INSTANCE_TTL:
                alive.append(iid)
            else:
                dead.append(iid)

        if dead:
            pipe = self.redis.pipeline()
            for iid in dead:
                pipe.hdel(REGISTRY_KEY, iid)
            pipe.execute()
            logger.info("Cleaned dead instances: %s", dead)

        alive.sort()
        self._shard_count = max(len(alive), 1)
        self._shard_index = (
            alive.index(self.instance_id) if self.instance_id in alive else 0
        )
        logger.debug(
            "Shard: %s/%s (instance=%s, alive=%d)",
            self._shard_index, self._shard_count,
            self.instance_id, len(alive),
        )

    def _is_mine(self, user_id: str) -> bool:
        if self._shard_count <= 1:
            return True
        return stable_hash(user_id) % self._shard_count == self._shard_index

    def _commit_post_dispatch(self, lock_key, task, msg_id, dispatch_lock):
        pipe = self.redis.pipeline()
        pipe.set(lock_key, task.id, ex=3600)
        pipe.hset(PENDING_HASH, task.id, json.dumps({
            "lock_key": lock_key,
            "dispatched_at": time.time(),
            "msg_id": msg_id,
        }))
        pipe.delete(dispatch_lock)
        pipe.set(
            f"task_tracker:{msg_id}",
            json.dumps({"status": "DISPATCHED", "task_id": task.id}),
            ex=86400,
        )
        pipe.execute()

    def _dispatch(self, msg_id, msg_data) -> bool:
        user_id = msg_data["user_id"]
        task_name = msg_data["task_name"]
        params = json.loads(msg_data.get("params", "{}"))

        lock_key = f"{task_name}:{user_id}"
        dispatch_lock = f"dispatch:{msg_id}"

        result = self.redis.eval(
            LUA_ATOMIC_LOCK, 2,
            dispatch_lock, lock_key,
            self.instance_id, str(300), str(3600),
        )

        if result == 0:
            return False
        if result == -1:
            return False

        try:
            task = celery_app.send_task(task_name, kwargs=params)
        except Exception as e:
            pipe = self.redis.pipeline()
            pipe.delete(dispatch_lock)
            pipe.delete(lock_key)
            pipe.execute()
            self.errors += 1
            logger.error(
                "send_task failed for %s:%s msg=%s: %s",
                task_name, user_id, msg_id, e, exc_info=True,
            )
            return False
        for attempt in range(2):
            try:
                self._commit_post_dispatch(lock_key, task, msg_id, dispatch_lock)
                break
            except Exception as e:
                logger.error(
                    "Post-dispatch state update failed for %s: %s",
                    task.id, e, exc_info=True,
                )
                time.sleep(0.1)
                self.errors += 1

        self.dispatched += 1
        logger.info("Task dispatched: %s (msg=%s)", task.id, msg_id)
        return True

    def _process_batch(self, user_ids):
        if not user_ids:
            return

        pipe = self.redis.pipeline()
        for uid in user_ids:
            pipe.lindex(f"{USER_QUEUE_PREFIX}{uid}", 0)
        heads = pipe.execute()

        candidates = []  # (user_id, msg_dict)
        empty_users = []

        for uid, head in zip(user_ids, heads):
            if head is None:
                empty_users.append(uid)
            else:
                try:
                    candidates.append((uid, json.loads(head)))
                except (json.JSONDecodeError, TypeError) as e:
                    logger.error("Bad message in queue for user %s: %s", uid, e)
                    self.redis.lpop(f"{USER_QUEUE_PREFIX}{uid}")

        if empty_users:
            pipe = self.redis.pipeline()
            for uid in empty_users:
                pipe.srem(ACTIVE_USERS, uid)
            pipe.execute()

        if not candidates:
            return

        for uid, msg in candidates:
            queue_key = f"{USER_QUEUE_PREFIX}{uid}"
            if self._dispatch(msg["msg_id"], msg):
                self.redis.lpop(queue_key)
                if self.redis.llen(queue_key) > 0:
                    self.redis.sadd(READY_SET, uid)

    def schedule_loop(self):
        self._heartbeat()
        self._cleanup_finished()

        ready_users = self.redis.smembers(READY_SET) or set()
        my_users = [uid for uid in ready_users if self._is_mine(uid)]
        if my_users:
            self.redis.srem(READY_SET, *my_users)
        else:
            time.sleep(0.5)
            return

        self._process_batch(my_users)
        time.sleep(0.1)

    def _full_scan(self):
        cursor = 0
        ready_batch = []
        while True:
            cursor, user_ids = self.redis.sscan(
                ACTIVE_USERS, cursor=cursor, count=1000,
            )
            if user_ids:
                my_users = [uid for uid in user_ids if self._is_mine(uid)]
                if my_users:
                    pipe = self.redis.pipeline()
                    for uid in my_users:
                        pipe.lindex(f"{USER_QUEUE_PREFIX}{uid}", 0)
                    heads = pipe.execute()

                    for uid, head in zip(my_users, heads):
                        if head is None:
                            continue
                        try:
                            msg = json.loads(head)
                            lock_key = f"{msg['task_name']}:{uid}"
                            ready_batch.append((uid, lock_key))
                        except (json.JSONDecodeError, TypeError):
                            continue

            if cursor == 0:
                break

        if not ready_batch:
            return

        pipe = self.redis.pipeline()
        for _, lock_key in ready_batch:
            pipe.exists(lock_key)
        lock_exists = pipe.execute()

        ready_uids = [
            uid for (uid, _), locked in zip(ready_batch, lock_exists)
            if not locked
        ]

        if ready_uids:
            self.redis.sadd(READY_SET, *ready_uids)
            logger.info("Full scan found %d ready users", len(ready_uids))

    def run_server(self):
        health_check_server(self)
        self.running = True

        last_full_scan = 0.0
        full_scan_interval = 30.0

        logger.info(
            "Scheduler started: instance=%s", self.instance_id,
        )

        while self.running:
            try:
                self.schedule_loop()

                now = time.time()
                if now - last_full_scan > full_scan_interval:
                    self._full_scan()
                    last_full_scan = now

            except Exception as e:
                logger.error("Scheduler exception %s", e, exc_info=True)
                self.errors += 1
                time.sleep(5)

    def health(self) -> dict:
        return {
            "running": self.running,
            "active_users": self.redis.scard(ACTIVE_USERS),
            "ready_users": self.redis.scard(READY_SET),
            "pending_tasks": self.redis.hlen(PENDING_HASH),
            "dispatched": self.dispatched,
            "errors": self.errors,
            "shard": f"{self._shard_index}/{self._shard_count}",
            "instance": self.instance_id,
        }

    def shutdown(self):
        logger.info("Scheduler shutting down: instance=%s", self.instance_id)
        self.running = False
        try:
            self.redis.hdel(REGISTRY_KEY, self.instance_id)
        except Exception as e:
            logger.error("Shutdown cleanup error: %s", e)


scheduler = RedisTaskScheduler()

if __name__ == "__main__":
    import signal
    import sys


    def _signal_handler(signum, frame):
        scheduler.shutdown()
        sys.exit(0)


    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    scheduler.run_server()
