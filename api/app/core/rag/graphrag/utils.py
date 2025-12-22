import xxhash
import redis
from app.core.config import settings

redis_client = redis.StrictRedis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD,
    decode_responses=True,
    max_connections=30
)


def get_llm_cache(llmnm, txt, history, genconf):
    hasher = xxhash.xxh64()
    hasher.update((str(llmnm) + str(txt) + str(history) + str(genconf)).encode("utf-8"))

    k = hasher.hexdigest()
    bin = redis_client.get(k)
    if not bin:
        return None
    return bin


def set_llm_cache(llmnm, txt, v, history, genconf):
    hasher = xxhash.xxh64()
    hasher.update((str(llmnm) + str(txt) + str(history) + str(genconf)).encode("utf-8"))
    k = hasher.hexdigest()
    redis_client.set(k, v.encode("utf-8"), 24 * 3600)
