"""
Sandbox Import Patches

在 sandbox 中运行 workflow 前，patch 掉需要数据库连接的模块。
这些 patch 让 workflow 节点可以 import 成功，但实际的 DB 操作
会通过 callback client 回到主 API 执行。

Usage (在 entrypoint 中调用):
    import sandbox_patches
    sandbox_patches.apply()
"""
import sys
import os
import logging
from unittest.mock import MagicMock
from contextlib import contextmanager, asynccontextmanager
from types import ModuleType

logger = logging.getLogger("sandbox.patches")


def apply():
    """Apply all sandbox patches before importing workflow code"""
    _patch_db_module()
    _patch_model_service()
    logger.info("Sandbox patches applied")


def _patch_db_module():
    """Replace app.db with a stub that doesn't connect to postgres

    Provides a real SQLAlchemy declarative Base (so models can define tables)
    but no actual DB connection. All queries go through callback API.
    """
    db_stub = ModuleType("app.db")

    class StubSession:
        def get(self, cls, pk):
            return None
        def query(self, *a, **k):
            return MagicMock()
        def execute(self, *a, **k):
            return MagicMock()
        def add(self, *a, **k): pass
        def close(self): pass
        def refresh(self, obj): pass
        def commit(self): pass
        def flush(self): pass
        def rollback(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

    _session = StubSession()

    def get_db():
        return _session

    @contextmanager
    def get_db_context():
        yield _session

    @contextmanager
    def get_db_read():
        yield _session

    @asynccontextmanager
    async def get_async_db():
        yield _session

    @asynccontextmanager
    async def get_async_db_context():
        yield _session

    def get_pool_status():
        return {"pool_size": 0, "checked_in": 0, "checked_out": 0, "overflow": 0, "total": 0, "usage_percent": 0}

    # Real SQLAlchemy declarative Base so model classes can be defined
    try:
        from sqlalchemy.ext.declarative import declarative_base
        Base = declarative_base()
    except Exception:
        Base = MagicMock()

    db_stub.Base = Base
    db_stub.get_db = get_db
    db_stub.get_db_context = get_db_context
    db_stub.get_db_read = get_db_read
    db_stub.get_async_db = get_async_db
    db_stub.get_async_db_context = get_async_db_context
    db_stub.get_pool_status = get_pool_status
    db_stub.engine = MagicMock()
    db_stub.async_engine = MagicMock()
    db_stub.SessionLocal = MagicMock(return_value=_session)
    db_stub.AsyncSessionLocal = MagicMock()
    db_stub.SANDBOX_MODE = True

    sys.modules["app.db"] = db_stub


def _patch_model_service():
    """Patch ModelConfigService to use env vars for model resolution

    In sandbox, we don't have DB access to look up model configs.
    Instead, the model info is passed via env vars or run_config.
    """
    import httpx

    class SandboxModelConfigService:
        """Resolves model config from env vars instead of DB"""

        def __init__(self, *args, **kwargs):
            pass

        @staticmethod
        def get_model_info(model_config_id, **kwargs):
            """Return model info from environment"""
            from app.schemas.model_schema import ModelInfo
            return ModelInfo(
                model_name=os.getenv("LLM_MODEL_NAME", ""),
                provider=os.getenv("LLM_PROVIDER", "openai"),
                api_key=os.getenv("LLM_API_KEY", ""),
                api_base=os.getenv("LLM_API_BASE", ""),
                capability=[],
                is_omni=False,
                model_type="chat",
            )

        @staticmethod
        async def get_model_info_async(model_config_id, **kwargs):
            return SandboxModelConfigService.get_model_info(model_config_id)

    # Patch into the service module
    try:
        import app.services.model_service as svc_mod
        svc_mod.ModelConfigService = SandboxModelConfigService
    except ImportError:
        pass

    # Also patch ModelApiKeyService
    class SandboxModelApiKeyService:
        @staticmethod
        def get_available_api_key(db, model_config_id, **kwargs):
            return {
                "model_name": os.getenv("LLM_MODEL_NAME", ""),
                "provider": os.getenv("LLM_PROVIDER", "openai"),
                "api_key": os.getenv("LLM_API_KEY", ""),
                "api_base": os.getenv("LLM_API_BASE", ""),
                "api_key_id": None,
                "capability": [],
                "is_omni": False,
            }

        @staticmethod
        async def get_available_api_key_async(db, model_config_id, **kwargs):
            return SandboxModelApiKeyService.get_available_api_key(db, model_config_id)

        @staticmethod
        def record_api_key_usage(*args, **kwargs):
            pass

    try:
        import app.services.model_service as svc_mod
        svc_mod.ModelApiKeyService = SandboxModelApiKeyService
    except ImportError:
        pass
