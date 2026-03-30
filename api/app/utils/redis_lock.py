import redis
import uuid
import time

UNLOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class RedisLock:
    def __init__(
            self,
            key: str,
            redis_client: redis.StrictRedis,
            expire: int = 60,
            retry_interval: float = 0.1,
            timeout: float = 30

    ):
        self.key = key
        self.expire = expire
        self.value = str(uuid.uuid4())
        self._locked = False
        self.retry_interval = retry_interval
        self.timeout = timeout
        self.redis_client = redis_client

    def acquire(self) -> bool:
        start = time.time()
        while True:
            ok = self.redis_client.set(self.key, self.value, ex=self.expire, nx=True)
            if ok:
                self._locked = True
                return True
            if time.time() - start >= self.timeout:
                return False
            time.sleep(self.retry_interval)

    def release(self):
        if not self._locked:
            return
        self.redis_client.eval(
            UNLOCK_SCRIPT,
            1,
            self.key,
            self.value
        )
        self._locked = False

    def __enter__(self):
        ok = self.acquire()
        if not ok:
            raise RuntimeError(f"Get redis lock timeout: {self.key}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
