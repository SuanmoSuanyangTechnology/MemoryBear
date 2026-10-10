"""FastAPI application entrypoint for the model service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.error_handlers import register_error_handlers
from .api.router import internal_v1_router
from .api.schemas.response_schema import ApiResponse
from .auth import ModelAuthConfig, ModelAuthMiddleware
from .bootstrap import get_settings
from .config import ModelServiceSettings, configure_settings
from .i18n import LanguageContextMiddleware, load_catalogs
from .i18n import configure as configure_i18n
from .infrastructure import redis as infrastructure_redis
from .logging import setup_logging
from .request_logging import RequestLoggingFastAPI
from .runtime import ProcessRuntime
from .sensitive import SensitiveDataFilter
from .trace import TraceIdMiddleware

logger = logging.getLogger(__name__)

_LEGACY_ERROR_RESPONSES = {
    status_code: {
        "model": ApiResponse,
        "description": "Legacy-compatible error response",
    }
    for status_code in (400, 401, 404, 409, 422, 500)
}


def _sync_seed_models(runtime: ProcessRuntime, settings: ModelServiceSettings) -> None:
    """启动期 YAML 种子同步（M7 D-M7-5：服务=唯一写者）。

    同步 Session 仅在启动路径一次性使用（此刻无并发请求，阻塞可接受）；同步失败
    只告警不阻断启动（口径同宿主 lifespan，YAML 种子缺同步不阻塞服务可用）。
    """
    if not settings.model_load_seed:
        logger.info("预定义模型加载已禁用 (LOAD_MODEL=false)")
        return

    from .services.model_loader import load_models

    logger.info("开始加载预定义模型...")
    try:
        with runtime.database.sync_session() as session:
            result = load_models(session, silent=True)
        logger.info(
            "预定义模型加载完成: 成功%s个, 跳过%s个, 失败%s个",
            result["success"],
            result["skipped"],
            result["failed"],
        )
    except Exception as exc:
        logger.warning("加载预定义模型时出错: %s", exc)


def create_app(settings: ModelServiceSettings | None = None) -> FastAPI:
    """Construct the internal API without connecting to dependencies."""

    load_catalogs()
    service_settings = settings or get_settings()
    setup_logging(service_settings)
    SensitiveDataFilter.configure(service_settings)
    configure_i18n(service_settings.i18n_default_language)
    configure_settings(service_settings)
    runtime = ProcessRuntime(service_settings)
    # 迁入模块（channel_service / usage_publisher / redis_cache）经进程 runtime 取连接
    infrastructure_redis.configure(runtime.redis)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        from .services.channel_registry import run_channel_invalidation_listener
        from .services.usage_consumer import run_usage_consumer

        logger.info("Model service started: %s", service_settings.safe_summary())
        _sync_seed_models(application.state.runtime, service_settings)
        # 多副本渠道缓存失效广播：Redis 不可用时自行退避重连，不阻断启动与请求
        invalidation_listener = asyncio.create_task(run_channel_invalidation_listener())
        # 用量事件消费（承接 G5 前的宿主 beat）：同上自行退避重连
        usage_consumer = asyncio.create_task(
            run_usage_consumer(application.state.runtime.database.async_session)
        )
        try:
            yield
        finally:
            # 先停常驻任务再关连接池：任务取消期间仍可能持有会话/连接
            for task in (invalidation_listener, usage_consumer):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await application.state.runtime.aclose()
            logger.info("Model service stopped")

    application = RequestLoggingFastAPI(
        title="MemoryBear Model Service",
        description="Internal MemoryBear model service API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        responses=_LEGACY_ERROR_RESPONSES,
        lifespan=lifespan,
    )
    application.state.runtime = runtime
    application.add_middleware(TraceIdMiddleware)
    # 鉴权在路由前（后注册者更外层）：无内部凭据请求即 401，不落业务处理
    application.add_middleware(
        ModelAuthMiddleware,
        model_auth=ModelAuthConfig(
            auth_mode=service_settings.model_service_auth_mode,
            service_name=service_settings.model_service_internal_name,
            jwks_url=service_settings.model_service_jwks_url,
            redis=runtime.redis.client,
        ),
    )
    # 语言解析最外层：401 等鉴权期响应也带正确 Content-Language（鉴权内部自取 lang 头）
    application.add_middleware(LanguageContextMiddleware)
    application.include_router(internal_v1_router)

    register_error_handlers(application)

    # 栈默认首请求才构建；此处显式构建，让 gateway 模式的处理器装配失败
    # （缺 enterprise-extensions 包）在启动期暴露，而不是变成运行期每请求 500。
    application.middleware_stack = application.build_middleware_stack()

    return application


app = create_app()
