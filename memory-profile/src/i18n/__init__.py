"""memory-profile 国际化（i18n）：按请求语言翻译 API 返回的 msg / 错误消息。

移植自老单体 ``api/app/i18n/`` 的**运行时核心**，未搬：``metrics.py`` / ``logger.py``
（指标与结构化翻译日志）、``serializers.py``（老单体业务序列化器）、
``controllers/i18n_controller.py``（14 个翻译管理端点）。理由：本服务是只读分析服务，
不需要在线改翻译/看指标；将来要命中率看板再补 metrics。

用法::

    from src.i18n import t, t_enum, get_translator          # 便捷函数与依赖
    from src.i18n.exceptions import NotFoundError           # 自动翻译的异常

    # 1) 路由内按请求语言翻译
    @router.get("/x")
    async def x(t: Callable = Depends(get_translator)):
        return success(msg=t("analytics.success.graph_data"))

    # 2) 抛自动翻译的异常（响应包体与老单体一致）
    raise NotFoundError(error_key="analytics.errors.end_user_not_found")

    # 3) 无请求上下文处（后台任务）显式指定语言
    from src.i18n import t
    t("errors.common.internal_error", locale="en")

接线见 ``src/interfaces/http/server.py``：LanguageMiddleware 必须注册在鉴权中间件
之外（详见 middleware 模块说明）。
"""

from src.i18n.dependencies import (
    get_current_language,
    get_enum_translator,
    get_translator,
)
from src.i18n.exceptions import (
    BadRequestError,
    ConflictError,
    EndUserNotFoundError,
    ForbiddenError,
    I18nException,
    InternalServerError,
    NotFoundError,
    QuotaExceededError,
    RateLimitExceededError,
    ServiceUnavailableError,
    TokenExpiredError,
    TokenInvalidError,
    UnauthorizedError,
    ValidationError,
    get_current_locale,
    set_current_locale,
)
from src.i18n.loader import TranslationLoader
from src.i18n.middleware import LanguageMiddleware
from src.i18n.service import TranslationService, get_translation_service, t, t_enum

__all__ = [
    "BadRequestError",
    "ConflictError",
    "EndUserNotFoundError",
    "ForbiddenError",
    "I18nException",
    "InternalServerError",
    "LanguageMiddleware",
    "NotFoundError",
    "QuotaExceededError",
    "RateLimitExceededError",
    "ServiceUnavailableError",
    "TokenExpiredError",
    "TokenInvalidError",
    "TranslationLoader",
    "TranslationService",
    "UnauthorizedError",
    "ValidationError",
    "get_current_language",
    "get_current_locale",
    "get_enum_translator",
    "get_translation_service",
    "get_translator",
    "set_current_locale",
    "t",
    "t_enum",
]