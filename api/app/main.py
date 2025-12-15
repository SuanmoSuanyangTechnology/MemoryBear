import os
import subprocess
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse
from app.core.response_utils import fail
from app.core.logging_config import LoggingConfig, get_logger
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode, HTTP_MAPPING
from app.controllers import (
    model_controller,
    task_controller,
    test_controller,
    user_controller,
    auth_controller,
    workspace_controller,
    setup_controller,
    file_controller,
    document_controller,
    knowledge_controller,
    chunk_controller,
    knowledgeshare_controller,
    app_controller,
    upload_controller,
    memory_agent_controller,
    memory_storage_controller,
    memory_dashboard_controller,
    multi_agent_controller,
)

from fastapi import FastAPI, APIRouter

app = FastAPI(title="Data Config API", version="1.0.0")
router = APIRouter(prefix="/memory", tags=["Memory"])

# 管理端 API (JWT 认证)
from app.controllers import manager_router

# 服务端 API (API Key 认证)
from app.controllers.service import service_router

# Initialize logging system
LoggingConfig.setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """使用 FastAPI lifespan 替代 on_event 处理启动/关闭事件"""
    # 应用启动事件

    # 检查是否需要自动升级数据库
    if settings.DB_AUTO_UPGRADE:
        logger.info("开始自动升级数据库...")
        try:
            result = subprocess.run(
                ["alembic", "upgrade", "head"],
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"数据库升级成功: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"数据库升级失败: {e.stderr}")
            raise RuntimeError(f"数据库升级失败: {e.stderr}")
        except Exception as e:
            logger.error(f"运行数据库升级时出错: {str(e)}")
            raise
    else:
        logger.info("自动数据库升级已禁用 (DB_AUTO_UPGRADE=false)")

    logger.info("应用程序启动完成")
    yield
    # 应用关闭事件
    logger.info("应用程序正在关闭")


app = FastAPI(
    title="redbera-mem",
    description="redbera-mem",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for frontend access with environment-extendable origins
default_origins = [
    settings.WEB_URL
]
allowed_origins = list({o for o in (default_origins + settings.CORS_ORIGINS) if o})

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("FastAPI应用程序启动")


@app.get("/", tags=["General"])
def read_root():
    """
    A simple health check endpoint.
    """
    logger.debug("健康检查端点被访问")
    return {"message": "FastAPI is running"}


# 生命周期事件由 lifespan 管理，无需 on_event


# 注册路由
# 管理端 API (JWT 认证)
app.include_router(manager_router, prefix="/api")

# 服务端 API (API Key 认证)
app.include_router(service_router, prefix="/v1")

logger.info("所有路由已注册完成")

# Import additional exception types for specific handling
from app.core.exceptions import (
    ValidationException,
    ResourceNotFoundException,
    PermissionDeniedException,
    AuthenticationException,
    AuthorizationException,
    FileUploadException,
    RateLimitException
)
from app.core.sensitive_filter import SensitiveDataFilter
import traceback


# 处理验证异常
@app.exception_handler(ValidationException)
async def validation_exception_handler(request: Request, exc: ValidationException):
    """处理验证异常"""
    # 过滤敏感信息
    filtered_message, filtered_context = SensitiveDataFilter.filter_message(exc.message, exc.context)

    logger.warning(
        f"Validation error: {filtered_message}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "context": filtered_context,
            "error_code": exc.code.value if isinstance(exc.code, BizCode) else exc.code,
            "cause": str(exc.cause) if exc.cause else None
        },
        exc_info=exc.cause is not None
    )
    biz_code = exc.code if isinstance(exc.code, BizCode) else BizCode.VALIDATION_FAILED
    status_code = HTTP_MAPPING.get(biz_code, 400)
    return JSONResponse(
        status_code=status_code,
        content=fail(code=biz_code.value, msg=filtered_message, error=filtered_message)
    )


# 处理资源不存在异常
@app.exception_handler(ResourceNotFoundException)
async def not_found_exception_handler(request: Request, exc: ResourceNotFoundException):
    """处理资源不存在异常"""
    # 过滤敏感信息
    filtered_message, filtered_context = SensitiveDataFilter.filter_message(exc.message, exc.context)

    logger.info(
        f"Resource not found: {filtered_message}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "context": filtered_context,
            "error_code": exc.code.value if isinstance(exc.code, BizCode) else exc.code,
            "cause": str(exc.cause) if exc.cause else None
        }
    )
    biz_code = exc.code if isinstance(exc.code, BizCode) else BizCode.FILE_NOT_FOUND
    status_code = HTTP_MAPPING.get(biz_code, 404)
    return JSONResponse(
        status_code=status_code,
        content=fail(code=biz_code.value, msg=filtered_message, error=filtered_message)
    )


# 处理权限拒绝异常
@app.exception_handler(PermissionDeniedException)
async def permission_denied_handler(request: Request, exc: PermissionDeniedException):
    """处理权限拒绝异常"""
    # 过滤敏感信息
    filtered_message, filtered_context = SensitiveDataFilter.filter_message(exc.message, exc.context)

    logger.warning(
        f"Permission denied: {filtered_message}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "user": getattr(request.state, "user_id", None),
            "context": filtered_context,
            "error_code": exc.code.value if isinstance(exc.code, BizCode) else exc.code,
            "cause": str(exc.cause) if exc.cause else None
        }
    )
    biz_code = exc.code if isinstance(exc.code, BizCode) else BizCode.FORBIDDEN
    status_code = HTTP_MAPPING.get(biz_code, 403)
    return JSONResponse(
        status_code=status_code,
        content=fail(code=biz_code.value, msg=filtered_message, error=filtered_message)
    )


# 处理认证异常
@app.exception_handler(AuthenticationException)
async def authentication_exception_handler(request: Request, exc: AuthenticationException):
    """处理认证异常"""
    # 过滤敏感信息
    filtered_message, filtered_context = SensitiveDataFilter.filter_message(exc.message, exc.context)

    logger.warning(
        f"Authentication error: {filtered_message}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "context": filtered_context,
            "error_code": exc.code.value if isinstance(exc.code, BizCode) else exc.code,
            "cause": str(exc.cause) if exc.cause else None
        }
    )
    biz_code = exc.code if isinstance(exc.code, BizCode) else BizCode.UNAUTHORIZED
    status_code = HTTP_MAPPING.get(biz_code, 401)
    return JSONResponse(
        status_code=status_code,
        content=fail(code=biz_code.value, msg=filtered_message, error=filtered_message)
    )


# 处理授权异常
@app.exception_handler(AuthorizationException)
async def authorization_exception_handler(request: Request, exc: AuthorizationException):
    """处理授权异常"""
    # 过滤敏感信息
    filtered_message, filtered_context = SensitiveDataFilter.filter_message(exc.message, exc.context)

    logger.warning(
        f"Authorization error: {filtered_message}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "context": filtered_context,
            "error_code": exc.code.value if isinstance(exc.code, BizCode) else exc.code,
            "cause": str(exc.cause) if exc.cause else None
        }
    )
    biz_code = exc.code if isinstance(exc.code, BizCode) else BizCode.FORBIDDEN
    status_code = HTTP_MAPPING.get(biz_code, 403)
    return JSONResponse(
        status_code=status_code,
        content=fail(code=biz_code.value, msg=filtered_message, error=filtered_message)
    )


# 处理文件上传异常
@app.exception_handler(FileUploadException)
async def file_upload_exception_handler(request: Request, exc: FileUploadException):
    """处理文件上传异常"""
    # 过滤敏感信息
    filtered_message, filtered_context = SensitiveDataFilter.filter_message(exc.message, exc.context)

    logger.error(
        f"File upload error: {filtered_message}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "context": filtered_context,
            "error_code": exc.code.value if isinstance(exc.code, BizCode) else exc.code,
            "cause": str(exc.cause) if exc.cause else None
        },
        exc_info=exc.cause is not None
    )
    biz_code = exc.code if isinstance(exc.code, BizCode) else BizCode.FILE_READ_ERROR
    status_code = HTTP_MAPPING.get(biz_code, 500)
    return JSONResponse(
        status_code=status_code,
        content=fail(code=biz_code.value, msg=filtered_message, error=filtered_message)
    )


# 处理限流异常
@app.exception_handler(RateLimitException)
async def rate_limit_exception_handler(request: Request, exc: RateLimitException):
    """处理限流异常"""
    # 过滤敏感信息
    filtered_message, filtered_context = SensitiveDataFilter.filter_message(exc.message, exc.context)

    logger.warning(
        f"Rate limit exceeded: {filtered_message}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "context": filtered_context,
            "error_code": exc.code.value if isinstance(exc.code, BizCode) else exc.code,
            "cause": str(exc.cause) if exc.cause else None
        }
    )

    biz_code = exc.code if isinstance(exc.code, BizCode) else BizCode.RATE_LIMITED
    status_code = HTTP_MAPPING.get(biz_code, 429)

    # 创建响应对象并添加限流头信息
    response = JSONResponse(
        status_code=status_code,
        content=fail(code=biz_code.value, msg=filtered_message, error=filtered_message)
    )

    # 添加限流相关的响应头
    rate_headers = exc.context.get("rate_limit_headers", {}) if exc.context else {}
    for header_name, header_value in rate_headers.items():
        response.headers[header_name] = str(header_value)

    return response


# 业务异常统一处理（使用业务错误码）
@app.exception_handler(BusinessException)
async def business_exception_handler(request: Request, exc: BusinessException):
    """处理通用业务异常"""
    # 过滤敏感信息
    filtered_message, filtered_context = SensitiveDataFilter.filter_message(exc.message, exc.context)

    logger.error(
        f"Business error: {filtered_message}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "context": filtered_context,
            "error_code": exc.code.value if isinstance(exc.code, BizCode) else exc.code,
            "cause": str(exc.cause) if exc.cause else None
        },
        exc_info=exc.cause is not None
    )
    raw_code = exc.code
    if isinstance(raw_code, BizCode):
        biz_code = raw_code
    elif isinstance(raw_code, int):
        try:
            biz_code = BizCode(raw_code)
        except ValueError:
            biz_code = BizCode.BAD_REQUEST
    else:
        biz_code = BizCode.BAD_REQUEST

    status_code = HTTP_MAPPING.get(biz_code, 400)
    return JSONResponse(
        status_code=status_code,
        content=fail(code=biz_code.value, msg=filtered_message, error=filtered_message)
    )


# 统一异常处理：将HTTPException转换为统一响应结构
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """处理HTTP异常"""
    # 过滤敏感信息
    filtered_detail = SensitiveDataFilter.filter_string(str(exc.detail))

    logger.warning(
        f"HTTP exception: {filtered_detail}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "status_code": exc.status_code
        }
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=fail(code=exc.status_code, msg=filtered_detail, error=filtered_detail)
    )


# 捕获未处理的异常，返回统一错误结构
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """处理未捕获的异常"""
    # 记录完整的堆栈跟踪（日志过滤器会自动过滤敏感信息）
    logger.error(
        f"Unhandled exception: {exc}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "exception_type": type(exc).__name__,
            "traceback": traceback.format_exc()
        },
        exc_info=True
    )

    # 生产环境隐藏详细错误信息
    environment = os.getenv("ENVIRONMENT", "development")
    if environment == "production":
        message = "服务器内部错误，请稍后重试"
    else:
        # 开发环境也要过滤敏感信息
        message = SensitiveDataFilter.filter_string(str(exc))

    return JSONResponse(
        status_code=500,
        content=fail(code=BizCode.INTERNAL_ERROR.value, msg=message, error=message)
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
