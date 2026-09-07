"""HTTP 入站/出站 interceptor。入站：验内部 token → claims 权威读身份；出站：附内部 token + 身份头。"""
import logging

from fastapi import Request, HTTPException
from jose.exceptions import JWTError

from auth_sdk.token import TokenVerifier
from auth_sdk.acl import AclMatcher
from auth_sdk.schema import UserContext

logger = logging.getLogger(__name__)


class InboundInterceptor:
    def __init__(self, verifier: TokenVerifier, acl: AclMatcher, service_name: str):
        self._verifier = verifier
        self._acl = acl
        self._service_name = service_name

    async def authenticate(self, request: Request) -> UserContext:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = auth.removeprefix("Bearer ").strip()
        try:
            payload = await self._verifier.verify_jwt(token, expected_aud=self._service_name)
        except (JWTError, ValueError) as exc:  # verify_jwt 的全部失败路径（签名/aud/type/密钥缺失）
            logger.warning("invalid internal token rejected: %s", exc)
            raise HTTPException(status_code=401, detail="invalid internal token")  # 固定文案，不回显异常文本
        # claims 权威（2.4）：身份头与 claims 不一致时以 claims 为准，忽略身份头
        caller = payload.get("iss")
        if not self._acl.allowed(caller, self._service_name, f"{request.method} {request.url.path}"):
            raise HTTPException(status_code=403, detail="acl denied")
        sub = payload.get("sub")
        if sub is None:
            logger.warning("internal token missing sub claim")
            raise HTTPException(status_code=401, detail="invalid internal token")
        return UserContext(
            user_id=sub, tenant_id=payload.get("tenant_id", ""),
            workspace_id=payload.get("workspace_id", ""),
            roles=tuple(payload.get("roles", [])),
        )
