from __future__ import annotations

from error_core.codes import HTTP_STATUS_TO_BIZ_CODE, BizCode, http_status_for
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.router import api_router
from .auth import authenticate
from .errors import BusinessException
from .response import fail
from .trace import TraceIdMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="file-service", docs_url=None, redoc_url=None,
                  openapi_url="/openapi.json")

    # Starlette's ExceptionMiddleware sits below user middleware, so
    # BusinessException raised here must be turned into a response locally.
    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        try:
            await authenticate(request)
        except BusinessException as exc:
            code = int(exc.code)
            status = exc.status_code or http_status_for(code)
            return JSONResponse(status_code=status, content=fail(code, exc.message))
        return await call_next(request)

    app.add_middleware(TraceIdMiddleware)
    app.include_router(api_router)

    @app.exception_handler(BusinessException)
    async def _business(request: Request, exc: BusinessException):
        code = int(exc.code)
        status = exc.status_code or http_status_for(code)
        return JSONResponse(status_code=status, content=fail(code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422,
                            content=fail(BizCode.VALIDATION_FAILED.value, "Validation failed"))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = HTTP_STATUS_TO_BIZ_CODE.get(exc.status_code)
        if code is None:
            code = BizCode.INTERNAL_ERROR if exc.status_code >= 500 else BizCode.BAD_REQUEST
        return JSONResponse(status_code=exc.status_code, content=fail(code.value, str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        return JSONResponse(status_code=500,
                            content=fail(BizCode.INTERNAL_ERROR.value, "Internal server error"))

    return app


app = create_app()
