"""审计保留策略：周期性清理超龄审计行（设计 §7 append-only 审计流 ≥180 天）。

AUDIT_RETENTION_DAYS=0 时禁用（不启动任务）；清理失败仅记日志，下轮再试。
"""
import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src import db
from src.config import settings

logger = logging.getLogger(__name__)


async def retention_once(session) -> int:
    days = settings.AUDIT_RETENTION_DAYS
    if days <= 0:
        return 0
    result = await session.execute(text(
        "DELETE FROM audit_logs WHERE ts < now() - make_interval(days => :days)"),
        {"days": days})
    await session.commit()
    return result.rowcount or 0


async def retention_loop():
    if settings.AUDIT_RETENTION_DAYS <= 0:
        logger.info("audit retention disabled (AUDIT_RETENTION_DAYS=%s)",
                    settings.AUDIT_RETENTION_DAYS)
        return
    while True:
        try:
            async with db.get_async_db_context() as session:
                n = await retention_once(session)
                if n:
                    logger.info("audit retention removed %s rows", n)
        except SQLAlchemyError:
            logger.exception("audit retention failed")
        await asyncio.sleep(settings.AUDIT_RETENTION_INTERVAL_SEC)
