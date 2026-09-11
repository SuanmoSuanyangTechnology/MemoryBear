"""国际化异常（协议性失败，C 类响应）。

设计要点（与老单体 i18n 异常的三处差异）：

1. **业务码是唯一真源，HTTP 状态由码推导**。子类只声明 ``biz_code`` 与默认翻译键；
   状态取自 ``constants/error_codes.py`` 的 ``HTTP_MAPPING``。老单体是反过来的
   （异常类硬编码 status_code，另一处又有 HTTP_MAPPING），导致同一个"资源不存在"
   在两处分别是 404 与 400 —— 本服务消除这种漂移。
2. **``error_code`` 取 ``BizCode.name``**（如 ``NOT_FOUND``），是客户端稳定标识，
   按**错误的语义**命名，不从翻译键推导（老单体从键推导出 ``COMMON_NOT_FOUND``
   之类，客户端要按 ``error_code === 'QUOTA_EXCEEDED'`` 判断就对不上）。
3. **翻译键只负责选文案**，不进响应契约——换文案不影响客户端分支。

响应包体（与 ``utils/response_utils.fail()`` 同构，多 ``message``/``error_code``
两个老单体兼容字段）::

    {"code": 24001, "msg": "…", "message": "…", "error_code": "END_USER_NOT_FOUND",
     "data": {...}, "error": "…", "time": 1712345678901}

用法::

    raise EndUserNotFoundError()                      # 用类默认的码与文案
    raise NotFoundError(error_key="errors.memory.not_found")   # 换文案不换码
    raise BadRequestError(biz_code=BizCode.INVALID_PARAMETER)  # 换码（状态随之）

注：**没有状态码逃生门**。要不同 HTTP 状态就换码——状态与码一一对应是刻意的，
避免"N 处各写一个状态"再次漂移。
"""

import time
from contextvars import ContextVar
from typing import ClassVar

from fastapi import HTTPException

from src.config import settings
from src.constants.error_codes import BizCode, http_status_for
from src.i18n.service import get_translation_service
from src.infrastructure.logger.config import get_logger

logger = get_logger(__name__)

# 当前请求语言：由 LanguageMiddleware 写入，供拿不到 request 的地方（异常构造、
# 后台任务）读取。
_current_locale: ContextVar[str | None] = ContextVar("current_locale", default=None)


def set_current_locale(locale: str) -> None:
    """设置当前请求语言（由 LanguageMiddleware 调用）。"""
    _current_locale.set(locale)


def get_current_locale() -> str | None:
    """读取当前请求语言；未设置时为 None。"""
    return _current_locale.get()


class I18nException(HTTPException):
    """自动翻译的错误消息基类。

    子类声明 ``biz_code``（决定 HTTP 状态与 ``error_code``）与 ``default_error_key``
    （决定默认文案键）。
    """

    biz_code: ClassVar[BizCode | None] = None
    default_error_key: ClassVar[str] = "errors.common.bad_request"

    def __init__(
        self,
        error_key: str | None = None,
        *,
        biz_code: BizCode | None = None,
        locale: str | None = None,
        headers: dict[str, str] | None = None,
        **params
    ):
        """
        Args:
            error_key: 翻译键；None 用子类默认键。
            biz_code: 业务码；None 用子类声明的码。HTTP 状态与响应 ``error_code``
                都由它推导，故换码即换状态。
            locale: 目标语言；None 取当前请求语言。
            headers: 额外响应头。
            **params: 文案插值参数，同时回填到响应 ``data``。
        """
        code = biz_code or self.biz_code
        if code is None:
            raise TypeError(
                f"{type(self).__name__} 未声明 biz_code，且调用时未传入"
            )

        self.biz_code = code
        self.error_key = error_key or self.default_error_key
        self.error_code = code.name              # 客户端稳定标识
        self.params = params

        status_code = http_status_for(code)      # 唯一真源，未登记即报错

        if locale is None:
            locale = self._get_current_locale()

        message = get_translation_service().translate(self.error_key, locale, **params)

        detail = {
            "code": int(code),
            "msg": message,
            "message": message,
            "error_code": self.error_code,
            "data": params if params else {},
            "error": message,
            "time": int(time.time() * 1000),
        }

        super().__init__(status_code=status_code, detail=detail, headers=headers)

        logger.debug(
            "I18nException raised: %s (key: %s, locale: %s, http: %s, code: %s)",
            self.error_code, self.error_key, locale, status_code, int(code),
        )

    def _get_current_locale(self) -> str:
        """当前请求语言；取不到时回落到默认语言。"""
        try:
            locale = _current_locale.get()
            if locale:
                return locale
        except Exception as e:  # ContextVar 理论上不会抛，防御性兜底
            logger.debug(f"Could not get locale from context: {e}")

        return settings.I18N_DEFAULT_LANGUAGE


# ---------------------------------------------------------------------------
# 通用（20xxx）
# ---------------------------------------------------------------------------

class BadRequestError(I18nException):
    """请求错误（HTTP 400）。"""

    biz_code = BizCode.BAD_REQUEST
    default_error_key = "errors.common.bad_request"


class ValidationError(I18nException):
    """校验失败（HTTP 422——前端不覆盖该状态，服务端文案能露出）。"""

    biz_code = BizCode.VALIDATION_FAILED
    default_error_key = "errors.common.validation_failed"


# ---------------------------------------------------------------------------
# 认证（21xxx）
# ---------------------------------------------------------------------------

class UnauthorizedError(I18nException):
    """未认证（HTTP 401——前端据此跳登录）。"""

    biz_code = BizCode.UNAUTHORIZED
    default_error_key = "errors.auth.unauthorized"


class TokenInvalidError(UnauthorizedError):
    """令牌无效（HTTP 401）。"""

    biz_code = BizCode.TOKEN_INVALID
    default_error_key = "errors.auth.token_invalid"


class TokenExpiredError(UnauthorizedError):
    """令牌过期（HTTP 401）。"""

    biz_code = BizCode.TOKEN_EXPIRED
    default_error_key = "errors.auth.token_expired"


# ---------------------------------------------------------------------------
# 鉴权（22xxx）
# ---------------------------------------------------------------------------

class ForbiddenError(I18nException):
    """无权限（HTTP 403）。"""

    biz_code = BizCode.FORBIDDEN
    default_error_key = "errors.auth.forbidden"


# ---------------------------------------------------------------------------
# 资源（24xxx）—— 刻意 400 而非 404，见 constants/error_codes.py 模块说明
# ---------------------------------------------------------------------------

class NotFoundError(I18nException):
    """资源不存在（HTTP 400 + 码 NOT_FOUND）。"""

    biz_code = BizCode.NOT_FOUND
    default_error_key = "errors.common.not_found"


class EndUserNotFoundError(NotFoundError):
    """终端用户不存在或不属于当前工作空间（HTTP 400 + 码 END_USER_NOT_FOUND）。

    三种情况（不存在 / 已软删 / 不属于本 workspace）**统一用同一句文案**，不区分
    原因——否则可据响应差异探测出别的 workspace 里有哪些 end_user_id。
    """

    biz_code = BizCode.END_USER_NOT_FOUND
    default_error_key = "errors.end_user.not_found"


# ---------------------------------------------------------------------------
# 冲突与业务状态（25xxx）
# ---------------------------------------------------------------------------

class ConflictError(I18nException):
    """状态冲突（HTTP 409）。"""

    biz_code = BizCode.STATE_CONFLICT
    default_error_key = "errors.common.conflict"


# ---------------------------------------------------------------------------
# 配额与限流（23xxx）
# ---------------------------------------------------------------------------

class RateLimitExceededError(I18nException):
    """请求过于频繁（HTTP 429）。"""

    biz_code = BizCode.RATE_LIMIT_EXCEEDED
    default_error_key = "errors.api.rate_limit_exceeded"


class QuotaExceededError(I18nException):
    """配额超限（HTTP 402）。``resource`` 传资源键时翻成当前语言的展示名。"""

    biz_code = BizCode.QUOTA_EXCEEDED
    default_error_key = "errors.api.quota_exceeded"

    # resource key -> i18n 展示名键（locales/*/errors.json 的 quota_resources 域）
    _RESOURCE_KEY_MAP: ClassVar[dict[str, str]] = {
        "workspace": "errors.quota_resources.workspace",
        "end_user": "errors.quota_resources.end_user",
        "memory_engine": "errors.quota_resources.memory_engine",
        "api_ops_rate_limit": "errors.quota_resources.api_ops_rate_limit",
    }

    def __init__(self, resource: str | None = None, **params):
        if resource:
            resource_key = self._RESOURCE_KEY_MAP.get(resource)
            if resource_key:
                try:
                    locale = _current_locale.get() or settings.I18N_DEFAULT_LANGUAGE
                    params["resource"] = get_translation_service().translate(
                        resource_key, locale
                    )
                except Exception:
                    params["resource"] = resource
            else:
                params["resource"] = resource

        super().__init__(**params)


# ---------------------------------------------------------------------------
# 系统与依赖（29xxx）
# ---------------------------------------------------------------------------

class InternalServerError(I18nException):
    """服务内部错误（HTTP 500）。"""

    biz_code = BizCode.INTERNAL_ERROR
    default_error_key = "errors.common.internal_error"


class ServiceUnavailableError(I18nException):
    """依赖不可用（HTTP 503：PG/ES/网关等；前端不覆盖该状态）。"""

    biz_code = BizCode.SERVICE_UNAVAILABLE
    default_error_key = "errors.common.service_unavailable"


__all__ = [
    "BadRequestError",
    "ConflictError",
    "EndUserNotFoundError",
    "ForbiddenError",
    "I18nException",
    "InternalServerError",
    "NotFoundError",
    "QuotaExceededError",
    "RateLimitExceededError",
    "ServiceUnavailableError",
    "TokenExpiredError",
    "TokenInvalidError",
    "UnauthorizedError",
    "ValidationError",
    "get_current_locale",
    "set_current_locale",
]