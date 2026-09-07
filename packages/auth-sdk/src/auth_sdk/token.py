"""验签（多算法 HS256/RS256 + JWKS kid 缓存）与内部 token 签发 Provider。"""
from datetime import datetime, timedelta, timezone
from typing import Protocol
import uuid

from jose import jwt, JWTError

from auth_sdk.schema import ApiKeyContext, UserContext


class TokenVerifier:
    """多算法验签：HS256 用 secret；RS256 用 JWKS（按 kid 匹配，kid 未命中强制刷新）。"""

    def __init__(self, secret: str | None = None, jwks_keys: list[dict] | None = None,
                 jwks_fetch=None):
        self.secret = secret
        self._jwks_keys = jwks_keys or []
        self._jwks_fetch = jwks_fetch  # async callable -> list[dict]，kid 未命中时回源拉取

    async def verify_jwt(self, token: str, expected_aud: str | None = None,
                         token_type: str | None = None) -> dict:
        unverified = jwt.get_unverified_header(token)
        alg = unverified.get("alg")
        if alg == "HS256":
            if not self.secret:
                raise ValueError("HS256 token but no secret configured")
            payload = jwt.decode(token, self.secret, algorithms=["HS256"])
        else:
            kid = unverified.get("kid")
            key = self._find_key(kid)
            if key is None and self._jwks_fetch is not None:
                self._jwks_keys = await self._jwks_fetch()
                key = self._find_key(kid)
            if key is None:
                raise ValueError(f"no JWKS key for kid={kid}")
            payload = jwt.decode(token, key, algorithms=["RS256"], audience=expected_aud, options={"verify_aud": expected_aud is not None})
        if expected_aud is not None:
            if payload.get("aud") != expected_aud:
                raise ValueError(f"aud mismatch: {payload.get('aud')} != {expected_aud}")
        # type 校验（对齐老单体 security.py verify_token）：token_type 非 None 时强制比对，
        # 防 refresh token（同 SECRET_KEY、TTL 更长）经网关访问业务接口。JWTError 由调用方转 401。
        if token_type is not None and payload.get("type") != token_type:
            raise JWTError(f"token type mismatch: expected {token_type}, got {payload.get('type')}")
        return payload

    def _find_key(self, kid: str) -> dict | None:
        return next((k for k in self._jwks_keys if k.get("kid") == kid), None)


class TokenIssuer(Protocol):
    def issue_internal_token(self, identity: UserContext | ApiKeyContext, aud: str) -> str: ...


class LocalTokenIssuer:
    """首期签发实现：本地持 RS256 私钥签发内部 token（决策 #5）。

    UserContext → sub=user_id；ApiKeyContext → sub=user_id（key 创建者，老单体"key 代理
    creator"语义），user_id 缺失（creator 已删/旧快照）退化为 "ak:{api_key_id}" 防与用户
    sub 撞车 + auth_type="api_key" + scopes。下游验签后按 auth_type 区分身份类型。
    """

    def __init__(self, private_key: str, kid: str, ttl: int = 120, leeway: int = 30,
                 issuer: str = "gateway"):
        self.private_key = private_key
        self.kid = kid
        self.ttl = ttl
        self.leeway = leeway
        self.issuer = issuer

    def issue_internal_token(self, identity: UserContext | ApiKeyContext, aud: str) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "iss": self.issuer, "aud": aud,
            "tenant_id": identity.tenant_id, "workspace_id": identity.workspace_id,
            "jti": str(uuid.uuid4()),
            "iat": now, "exp": now + timedelta(seconds=self.ttl),
        }
        if isinstance(identity, ApiKeyContext):
            payload["sub"] = identity.user_id or f"ak:{identity.api_key_id}"
            payload["auth_type"] = "api_key"
            payload["scopes"] = list(identity.scopes)
        else:
            payload["sub"] = identity.user_id
            payload["roles"] = list(identity.roles)
        return jwt.encode(payload, self.private_key, algorithm="RS256", headers={"kid": self.kid})
