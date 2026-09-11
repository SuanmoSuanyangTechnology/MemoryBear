"""翻译缓存（移植自 api/app/i18n/cache.py，无外部依赖，仅改 logger 来源）。

两级结构：
- 主缓存 ``{locale: {namespace: {key: value}}}``：启动时全量加载，命中即返回
- LRU 热点缓存：把高频 key 的「查表结果」缓成扁平字符串，省掉嵌套下钻

统计（hits/misses/lru_hits/lru_misses）供 /metrics 类端点读取；本服务当前无该端点，
但 ``get_stats()`` 仍可在排查「翻译没生效」时直接调用。
"""

from collections import OrderedDict
from typing import Any

from src.infrastructure.logger.config import get_logger

logger = get_logger(__name__)


class TranslationCache:
    """带 LRU 淘汰与懒加载开关的翻译缓存。"""

    def __init__(self, max_lru_size: int = 1000, enable_lazy_load: bool = True):
        """
        Args:
            max_lru_size: 热点翻译 LRU 容量上限。
            enable_lazy_load: 是否懒加载 locale（当前服务启动即全量加载，置 False）。
        """
        self.max_lru_size = max_lru_size
        self.enable_lazy_load = enable_lazy_load

        # 主缓存：{locale: {namespace: {key: value}}}
        self._main_cache: dict[str, dict[str, Any]] = {}

        # 热点翻译 LRU
        self._lru_cache: OrderedDict = OrderedDict()

        self._loaded_locales: set = set()

        self._stats = {
            "hits": 0,
            "misses": 0,
            "lru_hits": 0,
            "lru_misses": 0,
            "lazy_loads": 0
        }

        logger.info(
            f"TranslationCache initialized with LRU size: {max_lru_size}, "
            f"lazy loading: {enable_lazy_load}"
        )

    def set_locale_data(self, locale: str, data: dict[str, Any]):
        """写入某 locale 的全量翻译数据。"""
        self._main_cache[locale] = data
        self._loaded_locales.add(locale)
        logger.debug(f"Loaded locale '{locale}' into cache")

    def get_translation(
        self,
        locale: str,
        namespace: str,
        key_path: list
    ) -> str | None:
        """取翻译值；未找到或叶子不是字符串时返回 None（由 service 决定 fallback）。

        Args:
            locale: 语言码。
            namespace: 命名空间（= locales 下的文件名，如 "errors"）。
            key_path: 嵌套键路径，如 ["common", "not_found"]。
        """
        cache_key = f"{locale}:{namespace}:{'.'.join(key_path)}"

        # 1) 热点 LRU
        if cache_key in self._lru_cache:
            self._stats["lru_hits"] += 1
            self._stats["hits"] += 1
            self._lru_cache.move_to_end(cache_key)
            return self._lru_cache[cache_key]

        self._stats["lru_misses"] += 1

        # 2) 主缓存
        if locale not in self._main_cache:
            self._stats["misses"] += 1
            return None

        if namespace not in self._main_cache[locale]:
            self._stats["misses"] += 1
            return None

        current = self._main_cache[locale][namespace]
        for key in key_path:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                self._stats["misses"] += 1
                return None

        # 命中叶子但值不是字符串（如拿到的是一个子字典）→ 视为未命中
        if not isinstance(current, str):
            self._stats["misses"] += 1
            return None

        self._stats["hits"] += 1
        self._add_to_lru(cache_key, current)

        return current

    def _add_to_lru(self, key: str, value: str):
        """写入 LRU；满则淘汰最旧。"""
        if len(self._lru_cache) >= self.max_lru_size:
            self._lru_cache.popitem(last=False)

        self._lru_cache[key] = value

    def is_locale_loaded(self, locale: str) -> bool:
        return locale in self._loaded_locales

    def get_loaded_locales(self) -> list:
        return list(self._loaded_locales)

    def clear_lru(self):
        self._lru_cache.clear()
        logger.info("LRU cache cleared")

    def clear_locale(self, locale: str):
        """清除某 locale 的主缓存与相关 LRU 条目。"""
        if locale in self._main_cache:
            del self._main_cache[locale]
            self._loaded_locales.discard(locale)

        keys_to_remove = [k for k in self._lru_cache if k.startswith(f"{locale}:")]
        for key in keys_to_remove:
            del self._lru_cache[key]

        logger.info(f"Cleared cache for locale '{locale}'")

    def clear_all(self):
        self._main_cache.clear()
        self._lru_cache.clear()
        self._loaded_locales.clear()
        logger.info("All caches cleared")

    def get_stats(self) -> dict[str, Any]:
        """命中率统计（排查「翻译没生效」时先看 miss 与 miss 的 key）。"""
        total_requests = self._stats["hits"] + self._stats["misses"]
        hit_rate = (
            self._stats["hits"] / total_requests * 100
            if total_requests > 0
            else 0
        )

        lru_total = self._stats["lru_hits"] + self._stats["lru_misses"]
        lru_hit_rate = (
            self._stats["lru_hits"] / lru_total * 100
            if lru_total > 0
            else 0
        )

        return {
            "total_requests": total_requests,
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "hit_rate": round(hit_rate, 2),
            "lru_hits": self._stats["lru_hits"],
            "lru_misses": self._stats["lru_misses"],
            "lru_hit_rate": round(lru_hit_rate, 2),
            "lru_size": len(self._lru_cache),
            "lru_max_size": self.max_lru_size,
            "loaded_locales": len(self._loaded_locales),
            "lazy_loads": self._stats["lazy_loads"]
        }

    def reset_stats(self):
        self._stats = {
            "hits": 0,
            "misses": 0,
            "lru_hits": 0,
            "lru_misses": 0,
            "lazy_loads": 0
        }
        logger.info("Cache statistics reset")

    def get_memory_usage(self) -> dict[str, Any]:
        """粗略估算缓存占用（sys.getsizeof 不含 dict 内部对象的真实开销）。"""
        import sys

        main_cache_size = sys.getsizeof(self._main_cache)
        lru_cache_size = sys.getsizeof(self._lru_cache)

        for locale_data in self._main_cache.values():
            main_cache_size += sys.getsizeof(locale_data)
            for namespace_data in locale_data.values():
                main_cache_size += sys.getsizeof(namespace_data)

        return {
            "main_cache_bytes": main_cache_size,
            "lru_cache_bytes": lru_cache_size,
            "total_bytes": main_cache_size + lru_cache_size,
            "main_cache_mb": round(main_cache_size / 1024 / 1024, 2),
            "lru_cache_mb": round(lru_cache_size / 1024 / 1024, 2),
            "total_mb": round((main_cache_size + lru_cache_size) / 1024 / 1024, 2)
        }