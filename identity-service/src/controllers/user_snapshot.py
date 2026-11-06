"""网关回源接口：按 user_id 组装用户快照（未来版本 gateway backfill 接入点）。"""
from error_core import BizCode
from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from src import redis as iredis
from src.controllers._errors import error_response
from src.db import get_async_db
from src.services.snapshot import build_user_snapshot

router = APIRouter()


@router.get("/internal/user-snapshot/{user_id}")
async def user_snapshot(user_id: str, session: AsyncSession = Depends(get_async_db)):
    snap = await build_user_snapshot(session, user_id, redis=iredis.redis)
    if snap is None:
        return error_response(BizCode.USER_SNAPSHOT_NOT_FOUND, "user not found")
    return jsonable_encoder(snap)
