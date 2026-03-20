"""
Translation file loader for i18n system.

This module handles loading translation files from multiple directories
(community edition + enterprise edition) and provides hot reload support.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TranslationLoader:
    """
    Translation file loader that supports:
    - Loading from multiple directories (community + enterprise)
    - Hot reload of translation files
    - Automatic locale detection
    """

    def __init__(self, locales_dirs: Optional[List[str]] = None):
        """
        Initialize the translation loader.

        Args:
            locales_dirs: List of directories containing translation files.
                         If None, will auto-detect from settings.
        """
        if locales_dirs is None:
            locales_dirs = self._detect_locales_dirs()

        self.locales_dirs = [Path(d) for d in locales_dirs]
        logger.info(f"TranslationLoader initialized with directories: {self.locales_dirs}")

    def _detect_locales_dirs(self) -> List[str]:
        """
        Auto-detect translation directories from settings.

        Returns:
            List of translation directory paths
        """
        from app.core.config import settings

        dirs = []

        # 1. Core locales directory (community edition, required)
        core_dir = Path(settings.I18N_CORE_LOCALES_DIR)
        if core_dir.exists():
            dirs.append(str(core_dir))
            logger.debug(f"Found core locales directory: {core_dir}")
        else:
            logger.warning(f"Core locales directory not found: {core_dir}")

        # 2. Premium locales directory (enterprise edition, optional)
        if settings.I18N_PREMIUM_LOCALES_DIR:
            premium_dir = Path(settings.I18N_PREMIUM_LOCALES_DIR)
            if premium_dir.exists():
                dirs.append(str(premium_dir))
                logger.debug(f"Found premium locales directory: {premium_dir}")
        else:
            # Auto-detect premium directory
            premium_dir = Path("premium/locales")
            if premium_dir.exists():
                dirs.append(str(premium_dir))
                logger.debug(f"Auto-detected premium locales directory: {premium_dir}")

        if not dirs:
            logger.error("No translation directories found!")

        return dirs

    def get_available_locales(self) -> List[str]:
        """
        Get list of all available locales across all directories.

        Returns:
            List of locale codes (e.g., ['zh', 'en'])
        """
        locales = set()

        for locales_dir in self.locales_dirs:
            if not locales_dir.exists():
                continue

            for locale_dir in locales_dir.iterdir():
                if locale_dir.is_dir() and not locale_dir.name.startswith('.'):
                    locales.add(locale_dir.name)

        return sorted(list(locales))

    def load_locale(self, locale: str) -> Dict[str, Any]:
        """
        Load all translation files for a specific locale from all directories.

        Translation files are merged with priority:
        - Later directories override earlier directories
        - Enterprise translations override community translations

        Args:
            locale: Locale code (e.g., 'zh', 'en')

        Returns:
            Dictionary of translations organized by namespace
            Format: {namespace: {key: value, ...}, ...}
        """
        translations = {}

        # Load from each directory in order (later directories override earlier)
        for locales_dir in self.locales_dirs:
            locale_dir = locales_dir / locale
            if not locale_dir.exists():
                logger.debug(f"Locale directory not found: {locale_dir}")
                continue

            # Load all JSON files in this locale directory
            for json_file in locale_dir.glob("*.json"):
                namespace = json_file.stem

                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        new_translations = json.load(f)

                    # Merge translations (deep merge)
                    if namespace in translations:
                        translations[namespace] = self._deep_merge(
                            translations[namespace],
                            new_translations
                        )
                        logger.debug(
                            f"Merged translations: {locale}/{namespace} from {json_file}"
                        )
                    else:
                        translations[namespace] = new_translations
                        logger.debug(
                            f"Loaded translations: {locale}/{namespace} from {json_file}"
                        )

                except json.JSONDecodeError as e:
                    logger.error(
                        f"Failed to parse JSON file {json_file}: {e}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to load translation file {json_file}: {e}"
                    )

        if not translations:
            logger.warning(f"No translations found for locale: {locale}")

        return translations

    def reload(self, locale: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Reload translation files.

        Args:
            locale: Specific locale to reload. If None, reloads all locales.

        Returns:
            Dictionary of reloaded translations
            Format: {locale: {namespace: {key: value}}}
        """
        if locale:
            logger.info(f"Reloading translations for locale: {locale}")
            return {locale: self.load_locale(locale)}
        else:
            logger.info("Reloading all translations")
            all_translations = {}
            for loc in self.get_available_locales():
                all_translations[loc] = self.load_locale(loc)
            return all_translations

    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """
        Deep merge two dictionaries.

        Args:
            base: Base dictionary
            override: Dictionary with values to override

        Returns:
            Merged dictionary
        """
        result = base.copy()

        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result
