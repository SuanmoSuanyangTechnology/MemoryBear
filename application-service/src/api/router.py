"""Internal API router composition."""

from fastapi import APIRouter

from .routes.annotations import router as annotations_router
from .routes.annotations import v1_router as annotations_v1_router
from .routes.app_logs import router as app_logs_router
from .routes.apps import router as apps_router
from .routes.conversation_shares import router as conversation_shares_router
from .routes.draft_run import router as draft_run_router
from .routes.end_users import router as end_users_router
from .routes.health import router as health_router
from .routes.human_intervention import router as human_intervention_router
from .routes.human_intervention import v1_router as human_intervention_v1_router
from .routes.messages import router as messages_router
from .routes.messages import v1_router as messages_v1_router
from .routes.multi_agent import router as multi_agent_router
from .routes.prompt_optimizer import router as prompt_optimizer_router
from .routes.public_share import router as public_share_router
from .routes.release_shares import router as release_shares_router
from .routes.skills import router as skills_router
from .routes.tools import router as tools_router

internal_v1_router = APIRouter(prefix="/internal/v1")
internal_v1_router.include_router(health_router)
internal_v1_router.include_router(apps_router)
internal_v1_router.include_router(annotations_router)
internal_v1_router.include_router(annotations_v1_router)
internal_v1_router.include_router(app_logs_router)
internal_v1_router.include_router(conversation_shares_router)
internal_v1_router.include_router(draft_run_router)
internal_v1_router.include_router(end_users_router)
internal_v1_router.include_router(human_intervention_router)
internal_v1_router.include_router(human_intervention_v1_router)
internal_v1_router.include_router(messages_router)
internal_v1_router.include_router(messages_v1_router)
internal_v1_router.include_router(multi_agent_router)
internal_v1_router.include_router(prompt_optimizer_router)
internal_v1_router.include_router(public_share_router)
internal_v1_router.include_router(release_shares_router)
internal_v1_router.include_router(skills_router)
internal_v1_router.include_router(tools_router)

__all__ = ["internal_v1_router"]
