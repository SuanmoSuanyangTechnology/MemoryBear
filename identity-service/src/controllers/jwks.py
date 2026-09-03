"""JWKS 发布接口（SDK 验签内部 token 的公钥权威来源）。"""
from fastapi import APIRouter

from src.config import settings
from src.keys import KeyLoader
from src.schemas.jwks import jwks_response

router = APIRouter()

key_loader = KeyLoader(
    primary_pem=settings.INTERNAL_ISSUER_PRIVATE_KEY,
    primary_kid=settings.INTERNAL_ISSUER_KID,
)


@router.get("/jwks")
async def jwks():
    return jwks_response(key_loader)
