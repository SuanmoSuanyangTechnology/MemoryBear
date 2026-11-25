"""Domain modules exposed by the profile service."""

from __future__ import annotations

from fastapi import APIRouter

from src.modules.analytics import router as analytics_router
from src.modules.dashboard import router as dashboard_router
from src.modules.display import router as display_router
from src.modules.emotion_analytics import router as emotion_analytics_router
from src.modules.emotion_reply import router as emotion_reply_router
from src.modules.episodic_memory import router as episodic_memory_router
from src.modules.explicit_memory import router as explicit_memory_router
from src.modules.files import router as files_router
from src.modules.forgetting import router as forgetting_router
from src.modules.health import router as health_router
from src.modules.implicit_memory import router as implicit_memory_router
from src.modules.memory_config import (
    community_router as memory_config_community_router,
)
from src.modules.memory_config import router as memory_config_router
from src.modules.memory_read import router as memory_read_router
from src.modules.perceptual import router as perceptual_router
from src.modules.preference_recall.routes import router as preference_recall_router
from src.modules.reflection import router as reflection_router
from src.modules.reflection_logs.routes import router as reflection_logs_router
from src.modules.short import router as short_router
from src.modules.value_ranking import router as value_ranking_router
from src.modules.working_memory import router as working_memory_router
from src.modules.workspace import router as workspace_router
from src.modules.write import router as write_router

router = APIRouter(prefix="/internal/v1")
router.include_router(health_router)
router.include_router(analytics_router)
router.include_router(dashboard_router)
router.include_router(memory_config_router)
router.include_router(memory_read_router)
router.include_router(memory_config_community_router)
router.include_router(display_router)
router.include_router(emotion_reply_router)
router.include_router(perceptual_router)
router.include_router(preference_recall_router)
router.include_router(reflection_router)
router.include_router(implicit_memory_router)
router.include_router(working_memory_router)
router.include_router(write_router)
router.include_router(explicit_memory_router)
router.include_router(files_router)
router.include_router(episodic_memory_router)
router.include_router(emotion_analytics_router)
router.include_router(forgetting_router)
router.include_router(reflection_logs_router)
router.include_router(short_router)
router.include_router(value_ranking_router)
router.include_router(workspace_router)

__all__ = ["router"]