"""FastAPI 依赖注入（移植自 api/app/i18n/dependencies.py，仅改导入路径）。

用法::

    from src.i18n.dependencies import get_translator

    @router.get("/graph_data")
    async def graph_data(t: Callable = Depends(get_translator)):
        return success(msg=t("analytics.success.graph_data"))
"""

from collections.abc import Callable

from fastapi import Request

from src.config import settings
from src.i18n.service import get_translation_service
from src.infrastructure.logger.config import get_logger

logger = get_logger(__name__)


async def get_current_language(request: Request) -> str:
    """取 LanguageMiddleware 写入 ``request.state.language`` 的当前语言。

    未写入时（理论上仅当中间件未注册）回落到默认语言并告警。
    """
    language = getattr(request.state, "language", None)

    if language is None:
        language = settings.I18N_DEFAULT_LANGUAGE
        logger.warning(
            f"Language not found in request.state, using default: {language}"
        )

    return language


async def get_translator(request: Request) -> Callable:
    """返回绑定当前请求语言的翻译函数 ``t(key, **params) -> str``。"""
    language = await get_current_language(request)
    service = get_translation_service()

    def translate(key: str, **params) -> str:
        return service.translate(key, language, **params)

    return translate


async def get_enum_translator(request: Request) -> Callable:
    """返回绑定当前请求语言的枚举翻译函数 ``t_enum(enum_type, value) -> str``。"""
    language = await get_current_language(request)
    service = get_translation_service()

    def translate_enum(enum_type: str, value: str) -> str:
        return service.translate_enum(enum_type, value, language)

    return translate_enum


__all__ = ["get_current_language", "get_enum_translator", "get_translator"]