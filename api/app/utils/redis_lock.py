import redis
import uuid
import time
import threading

UNLOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

CLEANUP_DEAD_HEAD_SCRIPT = """
local queue_key = KEYS[1]
local lock_key = KEYS[2]

local first = redis.call("lindex", queue_key, 0)
if not first then
    return 0
end

if redis.call("exists", lock_key) == 1 then
    return 0
end

redis.call("lpop", queue_key)
return 1
"""

SAFE_RELEASE_QUEUE_SCRIPT = """
local queue_key = KEYS[1]
local value = ARGV[1]

local first = redis.call("lindex", queue_key, 0)
if first == value then
    redis.call("lpop", queue_key)
    return 1
end
return 0
"""


def _ensure_str(val):
    """统一将 Redis 返回值转为 str，兼容 decode_responses=True/False"""
    if val is None:
        return None
    if isinstance(val, bytes):
        return val.decode("utf-8")
    return str(val)


class RedisFairLock:
    def __init__(
            self,
            key: str,
            redis_client: redis.StrictRedis,
            expire: int = 30,
            retry_interval: float = 0.05,
            timeout: float = 600,
            auto_renewal: bool = True
    ):
        self.key = key
        self.queue_key = f"{key}:queue"
        self.value = str(uuid.uuid4())
        self.expire = expire
        self.retry_interval = retry_interval
        self.timeout = timeout
        self.redis = redis_client
        self._locked = False
        self.auto_renewal = auto_renewal
        self._renew_thread = None
        self._stop_renew = threading.Event()

    def acquire(self):
        start = time.time()

        self.redis.rpush(self.queue_key, self.value)

        while True:
            first = _ensure_str(self.redis.lindex(self.queue_key, 0))

            if first == self.value:
                ok = self.redis.set(self.key, self.value, nx=True, ex=self.expire)
                if ok:
                    self._locked = True

                    if self.auto_renewal:
                        self._start_renewal()
                    return True

            if first:
                self.redis.eval(CLEANUP_DEAD_HEAD_SCRIPT, 2, self.queue_key, self.key)

            if time.time() - start > self.timeout:
                self.redis.lrem(self.queue_key, 0, self.value)
                return False

            time.sleep(self.retry_interval)

    def _renewal_loop(self):
        while not self._stop_renew.is_set():
            time.sleep(self.expire / 3)
            if self._stop_renew.is_set():
                break

            self.redis.eval(
                RENEW_SCRIPT,
                1,
                self.key,
                self.value,
                str(self.expire)
            )

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

        self.redis.eval(UNLOCK_SCRIPT, 1, self.key, self.value)

        self.redis.eval(SAFE_RELEASE_QUEUE_SCRIPT, 1, self.queue_key, self.value)

        self._locked = False

    def __enter__(self):
        ok = self.acquire()
        if not ok:
            raise RuntimeError(f"Get redis lock timeout: {self.key}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

