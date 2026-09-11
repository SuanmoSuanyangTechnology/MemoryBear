"""语言探测中间件（移植并裁剪自 api/app/i18n/middleware.py）。

语言来源（优先级）：
1. 查询参数 ``?lang=en``
2. ``Accept-Language`` 请求头（带 q 值排序，``zh-CN`` 归一为 ``zh``）
3. ``I18N_DEFAULT_LANGUAGE``

老单体还有两级「用户语言偏好 / 租户默认语言」，本服务**未搬**：memory-profile 的
``request.state`` 只有 ``principal``（actor_id/tenant_id/workspace_id），不含语言偏好
——用户档案归宿主平台（ADR 0001），语言偏好不在本服务链路里。

同时做两件事：
- 写 ``request.state.language``，供 ``Depends(get_current_language)`` 取用
- 写 ContextVar（``set_current_locale``），供拿不到 request 的异常构造读取
- 回包加 ``Content-Language`` 头

**注册位置很关键**：必须注册在鉴权中间件**之外**（后 add_middleware 者更外层）。
Starlette 的 BaseHTTPMiddleware 在子任务里执行下游调用，外层中间件设的 ContextVar
会向下传播，内层设的不会向上传播——鉴权中间件若要按请求语言返回 401，语言必须在
它之前就绪。
"""

import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import settings
from src.i18n.exceptions import set_current_locale
from src.infrastructure.logger.config import get_logger

logger = get_logger(__name__)


class LanguageMiddleware(BaseHTTPMiddleware):
    """按优先级判定请求语言，校验受支持性，并注入请求上下文。"""

    async def dispatch(self, request: Request, call_next):
        language = await self._determine_language(request)

        if language not in settings.I18N_SUPPORTED_LANGUAGES:
            logger.warning(
                f"Unsupported language '{language}' requested, "
                f"falling back to default: {settings.I18N_DEFAULT_LANGUAGE}"
            )
            language = settings.I18N_DEFAULT_LANGUAGE

        request.state.language = language
        set_current_locale(language)

        logger.debug(f"Request language set to: {language}")

        response = await call_next(request)
        response.headers["Content-Language"] = language

        return response

    async def _determine_language(self, request: Request) -> str:
        """按优先级判定语言；都不命中则用系统默认。"""
        # 1. 查询参数 ?lang=en
        if "lang" in request.query_params:
            lang = request.query_params["lang"].strip().lower()
            if lang:
                logger.debug(f"Language from query parameter: {lang}")
                return lang

        # 2. Accept-Language 头
        if "Accept-Language" in request.headers:
            lang = self._parse_accept_language(request.headers["Accept-Language"])
            if lang:
                logger.debug(f"Language from Accept-Language header: {lang}")
                return lang

        # 3. 系统默认
        logger.debug(f"Using system default language: {settings.I18N_DEFAULT_LANGUAGE}")
        return settings.I18N_DEFAULT_LANGUAGE

    def _parse_accept_language(self, header: str) -> str | None:
        """解析 Accept-Language，返回第一个受支持的语言码；无则 None。

        形如 ``zh-CN,zh;q=0.9,en;q=0.8``：取主语言码（zh-CN → zh）、按 q 降序，
        再挑第一个受支持者。

        Examples:
            _parse_accept_language("zh-CN,zh;q=0.9,en;q=0.8")  # => "zh"
            _parse_accept_language("en-US,en;q=0.9")           # => "en"
        """
        if not header:
            return None

        languages = []

        for item in header.split(","):
            item = item.strip()
            if not item:
                continue

            parts = item.split(";")
            lang_code = parts[0].strip()
            base_lang = lang_code.split("-")[0].lower()

            quality = 1.0
            if len(parts) > 1:
                q_match = re.search(r"q=([\d.]+)", parts[1])
                if q_match:
                    try:
                        quality = float(q_match.group(1))
                    except ValueError:
                        quality = 1.0

            languages.append((base_lang, quality))

        languages.sort(key=lambda x: x[1], reverse=True)

        for lang_code, _ in languages:
            if lang_code in settings.I18N_SUPPORTED_LANGUAGES:
                return lang_code

        return None


__all__ = ["LanguageMiddleware"]
