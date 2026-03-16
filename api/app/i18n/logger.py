"""
Translation logging for i18n system.

This module provides:
- TranslationLogger for recording missing translations
- Missing translation report generation
- Integration with existing logging system
- Structured logging for translation events
"""

import logging
from typing import Dict, List, Optional, Set
from datetime import datetime
from collections import defaultdict
from pathlib import Path
import json

from app.core.logging_config import get_logger

logger = get_logger(__name__)


class TranslationLogger:
    """
    Logger for translation events and missing translations.
    
    Features:
    - Records missing translations with context
    - Generates missing translation reports
    - Integrates with existing logging system
    - Provides structured logging for analysis
    """
    
    def __init__(self, log_file: Optional[str] = None):
        """
        Initialize translation logger.
        
        Args:
            log_file: Optional custom log file path for missing translations
        """
        self.log_file = log_file or "logs/i18n/missing_translations.log"
        self._missing_translations: Dict[str, Set[str]] = defaultdict(set)
        self._missing_with_context: List[Dict] = []
        self._max_context_entries = 10000  # Keep last 10k entries
        
        # Ensure log directory exists
        log_path = Path(self.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create dedicated file handler for missing translations
        self._file_handler = logging.FileHandler(
            self.log_file,
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.WARNING)
        
        # Create formatter
        formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        
        # Create dedicated logger for missing translations
        self._logger = logging.getLogger("i18n.missing_translations")
        self._logger.setLevel(logging.WARNING)
        self._logger.addHandler(self._file_handler)
        self._logger.propagate = False  # Don't propagate to root logger
        
        logger.info(f"TranslationLogger initialized with log file: {self.log_file}")
    
    def log_missing_translation(
        self,
        key: str,
        locale: str,
        context: Optional[Dict] = None
    ):
        """
        Log a missing translation.
        
        Args:
            key: Translation key that was not found
            locale: Locale code
            context: Optional context information (e.g., request path, user info)
        """
        # Add to missing set
        self._missing_translations[locale].add(key)
        
        # Create context entry
        entry = {
            "timestamp": datetime.now().isoformat(),
            "key": key,
            "locale": locale,
            "context": context or {}
        }
        
        # Keep only recent entries to avoid memory bloat
        if len(self._missing_with_context) >= self._max_context_entries:
            self._missing_with_context.pop(0)
        
        self._missing_with_context.append(entry)
        
        # Log to file
        context_str = f" (context: {context})" if context else ""
        self._logger.warning(
            f"Missing translation: key='{key}', locale='{locale}'{context_str}"
        )
    
    def log_translation_error(
        self,
        error_type: str,
        message: str,
        key: Optional[str] = None,
        locale: Optional[str] = None,
        context: Optional[Dict] = None
    ):
        """
        Log a translation error.
        
        Args:
            error_type: Type of error (e.g., "format_error", "parameter_missing")
            message: Error message
            key: Translation key (optional)
            locale: Locale code (optional)
            context: Optional context information
        """
        error_data = {
            "error_type": error_type,
            "message": message,
            "key": key,
            "locale": locale,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        }
        
        self._logger.error(
            f"Translation error: {error_type} - {message} "
            f"(key: {key}, locale: {locale})"
        )
    
    def log_translation_success(
        self,
        key: str,
        locale: str,
        duration_ms: Optional[float] = None
    ):
        """
        Log a successful translation (debug level).
        
        Args:
            key: Translation key
            locale: Locale code
            duration_ms: Optional duration in milliseconds
        """
        duration_str = f" ({duration_ms:.3f}ms)" if duration_ms else ""
        logger.debug(
            f"Translation success: key='{key}', locale='{locale}'{duration_str}"
        )
    
    def get_missing_translations(
        self,
        locale: Optional[str] = None
    ) -> Dict[str, List[str]]:
        """
        Get missing translations.
        
        Args:
            locale: Specific locale (optional, returns all if None)
            
        Returns:
            Dictionary of missing translations by locale
        """
        if locale:
            return {locale: sorted(list(self._missing_translations.get(locale, set())))}
        
        return {
            loc: sorted(list(keys))
            for loc, keys in self._missing_translations.items()
        }
    
    def get_missing_with_context(
        self,
        locale: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        """
        Get missing translations with context.
        
        Args:
            locale: Filter by locale (optional)
            limit: Maximum number of entries to return (optional)
            
        Returns:
            List of missing translation entries with context
        """
        entries = self._missing_with_context
        
        # Filter by locale if specified
        if locale:
            entries = [e for e in entries if e["locale"] == locale]
        
        # Apply limit if specified
        if limit:
            entries = entries[-limit:]
        
        return entries
    
    def generate_report(
        self,
        locale: Optional[str] = None,
        output_file: Optional[str] = None
    ) -> Dict:
        """
        Generate a missing translation report.
        
        Args:
            locale: Specific locale (optional, generates for all if None)
            output_file: Optional file path to save report as JSON
            
        Returns:
            Report dictionary
        """
        missing = self.get_missing_translations(locale)
        
        report = {
            "generated_at": datetime.now().isoformat(),
            "total_missing": sum(len(keys) for keys in missing.values()),
            "missing_by_locale": {
                loc: {
                    "count": len(keys),
                    "keys": keys
                }
                for loc, keys in missing.items()
            },
            "recent_context": self.get_missing_with_context(locale, limit=100)
        }
        
        # Save to file if specified
        if output_file:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Missing translation report saved to: {output_file}")
        
        return report
    
    def get_statistics(self) -> Dict:
        """
        Get statistics about missing translations.
        
        Returns:
            Dictionary with statistics
        """
        total_missing = sum(len(keys) for keys in self._missing_translations.values())
        
        # Count by namespace
        namespace_counts = defaultdict(int)
        for locale, keys in self._missing_translations.items():
            for key in keys:
                namespace = key.split('.')[0] if '.' in key else 'unknown'
                namespace_counts[namespace] += 1
        
        return {
            "total_missing": total_missing,
            "locales_affected": len(self._missing_translations),
            "missing_by_locale": {
                loc: len(keys)
                for loc, keys in self._missing_translations.items()
            },
            "missing_by_namespace": dict(namespace_counts),
            "total_context_entries": len(self._missing_with_context)
        }
    
    def clear(self, locale: Optional[str] = None):
        """
        Clear missing translation records.
        
        Args:
            locale: Specific locale to clear (optional, clears all if None)
        """
        if locale:
            self._missing_translations.pop(locale, None)
            self._missing_with_context = [
                e for e in self._missing_with_context
                if e["locale"] != locale
            ]
            logger.info(f"Cleared missing translations for locale: {locale}")
        else:
            self._missing_translations.clear()
            self._missing_with_context.clear()
            logger.info("Cleared all missing translations")
    
    def export_to_json(self, output_file: str):
        """
        Export all missing translations to JSON file.
        
        Args:
            output_file: Output file path
        """
        data = {
            "exported_at": datetime.now().isoformat(),
            "missing_translations": self.get_missing_translations(),
            "statistics": self.get_statistics(),
            "recent_context": self.get_missing_with_context(limit=1000)
        }
        
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Missing translations exported to: {output_file}")
    
    def __del__(self):
        """Cleanup file handler on deletion."""
        try:
            if hasattr(self, '_file_handler'):
                self._file_handler.close()
                self._logger.removeHandler(self._file_handler)
        except Exception:
            pass


# Global translation logger instance
_translation_logger: Optional[TranslationLogger] = None


def get_translation_logger() -> TranslationLogger:
    """
    Get the global translation logger instance.
    
    Returns:
        TranslationLogger singleton
    """
    global _translation_logger
    if _translation_logger is None:
        _translation_logger = TranslationLogger()
    return _translation_logger


def log_missing_translation(
    key: str,
    locale: str,
    context: Optional[Dict] = None
):
    """
    Log a missing translation (convenience function).
    
    Args:
        key: Translation key
        locale: Locale code
        context: Optional context information
    """
    translation_logger = get_translation_logger()
    translation_logger.log_missing_translation(key, locale, context)


def log_translation_error(
    error_type: str,
    message: str,
    key: Optional[str] = None,
    locale: Optional[str] = None,
    context: Optional[Dict] = None
):
    """
    Log a translation error (convenience function).
    
    Args:
        error_type: Type of error
        message: Error message
        key: Translation key (optional)
        locale: Locale code (optional)
        context: Optional context information
    """
    translation_logger = get_translation_logger()
    translation_logger.log_translation_error(
        error_type, message, key, locale, context
    )
