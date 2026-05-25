"""
API Key authentication middleware for MCP routes.

Intercepts requests to /v1/mcp/* and:
1. Validates the API key (Authorization / X-API-Key header).
2. Reads the end user's other_id from X-End-User-Other-Id header.
3. Resolves other_id + workspace_id → end_user.
4. Resolves config_id: end_user.memory_config_id → workspace default.
5. Resolves storage_type from workspace configuration.
6. Stores workspace_id, end_user_id, config_id, storage_type via contextvars.

All identity and configuration is determined server-side from headers —
the client never passes end_user_id, config_id, or storage_type as tool
parameters.
"""

import logging
import uuid
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.api_key_auth import extract_api_key_from_request
from app.core.error_codes import BizCode
from app.db import get_db_context

logger = logging.getLogger(__name__)

MCP_PATH_PREFIX = "/v1/mcp"
OTHER_ID_HEADER = "X-End-User-Other-Id"

# ContextVars shared between this middleware and MCP tools.
mcp_workspace_id: ContextVar[uuid.UUID | None] = ContextVar(
    "mcp_workspace_id", default=None
)
mcp_end_user_id: ContextVar[str | None] = ContextVar(
    "mcp_end_user_id", default=None
)
mcp_config_id: ContextVar[str | None] = ContextVar(
    "mcp_config_id", default=None
)
mcp_storage_type: ContextVar[str | None] = ContextVar(
    "mcp_storage_type", default=None
)


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that authenticates and resolves context for MCP requests.

    Header requirements for /v1/mcp/* requests:
      - Authorization: Bearer <api_key>   (or X-API-Key: <api_key>)
      - X-End-User-Other-Id: <other_id>    (external user identifier)

    Resolved context (stored in ContextVars):
      - workspace_id: from the API key
      - end_user_id:  looked up from (workspace_id, other_id)
      - config_id:    end_user.memory_config_id, or workspace default
      - storage_type: from workspace configuration (defaults to "neo4j")
    """

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith(MCP_PATH_PREFIX):
            return await call_next(request)

        # 1. Validate API key
        api_key = extract_api_key_from_request(request)
        if not api_key:
            return self._unauthorized("API Key 不存在", BizCode.API_KEY_NOT_FOUND)

        from app.services.api_key_service import ApiKeyAuthService

        with get_db_context() as db:
            api_key_obj = ApiKeyAuthService.validate_api_key(db, api_key)
            if not api_key_obj:
                return self._unauthorized(
                    "API Key 无效或已过期", BizCode.API_KEY_INVALID
                )
            ApiKeyAuthService.check_app_published(db, api_key_obj)

            if not ApiKeyAuthService.check_scope(api_key_obj, "memory"):
                return self._forbidden(
                    "缺少 memory 权限范围", BizCode.API_KEY_INVALID_SCOPE
                )

            workspace_id = api_key_obj.workspace_id

            # 2. Read other_id from header
            other_id = request.headers.get(OTHER_ID_HEADER, "").strip()
            if not other_id:
                return self._unauthorized(
                    f"缺少 {OTHER_ID_HEADER} 请求头", BizCode.INVALID_PARAMETER
                )

            # 3. Resolve other_id → end_user (scoped to workspace)
            end_user = self._find_end_user(db, other_id, workspace_id)
            if end_user is None:
                return self._unauthorized(
                    f"未找到 other_id='{other_id}' 对应的终端用户",
                    BizCode.USER_NOT_FOUND,
                )

            end_user_id = str(end_user.id)

            # 4. Resolve config_id: end_user → workspace default
            config_id = self._resolve_config_id(db, end_user, workspace_id)
            if config_id is None:
                return self._unauthorized(
                    "未找到可用的记忆配置，请先为 workspace 设置默认配置",
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                )

            # 5. Resolve storage_type from workspace
            storage_type = self._resolve_storage_type(db, workspace_id)

        mcp_workspace_id.set(workspace_id)
        mcp_end_user_id.set(end_user_id)
        mcp_config_id.set(config_id)
        mcp_storage_type.set(storage_type)

        return await call_next(request)

    @staticmethod
    def _find_end_user(db, other_id: str, workspace_id: uuid.UUID):
        from app.models.end_user_model import EndUser

        return (
            db.query(EndUser)
            .filter(
                EndUser.other_id == other_id,
                EndUser.workspace_id == workspace_id,
            )
            .order_by(EndUser.created_at.asc())
            .first()
        )

    @staticmethod
    def _resolve_config_id(db, end_user, workspace_id: uuid.UUID) -> str | None:
        if end_user.memory_config_id is not None:
            return str(end_user.memory_config_id)

        from app.services.memory_config_service import MemoryConfigService

        config_service = MemoryConfigService(db)
        default_config = config_service.get_workspace_default_config(workspace_id)
        if default_config is not None:
            return str(default_config.config_id)

        return None

    @staticmethod
    def _resolve_storage_type(db, workspace_id: uuid.UUID) -> str:
        from app.services.workspace_service import get_workspace_storage_type_without_auth

        storage_type = get_workspace_storage_type_without_auth(db, workspace_id)
        return storage_type if storage_type else "neo4j"

    @staticmethod
    def _unauthorized(message: str, code: BizCode) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "error_code": code.value,
                "message": message,
            },
        )

    @staticmethod
    def _forbidden(message: str, code: BizCode) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "error_code": code.value,
                "message": message,
            },
        )