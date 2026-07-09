"""
E2B Self-Hosted Orchestrator API

管理 Sandbox 生命周期：创建、销毁、列举、心跳
兼容 E2B SDK 的 REST API 接口
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import sandboxes, templates, health
from app.services.sandbox_manager import SandboxManager
from app.services.redis_store import RedisStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    settings = get_settings()
    
    # Initialize Redis store
    redis_store = RedisStore(settings.REDIS_URL)
    await redis_store.connect()
    app.state.redis_store = redis_store
    
    # Initialize Sandbox Manager
    sandbox_manager = SandboxManager(
        redis_store=redis_store,
        settings=settings,
    )
    await sandbox_manager.initialize()
    app.state.sandbox_manager = sandbox_manager
    
    logger.info(
        "E2B Orchestrator started",
        extra={
            "max_sandboxes": settings.MAX_SANDBOXES,
            "default_timeout": settings.DEFAULT_SANDBOX_TIMEOUT,
        }
    )
    
    yield
    
    # Cleanup
    await sandbox_manager.shutdown()
    await redis_store.disconnect()
    logger.info("E2B Orchestrator shutdown complete")


app = FastAPI(
    title="E2B Self-Hosted Orchestrator",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(health.router, tags=["health"])
app.include_router(sandboxes.router, prefix="/v1", tags=["sandboxes"])
app.include_router(templates.router, prefix="/v1", tags=["templates"])
