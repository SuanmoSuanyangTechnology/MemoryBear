"""
Performance monitoring and metrics for i18n system.

This module provides:
- Translation request counters
- Translation timing metrics
- Missing translation tracking
- Performance monitoring decorators
- Prometheus-compatible metrics
"""

import logging
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)


class TranslationMetrics:
    """
    Metrics collector for translation operations.
    
    Tracks:
    - Translation request counts
    - Translation timing (latency)
    - Missing translations
    - Cache performance
    - Locale usage
    """
    
    def __init__(self):
        """Initialize metrics collector."""
        # Request counters by locale
        self._request_counts: Dict[str, int] = defaultdict(int)
        
        # Missing translation tracker
        self._missing_translations: Dict[str, set] = defaultdict(set)
        
        # Timing metrics (in milliseconds)
        self._timing_data: list = []
        self._max_timing_samples = 10000  # Keep last 10k samples
        
        # Locale usage
        self._locale_usage: Dict[str, int] = defaultdict(int)
        
        # Namespace usage
        self._namespace_usage: Dict[str, int] = defaultdict(int)
        
        # Error counts
        self._error_counts: Dict[str, int] = defaultdict(int)
        
        # Start time
        self._start_time = datetime.now()
        
        logger.info("TranslationMetrics initialized")
    
    def record_request(self, locale: str, namespace: str = None):
        """
        Record a translation request.
        
        Args:
            locale: Locale code
            namespace: Translation namespace (optional)
        """
        self._request_counts[locale] += 1
        self._locale_usage[locale] += 1
        
        if namespace:
            self._namespace_usage[namespace] += 1
    
    def record_missing(self, key: str, locale: str):
        """
        Record a missing translation.
        
        Args:
            key: Translation key
            locale: Locale code
        """
        self._missing_translations[locale].add(key)
        logger.debug(f"Missing translation recorded: {key} (locale: {locale})")
    
    def record_timing(self, duration_ms: float, locale: str, operation: str = "translate"):
        """
        Record translation operation timing.
        
        Args:
            duration_ms: Duration in milliseconds
            locale: Locale code
            operation: Operation type
        """
        # Keep only recent samples to avoid memory bloat
        if len(self._timing_data) >= self._max_timing_samples:
            self._timing_data.pop(0)
        
        self._timing_data.append({
            "duration_ms": duration_ms,
            "locale": locale,
            "operation": operation,
            "timestamp": time.time()
        })
    
    def record_error(self, error_type: str):
        """
        Record an error.
        
        Args:
            error_type: Type of error
        """
        self._error_counts[error_type] += 1
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get metrics summary.
        
        Returns:
            Dictionary with metrics summary
        """
        total_requests = sum(self._request_counts.values())
        total_missing = sum(len(keys) for keys in self._missing_translations.values())
        
        # Calculate timing statistics
        timing_stats = self._calculate_timing_stats()
        
        # Calculate uptime
        uptime_seconds = (datetime.now() - self._start_time).total_seconds()
        
        return {
            "uptime_seconds": round(uptime_seconds, 2),
            "total_requests": total_requests,
            "requests_per_locale": dict(self._request_counts),
            "total_missing_translations": total_missing,
            "missing_by_locale": {
                locale: len(keys)
                for locale, keys in self._missing_translations.items()
            },
            "timing": timing_stats,
            "locale_usage": dict(self._locale_usage),
            "namespace_usage": dict(self._namespace_usage),
            "error_counts": dict(self._error_counts)
        }
    
    def _calculate_timing_stats(self) -> Dict[str, Any]:
        """
        Calculate timing statistics.
        
        Returns:
            Dictionary with timing statistics
        """
        if not self._timing_data:
            return {
                "count": 0,
                "avg_ms": 0,
                "min_ms": 0,
                "max_ms": 0,
                "p50_ms": 0,
                "p95_ms": 0,
                "p99_ms": 0
            }
        
        durations = [d["duration_ms"] for d in self._timing_data]
        durations.sort()
        
        count = len(durations)
        avg = sum(durations) / count
        
        # Calculate percentiles
        p50_idx = int(count * 0.50)
        p95_idx = int(count * 0.95)
        p99_idx = int(count * 0.99)
        
        return {
            "count": count,
            "avg_ms": round(avg, 3),
            "min_ms": round(durations[0], 3),
            "max_ms": round(durations[-1], 3),
            "p50_ms": round(durations[p50_idx], 3),
            "p95_ms": round(durations[p95_idx], 3),
            "p99_ms": round(durations[p99_idx], 3)
        }
    
    def get_missing_translations(self, locale: Optional[str] = None) -> Dict[str, list]:
        """
        Get missing translations.
        
        Args:
            locale: Specific locale (optional, returns all if None)
            
        Returns:
            Dictionary of missing translations by locale
        """
        if locale:
            return {locale: list(self._missing_translations.get(locale, set()))}
        
        return {
            locale: list(keys)
            for locale, keys in self._missing_translations.items()
        }
    
    def reset(self):
        """Reset all metrics."""
        self._request_counts.clear()
        self._missing_translations.clear()
        self._timing_data.clear()
        self._locale_usage.clear()
        self._namespace_usage.clear()
        self._error_counts.clear()
        self._start_time = datetime.now()
        logger.info("Metrics reset")
    
    def export_prometheus(self) -> str:
        """
        Export metrics in Prometheus format.
        
        Returns:
            Prometheus-formatted metrics string
        """
        lines = []
        
        # Translation requests counter
        lines.append("# HELP i18n_translation_requests_total Total number of translation requests")
        lines.append("# TYPE i18n_translation_requests_total counter")
        for locale, count in self._request_counts.items():
            lines.append(f'i18n_translation_requests_total{{locale="{locale}"}} {count}')
        
        # Missing translations counter
        lines.append("# HELP i18n_missing_translations_total Total number of missing translations")
        lines.append("# TYPE i18n_missing_translations_total counter")
        for locale, keys in self._missing_translations.items():
            lines.append(f'i18n_missing_translations_total{{locale="{locale}"}} {len(keys)}')
        
        # Timing metrics
        timing_stats = self._calculate_timing_stats()
        lines.append("# HELP i18n_translation_duration_ms Translation operation duration in milliseconds")
        lines.append("# TYPE i18n_translation_duration_ms summary")
        lines.append(f'i18n_translation_duration_ms{{quantile="0.5"}} {timing_stats["p50_ms"]}')
        lines.append(f'i18n_translation_duration_ms{{quantile="0.95"}} {timing_stats["p95_ms"]}')
        lines.append(f'i18n_translation_duration_ms{{quantile="0.99"}} {timing_stats["p99_ms"]}')
        lines.append(f'i18n_translation_duration_ms_sum {sum(d["duration_ms"] for d in self._timing_data)}')
        lines.append(f'i18n_translation_duration_ms_count {timing_stats["count"]}')
        
        # Error counter
        lines.append("# HELP i18n_errors_total Total number of i18n errors")
        lines.append("# TYPE i18n_errors_total counter")
        for error_type, count in self._error_counts.items():
            lines.append(f'i18n_errors_total{{type="{error_type}"}} {count}')
        
        return "\n".join(lines)


# Global metrics instance
_metrics: Optional[TranslationMetrics] = None


def get_metrics() -> TranslationMetrics:
    """
    Get the global metrics instance.
    
    Returns:
        TranslationMetrics singleton
    """
    global _metrics
    if _metrics is None:
        _metrics = TranslationMetrics()
    return _metrics


def monitor_performance(operation: str = "translate"):
    """
    Decorator to monitor translation operation performance.
    
    Args:
        operation: Operation name for metrics
        
    Returns:
        Decorated function
        
    Example:
        @monitor_performance("translate")
        def translate(key: str, locale: str) -> str:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            
            try:
                result = func(*args, **kwargs)
                
                # Record timing
                duration_ms = (time.perf_counter() - start_time) * 1000
                
                # Try to extract locale from args/kwargs
                locale = kwargs.get("locale", "unknown")
                if not locale and len(args) > 1:
                    locale = args[1] if isinstance(args[1], str) else "unknown"
                
                metrics = get_metrics()
                metrics.record_timing(duration_ms, locale, operation)
                
                return result
            
            except Exception as e:
                # Record error
                metrics = get_metrics()
                metrics.record_error(type(e).__name__)
                raise
        
        return wrapper
    return decorator


def track_missing_translation(key: str, locale: str):
    """
    Track a missing translation.
    
    Args:
        key: Translation key
        locale: Locale code
    """
    metrics = get_metrics()
    metrics.record_missing(key, locale)


def track_translation_request(locale: str, namespace: str = None):
    """
    Track a translation request.
    
    Args:
        locale: Locale code
        namespace: Translation namespace (optional)
    """
    metrics = get_metrics()
    metrics.record_request(locale, namespace)
