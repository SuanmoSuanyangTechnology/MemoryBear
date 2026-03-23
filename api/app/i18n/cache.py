"""
Advanced caching system for i18n translations.

This module provides:
- LRU cache for hot translations
- Lazy loading mechanism
- Memory optimization
- Cache statistics
"""

import logging
from functools import lru_cache
from typing import Any, Dict, Optional
from collections import OrderedDict
import time

logger = logging.getLogger(__name__)


class TranslationCache:
    """
    Advanced translation cache with LRU eviction and lazy loading.
    
    Features:
    - LRU cache for frequently accessed translations
    - Lazy loading to reduce startup time
    - Memory-efficient storage
    - Cache hit/miss statistics
    """
    
    def __init__(self, max_lru_size: int = 1000, enable_lazy_load: bool = True):
        """
        Initialize the translation cache.
        
        Args:
            max_lru_size: Maximum size of LRU cache for hot translations
            enable_lazy_load: Enable lazy loading of locales
        """
        self.max_lru_size = max_lru_size
        self.enable_lazy_load = enable_lazy_load
        
        # Main cache: {locale: {namespace: {key: value}}}
        self._main_cache: Dict[str, Dict[str, Any]] = {}
        
        # LRU cache for hot translations
        self._lru_cache: OrderedDict = OrderedDict()
        
        # Loaded locales tracker
        self._loaded_locales: set = set()
        
        # Statistics
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
    
    def set_locale_data(self, locale: str, data: Dict[str, Any]):
        """
        Set translation data for a locale.
        
        Args:
            locale: Locale code
            data: Translation data dictionary
        """
        self._main_cache[locale] = data
        self._loaded_locales.add(locale)
        logger.debug(f"Loaded locale '{locale}' into cache")
    
    def get_translation(
        self,
        locale: str,
        namespace: str,
        key_path: list
    ) -> Optional[str]:
        """
        Get translation from cache with LRU optimization.
        
        Args:
            locale: Locale code
            namespace: Translation namespace
            key_path: List of nested keys
            
        Returns:
            Translation string or None if not found
        """
        # Build cache key for LRU
        cache_key = f"{locale}:{namespace}:{'.'.join(key_path)}"
        
        # Check LRU cache first (hot translations)
        if cache_key in self._lru_cache:
            self._stats["lru_hits"] += 1
            self._stats["hits"] += 1
            # Move to end (most recently used)
            self._lru_cache.move_to_end(cache_key)
            return self._lru_cache[cache_key]
        
        self._stats["lru_misses"] += 1
        
        # Check main cache
        if locale not in self._main_cache:
            self._stats["misses"] += 1
            return None
        
        if namespace not in self._main_cache[locale]:
            self._stats["misses"] += 1
            return None
        
        # Navigate through nested keys
        current = self._main_cache[locale][namespace]
        for key in key_path:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                self._stats["misses"] += 1
                return None
        
        # Return only if it's a string value
        if not isinstance(current, str):
            self._stats["misses"] += 1
            return None
        
        self._stats["hits"] += 1
        
        # Add to LRU cache
        self._add_to_lru(cache_key, current)
        
        return current
    
    def _add_to_lru(self, key: str, value: str):
        """
        Add translation to LRU cache.
        
        Args:
            key: Cache key
            value: Translation value
        """
        # Remove oldest if cache is full
        if len(self._lru_cache) >= self.max_lru_size:
            self._lru_cache.popitem(last=False)
        
        self._lru_cache[key] = value
    
    def is_locale_loaded(self, locale: str) -> bool:
        """
        Check if a locale is loaded.
        
        Args:
            locale: Locale code
            
        Returns:
            True if locale is loaded
        """
        return locale in self._loaded_locales
    
    def get_loaded_locales(self) -> list:
        """
        Get list of loaded locales.
        
        Returns:
            List of locale codes
        """
        return list(self._loaded_locales)
    
    def clear_lru(self):
        """Clear the LRU cache."""
        self._lru_cache.clear()
        logger.info("LRU cache cleared")
    
    def clear_locale(self, locale: str):
        """
        Clear cache for a specific locale.
        
        Args:
            locale: Locale code
        """
        if locale in self._main_cache:
            del self._main_cache[locale]
            self._loaded_locales.discard(locale)
        
        # Clear related LRU entries
        keys_to_remove = [k for k in self._lru_cache if k.startswith(f"{locale}:")]
        for key in keys_to_remove:
            del self._lru_cache[key]
        
        logger.info(f"Cleared cache for locale '{locale}'")
    
    def clear_all(self):
        """Clear all caches."""
        self._main_cache.clear()
        self._lru_cache.clear()
        self._loaded_locales.clear()
        logger.info("All caches cleared")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
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
        """Reset cache statistics."""
        self._stats = {
            "hits": 0,
            "misses": 0,
            "lru_hits": 0,
            "lru_misses": 0,
            "lazy_loads": 0
        }
        logger.info("Cache statistics reset")
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """
        Estimate memory usage of the cache.
        
        Returns:
            Dictionary with memory usage information
        """
        import sys
        
        main_cache_size = sys.getsizeof(self._main_cache)
        lru_cache_size = sys.getsizeof(self._lru_cache)
        
        # Rough estimate of nested data
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


@lru_cache(maxsize=128)
def get_cached_translation_key(locale: str, namespace: str, key: str) -> str:
    """
    LRU cached function for building translation cache keys.
    
    This reduces string concatenation overhead for frequently accessed keys.
    
    Args:
        locale: Locale code
        namespace: Translation namespace
        key: Translation key
        
    Returns:
        Cache key string
    """
    return f"{locale}:{namespace}:{key}"
