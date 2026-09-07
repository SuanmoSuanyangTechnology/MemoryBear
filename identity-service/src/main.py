"""身份与计费服务入口。

职责：① 用户/API key 快照组装（供网关读取）；② 内部 token RS256 单一注入密钥加载与部署驱动替换 +
JWKS 发布（SDK 验签权威）；③ ACL 规则维护与下发 Redis；④ 审计队列消费落库
audit_logs；⑤ 快照失效通知订阅 + 定时校正（补偿老单体埋点丢失的失效事件）。

分层：controllers（路由）/ services（业务与后台循环）/ schemas（序列化）/
models（只读模型）/ repositories（DB 访问）；config/db/redis/keys 为顶层基础设施。
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src import db, redis as iredis
from src.controllers import acl, jwks, user_snapshot
from src.services import audit_consumer, notify, reconcile, retention, schema_check

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    # 只读表契约校验（受控复制保障）：core 改列名/删列时启动即 fail-fast
    async with db.get_async_db_context() as session:
        await schema_check.assert_readonly_schema(session)
    await iredis.init_redis()
    # 自有表（acl_rules/audit_logs）由 alembic 迁移链管理（migrations/），部署时 upgrade head
    # 密钥为注入模式（决策 #15，K8s Secret 管理、不轮换）；key_loader 在 controllers/jwks.py 模块级就位
    notify_task = asyncio.create_task(notify.subscribe(iredis.redis))
    audit_task = asyncio.create_task(audit_consumer.audit_loop())
    reconcile_task = asyncio.create_task(reconcile.reconcile_loop())
    retention_task = asyncio.create_task(retention.retention_loop())
    yield
    for t in (notify_task, audit_task, reconcile_task, retention_task):
        t.cancel()
    await asyncio.gather(notify_task, audit_task, reconcile_task, retention_task,
                         return_exceptions=True)
    await iredis.close_redis()
    await db.close_db()


app = FastAPI(title="identity-service", lifespan=lifespan)
app.include_router(jwks.router)
app.include_router(user_snapshot.router)
app.include_router(acl.router)


@app.get("/healthz")
async def healthz():
    # K8s probe：不依赖 Redis/DB，外部依赖故障不影响 liveness 判定
    return {"status": "ok"}
