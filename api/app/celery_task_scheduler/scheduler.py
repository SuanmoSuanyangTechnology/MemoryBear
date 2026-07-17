import hashlib
import json
import os
import socket
import threading
import time
import uuid
from pathlib import Path

import redis

from app.celery_app import celery_app
from app.core.config import settings
from app.core.exceptions import RateLimitException
from app.core.logging_config import get_named_logger

logger = get_named_logger("task_scheduler")

# per-unit queue scheduler:uq:{task_name}:{user_id}
USER_QUEUE_PREFIX = "scheduler:uq:"
# Collection of scheduling units (task_name:user_id) with pending messages
ACTIVE_UNITS = "scheduler:active_units"
# Set of scheduling units that can dispatch (ready signal)
READY_SET = "scheduler:ready_units"
# Metadata of tasks that have been dispatched and are pending completion
PENDING_HASH = "scheduler:pending_tasks"
# Dynamic Sharding: Instance Registry
REGISTRY_KEY = "scheduler:instances"
# Namespaced task-status tracker (client polling): scheduler:tracker:{msg_id}
TRACKER_PREFIX = "scheduler:tracker:"
# Namespaced per-message dispatch lock: scheduler:dispatch_lock:{msg_id}
DISPATCH_LOCK_PREFIX = "scheduler:dispatch_lock:"

TASK_TIMEOUT = 7800  # Task timeout (seconds), considered lost if exceeded
HEARTBEAT_INTERVAL = 10  # Heartbeat interval (seconds)
INSTANCE_TTL = 30  # Instance timeout (seconds)

_LUA_DIR = Path(__file__).parent / "lua_scripts"
_LUA_ATOMIC_LOCK = (_LUA_DIR / "atomic_lock.lua").read_text()
_LUA_SAFE_DELETE = (_LUA_DIR / "safe_delete.lua").read_text()
_LUA_MIGRATE_HEAD = (_LUA_DIR / "migrate_head.lua").read_text()
_LUA_MIGRATE_TRACKER = (_LUA_DIR / "migrate_tracker.lua").read_text()

# --- Legacy migration (per-user queue -> per-unit queue) ---
LEGACY_ACTIVE_USERS = "scheduler:active_users"
LEGACY_READY_SET = "scheduler:ready_users"
MIGRATION_LOCK = "scheduler:migration:v2_unit_queue"

# --- Legacy migration (task_tracker:{msg_id} -> scheduler:tracker:{msg_id}) ---
LEGACY_TRACKER_PREFIX = "task_tracker:"
TRACKER_MIGRATION_LOCK = "scheduler:migration:v2_tracker_keys"


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

    async def push_task(self, task_name, user_id, params):
        from app.celery_task_scheduler.rate_policy import AdmissionCtx, enforce_admission
        try:
            msg_id = str(uuid.uuid4())
            msg = json.dumps({
                "msg_id": msg_id,
                "task_name": task_name,
                "user_id": user_id,
                "params": json.dumps(params, default=str),
            })

            ctx = AdmissionCtx(
                task_name=task_name,
                user_id=user_id,
                msg=msg,
                msg_id=msg_id,
                unit_key=f"{task_name}:{user_id}",
                queue_key=f"{USER_QUEUE_PREFIX}{task_name}:{user_id}",
                active_units_key=ACTIVE_UNITS,
                ready_set_key=READY_SET,
                tracker_key=f"{TRACKER_PREFIX}{msg_id}",
                tracker_val=json.dumps({"status": "QUEUED", "task_id": None}),
            )

            await enforce_admission(self.redis, ctx=ctx)

            logger.info("Task pushed: msg_id=%s task=%s user=%s", msg_id, task_name, user_id)
            return msg_id
        except RateLimitException:
            raise
        except Exception as e:
            logger.error("Push task exception %s", e, exc_info=True)
            raise

    def get_task_status(self, msg_id: str) -> dict:
        raw = self.redis.get(f"{TRACKER_PREFIX}{msg_id}")
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
        ready_units = set()

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

                    self.redis.eval(_LUA_SAFE_DELETE, 1, lock_key, task_id)

                    cleanup_pipe.hdel(PENDING_HASH, task_id)

                    tracker_msg_id = meta.get("msg_id")
                    if tracker_msg_id:
                        cleanup_pipe.set(
                            f"{TRACKER_PREFIX}{tracker_msg_id}",
                            json.dumps({
                                "status": final_status,
                                "task_id": task_id,
                                "result": result_data.get("result") or {},
                            }),
                            ex=86400,
                        )
                    has_cleanup = True

                    # lock_key == unit_key (task_name:user_id)
                    ready_units.add(lock_key)

            except Exception as e:
                logger.error("Cleanup error for %s: %s", task_id, e, exc_info=True)
                self.errors += 1

        if has_cleanup:
            cleanup_pipe.execute()

        if ready_units:
            self.redis.sadd(READY_SET, *ready_units)

    def _heartbeat(self):
        now = time.time()
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

    def _is_mine(self, unit_key: str) -> bool:
        if self._shard_count <= 1:
            return True
        # unit_key == "{task_name}:{user_id}"; shard by user_id so that all
        # task types of the same user are owned by the same instance.
        user_id = unit_key.split(":", 1)[1] if ":" in unit_key else unit_key
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
            f"{TRACKER_PREFIX}{msg_id}",
            json.dumps({"status": "DISPATCHED", "task_id": task.id}),
            ex=86400,
        )
        pipe.execute()

    def _dispatch(self, msg_id, msg_data) -> bool:
        user_id = msg_data["user_id"]
        task_name = msg_data["task_name"]
        params = json.loads(msg_data.get("params", "{}"))

        lock_key = f"{task_name}:{user_id}"
        dispatch_lock = f"{DISPATCH_LOCK_PREFIX}{msg_id}"

        result = self.redis.eval(
            _LUA_ATOMIC_LOCK, 2,
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

    def _process_batch(self, unit_keys):
        if not unit_keys:
            return

        pipe = self.redis.pipeline()
        for uk in unit_keys:
            pipe.lindex(f"{USER_QUEUE_PREFIX}{uk}", 0)
        heads = pipe.execute()

        candidates = []  # (unit_key, msg_dict)
        empty_units = []

        for uk, head in zip(unit_keys, heads):
            if head is None:
                empty_units.append(uk)
            else:
                try:
                    candidates.append((uk, json.loads(head)))
                except (json.JSONDecodeError, TypeError) as e:
                    logger.error("Bad message in queue for unit %s: %s", uk, e)
                    self.redis.lpop(f"{USER_QUEUE_PREFIX}{uk}")

        if empty_units:
            pipe = self.redis.pipeline()
            for uk in empty_units:
                pipe.srem(ACTIVE_UNITS, uk)
            pipe.execute()

        if not candidates:
            return

        for uk, msg in candidates:
            queue_key = f"{USER_QUEUE_PREFIX}{uk}"
            if self._dispatch(msg["msg_id"], msg):
                self.redis.lpop(queue_key)
                if self.redis.llen(queue_key) > 0:
                    self.redis.sadd(READY_SET, uk)

    def schedule_loop(self):
        self._cleanup_finished()

        ready_units = self.redis.smembers(READY_SET) or set()
        my_units = [uk for uk in ready_units if self._is_mine(uk)]
        if my_units:
            self.redis.srem(READY_SET, *my_units)
        else:
            time.sleep(0.5)
            return

        self._process_batch(my_units)
        time.sleep(0.1)

    def _full_scan(self):
        cursor = 0
        ready_batch = []  # unit_key (== lock_key)
        while True:
            cursor, unit_keys = self.redis.sscan(
                ACTIVE_UNITS, cursor=cursor, count=1000,
            )
            if unit_keys:
                my_units = [uk for uk in unit_keys if self._is_mine(uk)]
                if my_units:
                    pipe = self.redis.pipeline()
                    for uk in my_units:
                        pipe.lindex(f"{USER_QUEUE_PREFIX}{uk}", 0)
                    heads = pipe.execute()

                    for uk, head in zip(my_units, heads):
                        if head is None:
                            continue
                        # unit_key already equals the lock_key
                        ready_batch.append(uk)

            if cursor == 0:
                break

        if not ready_batch:
            return

        pipe = self.redis.pipeline()
        for lock_key in ready_batch:
            pipe.exists(lock_key)
        lock_exists = pipe.execute()

        ready_units = [
            uk for uk, locked in zip(ready_batch, lock_exists)
            if not locked
        ]

        if ready_units:
            self.redis.sadd(READY_SET, *ready_units)
            logger.info("Full scan found %d ready units", len(ready_units))

    def migrate_legacy_queues(self):
        """One-time migration from per-user queues (scheduler:uq:{user_id})
        to per-unit queues (scheduler:uq:{task_name}:{user_id}).

        Idempotent and restartable: each message is moved atomically, so a
        crash mid-run only leaves the remaining messages in the legacy queue
        to be picked up on the next startup. Guarded by a single-flight lock
        so only one instance runs it. Call this at process startup BEFORE
        run_server (and never at import time, since the singleton is also
        imported by the API process).
        """
        # single-flight guard across instances
        if not self.redis.set(MIGRATION_LOCK, self.instance_id, nx=True, ex=600):
            logger.info("[Migrate] Another instance holds the migration lock, skip")
            return

        migrated_msgs = 0
        migrated_users = 0
        try:
            legacy_users = self.redis.smembers(LEGACY_ACTIVE_USERS) or set()
            if not legacy_users:
                # Nothing queued under the old scheme; drop any stray ready set.
                self.redis.delete(LEGACY_READY_SET)
                logger.info("[Migrate] No legacy per-user queues found")
                return

            for user_id in legacy_users:
                legacy_queue = f"{USER_QUEUE_PREFIX}{user_id}"
                while True:
                    peek = self.redis.lindex(legacy_queue, 0)
                    if peek is None:
                        break
                    try:
                        msg = json.loads(peek)
                        task_name = msg["task_name"]
                        msg_user = msg.get("user_id", user_id)
                    except (json.JSONDecodeError, TypeError, KeyError) as e:
                        logger.error(
                            "[Migrate] Bad legacy message for %s, dropping: %s",
                            user_id, e,
                        )
                        self.redis.lpop(legacy_queue)  # discard to make progress
                        continue

                    unit_key = f"{task_name}:{msg_user}"
                    new_queue = f"{USER_QUEUE_PREFIX}{unit_key}"

                    moved = self.redis.eval(
                        _LUA_MIGRATE_HEAD, 2, legacy_queue, new_queue,
                    )
                    if moved is None:  # queue drained concurrently
                        break

                    self.redis.sadd(ACTIVE_UNITS, unit_key)
                    if not self.redis.exists(unit_key):
                        self.redis.sadd(READY_SET, unit_key)
                    migrated_msgs += 1

                self.redis.delete(legacy_queue)
                self.redis.srem(LEGACY_ACTIVE_USERS, user_id)
                migrated_users += 1

            self.redis.delete(LEGACY_ACTIVE_USERS)
            self.redis.delete(LEGACY_READY_SET)
            logger.info(
                "[Migrate] Legacy migration done: users=%d messages=%d",
                migrated_users, migrated_msgs,
            )
        except Exception as e:
            logger.error("[Migrate] Legacy migration failed: %s", e, exc_info=True)
        finally:
            self.redis.eval(_LUA_SAFE_DELETE, 1, MIGRATION_LOCK, self.instance_id)

    def migrate_legacy_tracker_keys(self):
        """One-time migration of task-status tracker keys from the old
        un-namespaced form ``task_tracker:{msg_id}`` to ``scheduler:tracker:{msg_id}``.

        Preserves TTL (via RENAME) and never clobbers a newer value: if the
        namespaced key already exists (e.g. an in-flight task was dispatched
        after deploy and wrote the new key), the stale old key is dropped.
        Idempotent, restartable, and guarded by a single-flight lock.

        The ephemeral ``dispatch:{msg_id}`` lock is intentionally NOT migrated:
        it lives only during an active dispatch (300s TTL) and any stale entry
        expires harmlessly on its own.
        """
        if not self.redis.set(TRACKER_MIGRATION_LOCK, self.instance_id, nx=True, ex=600):
            logger.info("[Migrate] Another instance holds the tracker migration lock, skip")
            return

        renamed = 0
        dropped = 0
        prefix_len = len(LEGACY_TRACKER_PREFIX)
        try:
            cursor = 0
            while True:
                cursor, keys = self.redis.scan(
                    cursor=cursor, match=f"{LEGACY_TRACKER_PREFIX}*", count=500,
                )
                for old_key in keys:
                    msg_id = old_key[prefix_len:]
                    new_key = f"{TRACKER_PREFIX}{msg_id}"
                    try:
                        res = self.redis.eval(
                            _LUA_MIGRATE_TRACKER, 2, old_key, new_key,
                        )
                    except Exception as e:
                        logger.error(
                            "[Migrate] Tracker key migrate failed %s: %s", old_key, e,
                        )
                        continue
                    if res == 1:
                        renamed += 1
                    elif res == 0:
                        dropped += 1
                if cursor == 0:
                    break
            logger.info(
                "[Migrate] Tracker keys migrated: renamed=%d dropped_stale=%d",
                renamed, dropped,
            )
        except Exception as e:
            logger.error("[Migrate] Tracker migration failed: %s", e, exc_info=True)
        finally:
            self.redis.eval(_LUA_SAFE_DELETE, 1, TRACKER_MIGRATION_LOCK, self.instance_id)

    def run_server(self):
        health_check_server(self)
        self.running = True

        last_full_scan = 0.0
        full_scan_interval = 30.0

        logger.info(
            "Scheduler started: instance=%s", self.instance_id,
        )

        def _heartbeat_thread():
            while self.running:
                self._heartbeat()
                time.sleep(HEARTBEAT_INTERVAL)

        threading.Thread(target=_heartbeat_thread, daemon=True).start()

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
            "active_units": self.redis.scard(ACTIVE_UNITS),
            "ready_units": self.redis.scard(READY_SET),
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
