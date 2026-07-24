"""Orchestrator — FastAPI entry point."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings
from app.routes.sandboxes import router as sandbox_router
from app.services.pool_manager import PoolManager
from app.services.redis_store import RedisStore
from app.services.sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    instance_id = str(uuid.uuid4())

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    redis_store = RedisStore(settings, instance_id)
    await redis_store.connect()

    pool_manager = PoolManager(settings, redis_store)
    sandbox_manager = SandboxManager(settings, redis_store, pool_manager)

    app.state.settings = settings
    app.state.api_key = settings.API_KEY
    app.state.redis_store = redis_store
    app.state.pool_manager = pool_manager
    app.state.sandbox_manager = sandbox_manager

    await sandbox_manager.start()
    logger.info("Orchestrator started instance=%s", instance_id)

    yield

    await sandbox_manager.stop()
    await redis_store.disconnect()
    logger.info("Orchestrator stopped")


app = FastAPI(title="Agent Runtime Orchestrator", version="0.1.0", lifespan=lifespan)
app.include_router(sandbox_router)
