"""翻译服务（移植自 api/app/i18n/service.py）。

与老单体的差异：**剥掉了 metrics.py / logger.py 依赖**——老单体版每个翻译都打点
（track_translation_request / monitor_performance）并写结构化翻译日志；本服务当前
不需要指标与翻译专属日志，缺失翻译统一走本模块 logger.warning（受
``I18N_LOG_MISSING_TRANSLATIONS`` 开关控制）。若将来要做命中率看板，再补回 metrics。

对外形态不变：
- ``t(key, locale=None, **params)`` 便捷函数
- ``t_enum(enum_type, value, locale=None)`` 枚举展示名
- ``get_translation_service()`` 单例（首次调用时才加载 locales）
"""

from typing import Any

from src.config import settings
from src.i18n.cache import TranslationCache
from src.i18n.loader import TranslationLoader
from src.infrastructure.logger.config import get_logger

logger = get_logger(__name__)


class TranslationService:
    """翻译查找：内存缓存 + 参数插值 + fallback（目标语言 → fallback 语言 → 原 key）。"""

    def __init__(self, locales_dirs: list | None = None):
        """
        Args:
            locales_dirs: 翻译目录列表；None 时由 loader 按 settings 自动探测。
        """
        self.loader = TranslationLoader(locales_dirs)
        self.default_locale = settings.I18N_DEFAULT_LANGUAGE
        self.fallback_locale = settings.I18N_FALLBACK_LANGUAGE
        self.log_missing = settings.I18N_LOG_MISSING_TRANSLATIONS
        self.enable_cache = settings.I18N_ENABLE_TRANSLATION_CACHE

        lru_cache_size = getattr(settings, "I18N_LRU_CACHE_SIZE", 1000)
        self.cache = TranslationCache(
            max_lru_size=lru_cache_size,
            enable_lazy_load=False  # 启动即全量加载
        )

        self._load_all_locales()

        logger.info(
            f"TranslationService initialized with default locale: {self.default_locale}, "
            f"LRU cache size: {lru_cache_size}"
        )

    def _load_all_locales(self):
        """把磁盘上所有可用 locale 加载进内存缓存。"""
        available_locales = self.loader.get_available_locales()
        logger.info(f"Loading translations for locales: {available_locales}")

        for locale in available_locales:
            locale_data = self.loader.load_locale(locale)
            self.cache.set_locale_data(locale, locale_data)

        logger.info(f"Loaded {len(available_locales)} locales into cache")

    def translate(
        self,
        key: str,
        locale: str | None = None,
        **params
    ) -> str:
        """翻译一个 key。

        key 形如 ``"namespace.key.subkey"``（namespace = locales 下的文件名）。
        支持 ``{param}`` 插值；缺翻译时按 fallback 语言再试，最终返回 key 本身
        （**不抛异常**——避免翻译缺失把业务打挂）。

        Args:
            key: 翻译键，如 "errors.common.not_found"。
            locale: 目标语言；None 用 default_locale。
            **params: 插值参数，如 field="名称"。

        Examples:
            translate("errors.common.not_found", "zh")   # => "请求的资源不存在"
            translate("common.validation.required", "zh", field="名称")  # => "名称不能为空"
        """
        if locale is None:
            locale = self.default_locale

        parts = key.split(".", 1)
        if len(parts) < 2:
            # 没有 namespace 前缀的 key 无法定位文件
            if self.log_missing:
                logger.warning(f"Invalid translation key format: {key}")
            return key

        namespace = parts[0]
        key_path = parts[1].split(".")

        translation = self.cache.get_translation(locale, namespace, key_path)

        if translation is None and locale != self.fallback_locale:
            translation = self.cache.get_translation(
                self.fallback_locale, namespace, key_path
            )

        if translation is None:
            if self.log_missing:
                logger.warning(f"Missing translation: {key} (locale: {locale})")
            return key

        if params:
            try:
                translation = translation.format(**params)
            except KeyError as e:
                # 模板里的占位符没被调用方填：返回未插值原文，记 error
                logger.error(f"Missing parameter in translation '{key}': {e}")
            except Exception as e:
                logger.error(f"Error formatting translation '{key}': {e}")

        return translation

    def translate_enum(
        self,
        enum_type: str,
        value: str,
        locale: str | None = None
    ) -> str:
        """翻译枚举展示名，等价于 ``translate(f"enums.{enum_type}.{value}")``。

        Examples:
            translate_enum("node_type", "Dialogue", "zh")      # => "单次对话"
            translate_enum("identity_status", "temporary", "zh")  # => "临时"
        """
        key = f"enums.{enum_type}.{value}"
        return self.translate(key, locale)

    def has_translation(self, key: str, locale: str) -> bool:
        """key 在该 locale 下是否已有翻译（不做 fallback）。"""
        parts = key.split(".", 1)
        if len(parts) < 2:
            return False

        namespace = parts[0]
        key_path = parts[1].split(".")

        translation = self.cache.get_translation(locale, namespace, key_path)
        return translation is not None

    def reload(self, locale: str | None = None):
        """重载翻译文件（locale=None 为全量）。"""
        logger.info(f"Reloading translations for locale: {locale or 'all'}")

        if locale:
            locale_data = self.loader.load_locale(locale)
            self.cache.set_locale_data(locale, locale_data)
            self.cache.clear_locale(locale)
        else:
            self._load_all_locales()
            self.cache.clear_lru()

        logger.info("Translation reload completed")

    def get_available_locales(self) -> list:
        return self.cache.get_loaded_locales()

    def get_cache_stats(self) -> dict[str, Any]:
        return self.cache.get_stats()

    def get_memory_usage(self) -> dict[str, Any]:
        return self.cache.get_memory_usage()

    def get_loaded_dirs(self) -> list:
        return self.loader.locales_dirs


# 全局单例（首次调用才加载 locales，避免 import 期读盘）
_translation_service: TranslationService | None = None


def get_translation_service() -> TranslationService:
    """取全局翻译服务单例。"""
    global _translation_service
    if _translation_service is None:
        _translation_service = TranslationService()
    return _translation_service


def t(key: str, locale: str | None = None, **params) -> str:
    """翻译便捷函数；locale=None 时用默认语言（**不含请求语言协商**）。

    注意：请求相关场景请用 ``Depends(get_translator)`` 或先取
    ``get_current_locale()``——本函数不感知当前请求。
    """
    service = get_translation_service()
    return service.translate(key, locale, **params)


def t_enum(enum_type: str, value: str, locale: str | None = None) -> str:
    """枚举展示名便捷函数（同上，不感知请求语言）。"""
    service = get_translation_service()
    return service.translate_enum(enum_type, value, locale)