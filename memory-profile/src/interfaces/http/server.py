from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from src.config import settings
from src.controller import controller_router
from src.i18n.exceptions import I18nException
from src.i18n.middleware import LanguageMiddleware
from src.infrastructure.http.client import close_http_client, init_http_client
from src.infrastructure.logger.config import get_logger
from src.infrastructure.neo4j.client import close_neo4j, init_neo4j
from src.infrastructure.redis.client import close_redis, get_redis, init_redis
from src.middleware.auth import MemoryProfileAuthConfig, MemoryProfileAuthMiddleware
from src.middleware.trace import TraceIdMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_http_client()
    await init_redis()
    await init_neo4j()
    logger.info("public http client ready.")
    yield
    await close_http_client()
    await close_redis()
    await close_neo4j()
    logger.info("public http client closed.")


def _register_i18n_exception_handler(app: FastAPI) -> None:
    """注册 i18n 异常处理器：异常已自带翻译，直接按 detail 回包。

    响应体形状 = ``{code, msg, message, error_code, data, error, time}``，与
    ``utils/response_utils.fail()`` 同构（多出 message/error_code 两个老单体兼容字段）。
    未加老单体的 ``success: False`` 额外字段——两边的 ApiResponse 都没有它，
    只在本处理器加会让成功/失败响应体形状不一致。
    """

    @app.exception_handler(I18nException)
    async def i18n_exception_handler(request: Request, exc: I18nException):
        logger.warning(
            "I18n exception: %s",
            exc.error_key,
            extra={
                "path": request.url.path,
                "method": request.method,
                "error_code": exc.error_code,
                "language": getattr(request.state, "language", None),
                "status_code": exc.status_code,
                "params": exc.params,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
            headers=exc.headers,
        )


def create_http_server() -> FastAPI:
    app = FastAPI(
        title="MemoryBear",
        description="MemoryBear",
        version="1.0.0",
        lifespan=lifespan,
    )
    # 鉴权（后注册者更外层）：无凭据请求在路由前即 401
    app.add_middleware(
        MemoryProfileAuthMiddleware,
        config=MemoryProfileAuthConfig(
            auth_mode=settings.MEMORY_AUTH_MODE,
            service_name=settings.MEMORY_SERVICE_NAME,
            kill_switch_file=settings.MEMORY_KILL_SWITCH_FILE,
            jwks_url=settings.MEMORY_JWKS_URL,
            secret=settings.MEMORY_SECRET,
            api_key_verify_url=settings.MEMORY_API_KEY_VERIFY_URL,
            redis=get_redis,
        ),
    )
    # i18n 语言探测：必须比鉴权**更外层**（最后注册）。
    # 1) Starlette 的 BaseHTTPMiddleware 只在子任务里向下传 ContextVar，内层设的
    #    语言外层看不到——鉴权若要按请求语言返回 401，语言得先就绪；
    # 2) 401/403 响应也会带上正确的 Content-Language。
    # 详见 src/i18n/middleware.py 的模块说明。
    app.add_middleware(LanguageMiddleware)

    # trace_id + 服务内耗时：**最外层**（最后注册）。
    # 1) 计时才覆盖全部处理，含鉴权的 API key 校验 / ACL 判定（direct 模式是一次到
    #    identity 的 HTTP 往返）；
    # 2) trace_id 在鉴权之前就绪，鉴权/语言的日志也带 trace_id，请求级关联不断链。
    # 详见 src/middleware/trace/middleware.py。
    app.add_middleware(TraceIdMiddleware)

    _register_i18n_exception_handler(app)
    app.include_router(controller_router)
    return app