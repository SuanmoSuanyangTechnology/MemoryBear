import xxhash
from app.aioRedis import aio_redis_set, aio_redis_get

def get_llm_cache(llmnm, txt, history, genconf):
    hasher = xxhash.xxh64()
    hasher.update((str(llmnm)+str(txt)+str(history)+str(genconf)).encode("utf-8"))

    k = hasher.hexdigest()
    bin = aio_redis_get(k)
    if not bin:
        return None
    return bin


def set_llm_cache(llmnm, txt, v, history, genconf):
    hasher = xxhash.xxh64()
    hasher.update((str(llmnm)+str(txt)+str(history)+str(genconf)).encode("utf-8"))
    k = hasher.hexdigest()
    aio_redis_set(k, v.encode("utf-8"), 24 * 3600)
