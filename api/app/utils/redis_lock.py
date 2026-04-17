import logging
import threading
import time
import uuid

import redis
from redis.exceptions import (
    ConnectionError,
    TimeoutError,
    RedisError,
)

UNLOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end

return 0
"""

RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

ACQUIRE_SCRIPT = """
local queue_key = KEYS[1]
local lock_key = KEYS[2]

local client_id = ARGV[1]
local expire = tonumber(ARGV[2])
local time_out = tonumber(ARGV[3])

local now = tonumber(redis.call("time")[1])

if redis.call("zscore", queue_key, client_id) == false then
    redis.call("zadd", queue_key, now, client_id)
end

local expired = redis.call("zrangebyscore", queue_key, 0, now - time_out)

for _, v in ipairs(expired) do
    redis.call("zrem", queue_key, v)
end

local first = redis.call("zrange", queue_key, 0, 0)[1]
if first == client_id then

    if redis.call("set", lock_key, client_id, "NX", "EX", expire) then
        redis.call("zrem", queue_key, client_id)
        return 1
    end

    if redis.call("get", lock_key) == client_id then
        redis.call("expire", lock_key, expire)
        return 1
    end
end
return 0
"""


def _ensure_str(val):
    if val is None:
        return None
    if isinstance(val, bytes):
        return val.decode("utf-8")
    return str(val)


class RedisFairLock:
    # ZOMBIE CLEAN BUFFER
    CLEANUP_BUFFER = 30
    # Redis 操作失败时的最大重试次数
    MAX_RETRIES = 3
    # 重试间隔基数（秒），实际间隔 = base * 2^attempt（指数退避）
    RETRY_BACKOFF_BASE = 0.1

    _logger = logging.getLogger(__name__)

    def __init__(
            self,
            key: str,
            redis_client: redis.StrictRedis,
            expire: int = 30,
            retry_interval: float = 1,
            timeout: float = 600,
            auto_renewal: bool = True
    ):
        self.key = key
        self.queue_key = f"{key}:zset"
        self.value = f"{uuid.uuid4().hex}:{int(time.time())}"
        self.expire = expire
        self.retry_interval = retry_interval
        self.timeout = timeout
        self.redis = redis_client
        self._locked = False
        self.auto_renewal = auto_renewal
        self._renew_thread = None
        self._stop_renew = threading.Event()

    def _exec_with_retry(self, func, *args, raise_on_fail=True, **kwargs):
        """
        带指数退避重试的 Redis 操作执行器。

        对 ConnectionError / TimeoutError 自动重试，其他异常直接抛出。
        """
        last_err = None
        for attempt in range(self.MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except (ConnectionError, TimeoutError) as e:
                last_err = e
                wait = self.RETRY_BACKOFF_BASE * (2 ** attempt)
                self._logger.warning(
                    f"[RedisFairLock] Redis error on attempt {attempt + 1}/{self.MAX_RETRIES} "
                    f"for key={self.key}: {e}, retrying in {wait:.2f}s"
                )
                time.sleep(wait)
            except RedisError:
                raise
        if raise_on_fail:
            raise last_err
        return None

    def acquire(self):
        start = time.time()

        while True:
            ok = self._exec_with_retry(
                self.redis.eval,
                ACQUIRE_SCRIPT,
                2,
                self.queue_key,
                self.key,
                self.value,
                str(self.expire),
                str(self.timeout + self.CLEANUP_BUFFER),
            )

            if ok == 1:
                self._locked = True
                if self.auto_renewal:
                    self._start_renewal()
                return True

            if time.time() - start > self.timeout:
                self._exec_with_retry(
                    self.redis.zrem, self.queue_key, self.value,
                    raise_on_fail=False,
                )
                return False

            time.sleep(self.retry_interval)

    def _renewal_loop(self):
        while not self._stop_renew.is_set():
            time.sleep(self.expire / 3)
            if self._stop_renew.is_set():
                break

            success = self._exec_with_retry(
                self.redis.eval,
                RENEW_SCRIPT,
                1,
                self.key,
                self.value,
                str(self.expire),
                raise_on_fail=False,
            )
            if not success:
                break

    def _start_renewal(self):
        self._stop_renew = threading.Event()
        self._renew_thread = threading.Thread(target=self._renewal_loop, daemon=True)
        self._renew_thread.start()

    def _stop_renewal(self):
        self._stop_renew.set()
        if self._renew_thread:
            self._renew_thread.join(timeout=1)

    def release(self):
        if not self._locked:
            return

        if self.auto_renewal:
            self._stop_renewal()

        self._exec_with_retry(
            self.redis.eval, UNLOCK_SCRIPT, 1, self.key, self.value,
            raise_on_fail=False,
        )

        self._locked = False

    def __enter__(self):
        ok = self.acquire()
        if not ok:
            raise RuntimeError(f"Get redis lock timeout: {self.key}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
