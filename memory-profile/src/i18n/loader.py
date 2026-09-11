"""翻译文件加载器（移植自 api/app/i18n/loader.py，仅改导入路径）。

支持多目录加载：core（社区版，必需）+ premium（企业版，可选，后加载者覆盖前者）。
"""

import json
from pathlib import Path
from typing import Any

from src.config import settings
from src.infrastructure.logger.config import get_logger

logger = get_logger(__name__)


class TranslationLoader:
    """翻译文件加载器：多目录合并 + 热重载 + 语言自动探测。"""

    def __init__(self, locales_dirs: list[str] | None = None):
        """
        Args:
            locales_dirs: 翻译目录列表；None 时按 settings 自动探测。
        """
        if locales_dirs is None:
            locales_dirs = self._detect_locales_dirs()

        self.locales_dirs = [Path(d) for d in locales_dirs]
        logger.info(f"TranslationLoader initialized with directories: {self.locales_dirs}")

    def _detect_locales_dirs(self) -> list[str]:
        """按 settings 探测翻译目录：core 必需 + premium 可选。"""
        dirs = []

        # 1. core（社区版，必需）
        core_dir = Path(settings.I18N_CORE_LOCALES_DIR)
        if core_dir.exists():
            dirs.append(str(core_dir))
            logger.debug(f"Found core locales directory: {core_dir}")
        else:
            logger.warning(f"Core locales directory not found: {core_dir}")

        # 2. premium（企业版，可选）
        if settings.I18N_PREMIUM_LOCALES_DIR:
            premium_dir = Path(settings.I18N_PREMIUM_LOCALES_DIR)
            if premium_dir.exists():
                dirs.append(str(premium_dir))
                logger.debug(f"Found premium locales directory: {premium_dir}")
        else:
            premium_dir = Path("premium/locales")
            if premium_dir.exists():
                dirs.append(str(premium_dir))
                logger.debug(f"Auto-detected premium locales directory: {premium_dir}")

        if not dirs:
            logger.error("No translation directories found!")

        return dirs

    def get_available_locales(self) -> list[str]:
        """所有目录下可用的 locale 列表（如 ['en', 'zh']）。"""
        locales = set()

        for locales_dir in self.locales_dirs:
            if not locales_dir.exists():
                continue

            for locale_dir in locales_dir.iterdir():
                if locale_dir.is_dir() and not locale_dir.name.startswith('.'):
                    locales.add(locale_dir.name)

        return sorted(locales)

    def load_locale(self, locale: str) -> dict[str, Any]:
        """加载某 locale 的全部翻译文件，按目录顺序深度合并（后者覆盖前者）。

        Returns:
            {namespace: {key: value}}，namespace 即 JSON 文件名（不含扩展名）。
        """
        translations = {}

        for locales_dir in self.locales_dirs:
            locale_dir = locales_dir / locale
            if not locale_dir.exists():
                logger.debug(f"Locale directory not found: {locale_dir}")
                continue

            for json_file in locale_dir.glob("*.json"):
                namespace = json_file.stem

                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        new_translations = json.load(f)

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
                    logger.error(f"Failed to parse JSON file {json_file}: {e}")
                except Exception as e:
                    logger.error(f"Failed to load translation file {json_file}: {e}")

        if not translations:
            logger.warning(f"No translations found for locale: {locale}")

        return translations

    def reload(self, locale: str | None = None) -> dict[str, dict[str, Any]]:
        """重载翻译文件；locale=None 时重载全部。"""
        if locale:
            logger.info(f"Reloading translations for locale: {locale}")
            return {locale: self.load_locale(locale)}

        logger.info("Reloading all translations")
        all_translations = {}
        for loc in self.get_available_locales():
            all_translations[loc] = self.load_locale(loc)
        return all_translations

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """递归合并：两边同为 dict 则下钻，否则 override 覆盖。"""
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