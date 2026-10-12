"""ACL 规则 CRUD + 变更后全量下发 Redis（设计 §6.3：CRUD API + 变更后推送 acl:rules，SDK 本地读）。

按 internal 接口处理（与 /internal/user-snapshot 一致，网络层隔离；内网可写 ACL 为已知边界）。
每次变更 commit 后全量读表 → rules_to_redis → SET acl:rules；下发失败返回 500（已存未下发，
调用方应感知并重试，避免规则漂移）。

(caller_service, target_service, endpoint) 三元组唯一（约束 uq_acl_rules_triple）：撞键返回
409 + 已存在规则 id，改 effect 须显式走 PUT，避免 POST 静默翻转 allow/deny。
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src import redis as iredis
from src.db import get_async_db
from src.models import AclRule
from src.schemas.acl import rules_to_redis

logger = logging.getLogger(__name__)

router = APIRouter()

REDIS_ACL_KEY = "acl:rules"

ALLOWED_EFFECTS = {"allow", "deny"}


class AclRuleIn(BaseModel):
    caller_service: str = Field(min_length=1)
    target_service: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    effect: str

    @field_validator("caller_service", "target_service", "endpoint")
    @classmethod
    def _strip_not_blank(cls, value: str) -> str:
        # 首尾空白会写出一条永不匹配的死规则（matcher 用精确/fnmatch 比对明文端点）
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


async def _find_conflict(
    session: AsyncSession, body: AclRuleIn, *, exclude_id: str | None = None
) -> AclRule | None:
    """查同三元组的既有规则；exclude_id 用于 PUT 排除自身。"""
    stmt = select(AclRule).where(
        AclRule.caller_service == body.caller_service,
        AclRule.target_service == body.target_service,
        AclRule.endpoint == body.endpoint,
    )
    if exclude_id is not None:
        stmt = stmt.where(AclRule.id != exclude_id)
    return (await session.execute(stmt)).scalars().first()


def _conflict_response(existing: AclRule) -> JSONResponse:
    return JSONResponse(status_code=409, content={
        "error": "acl rule already exists for this (caller_service, target_service, endpoint)",
        "id": str(existing.id),
        "effect": existing.effect,
    })


async def _push_rules(session) -> None:
    """全量读表 → 下发 Redis；任何一步失败抛 SQLAlchemyError/RedisError 由调用方转 500。"""
    rules = (await session.execute(select(AclRule))).scalars().all()
    blob = rules_to_redis([jsonable_encoder(r) for r in rules])
    await iredis.redis.set(REDIS_ACL_KEY, blob)


async def _rule_or_404(session, rule_id: str) -> AclRule:
    rule = await session.get(AclRule, rule_id)
    if rule is None:
        raise _RuleNotFound()
    return rule


class _RuleNotFound(Exception):
    pass


@router.get("/internal/acl-rules")
async def list_rules(session: AsyncSession = Depends(get_async_db)):
    rules = (await session.execute(select(AclRule))).scalars().all()
    return jsonable_encoder(rules)


@router.get("/internal/acl-rules/{rule_id}")
async def get_rule(rule_id: str, session: AsyncSession = Depends(get_async_db)):
    try:
        rule = await _rule_or_404(session, rule_id)
    except _RuleNotFound:
        return JSONResponse(status_code=404, content={"error": "acl rule not found"})
    return jsonable_encoder(rule)


@router.post("/internal/acl-rules")
async def create_rule(body: AclRuleIn, session: AsyncSession = Depends(get_async_db)):
    if body.effect not in ALLOWED_EFFECTS:
        return JSONResponse(status_code=400,
                            content={"error": f"effect must be one of {sorted(ALLOWED_EFFECTS)}"})
    existing = await _find_conflict(session, body)
    if existing is not None:
        return _conflict_response(existing)
    rule = AclRule(caller_service=body.caller_service, target_service=body.target_service,
                   endpoint=body.endpoint, effect=body.effect)
    session.add(rule)
    try:
        await session.commit()
    except IntegrityError:
        # 并发窗口：预检与写入之间有另一事务插入了同三元组，唯一约束兜底
        await session.rollback()
        existing = await _find_conflict(session, body)
        if existing is not None:
            return _conflict_response(existing)
        raise
    await session.refresh(rule)  # 取回 server_default 生成的 id
    try:
        await _push_rules(session)
    except (SQLAlchemyError, RedisError):
        logger.exception("acl rule created but redis push failed")
        return JSONResponse(status_code=500, content={"error": "rule saved but redis push failed"})
    return jsonable_encoder(rule)


@router.put("/internal/acl-rules/{rule_id}")
async def update_rule(rule_id: str, body: AclRuleIn,
                      session: AsyncSession = Depends(get_async_db)):
    if body.effect not in ALLOWED_EFFECTS:
        return JSONResponse(status_code=400,
                            content={"error": f"effect must be one of {sorted(ALLOWED_EFFECTS)}"})
    try:
        rule = await _rule_or_404(session, rule_id)
    except _RuleNotFound:
        return JSONResponse(status_code=404, content={"error": "acl rule not found"})
    existing = await _find_conflict(session, body, exclude_id=rule_id)
    if existing is not None:
        return _conflict_response(existing)
    rule.caller_service = body.caller_service
    rule.target_service = body.target_service
    rule.endpoint = body.endpoint
    rule.effect = body.effect
    try:
        await session.commit()
    except IntegrityError:
        # 并发窗口：同 POST 的约束兜底（rule_id 用路径参数，回滚后 ORM 属性已过期）
        await session.rollback()
        existing = await _find_conflict(session, body, exclude_id=rule_id)
        if existing is not None:
            return _conflict_response(existing)
        raise
    try:
        await _push_rules(session)
    except (SQLAlchemyError, RedisError):
        logger.exception("acl rule updated but redis push failed")
        return JSONResponse(status_code=500, content={"error": "rule saved but redis push failed"})
    return jsonable_encoder(rule)


@router.delete("/internal/acl-rules/{rule_id}")
async def delete_rule(rule_id: str, session: AsyncSession = Depends(get_async_db)):
    try:
        rule = await _rule_or_404(session, rule_id)
    except _RuleNotFound:
        return JSONResponse(status_code=404, content={"error": "acl rule not found"})
    await session.delete(rule)
    await session.commit()
    try:
        await _push_rules(session)
    except (SQLAlchemyError, RedisError):
        logger.exception("acl rule deleted but redis push failed")
        return JSONResponse(status_code=500, content={"error": "rule deleted but redis push failed"})
    return JSONResponse(status_code=200, content={"deleted": rule_id})
