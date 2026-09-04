"""API key 查询（identity.models 本地只读模型，直连现有库）。"""
from sqlalchemy import select

from src.models import ApiKey


async def get_api_key(session, api_key: str) -> ApiKey | None:
    return (await session.execute(
        select(ApiKey).where(ApiKey.api_key == api_key)
    )).scalars().first()


async def get_inactive_keys_since(session, since) -> list[ApiKey]:
    """since 之后更新过的禁用/吊销 API key（校正任务只删不建）。"""
    return (await session.execute(
        select(ApiKey).where(ApiKey.updated_at > since, ApiKey.is_active == False)  # noqa: E712
    )).scalars().all()
