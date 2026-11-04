"""Internal API router composition."""

from fastapi import APIRouter

from .routes.annotations import router as annotations_router
from .routes.annotations import v1_router as annotations_v1_router
from .routes.app_logs import router as app_logs_router
from .routes.apps import router as apps_router
from .routes.assets import router as assets_router
from .routes.conversation_shares import router as conversation_shares_router
from .routes.draft_run import router as draft_run_router
from .routes.end_users import router as end_users_router
from .routes.health import router as health_router
from .routes.human_intervention import router as human_intervention_router
from .routes.human_intervention import v1_router as human_intervention_v1_router
from .routes.messages import router as messages_router
from .routes.messages import v1_router as messages_v1_router
from .routes.metrics import router as metrics_router
from .routes.multi_agent import router as multi_agent_router
from .routes.prompt_optimizer import router as prompt_optimizer_router
from .routes.public_share import router as public_share_router
from .routes.release_shares import router as release_shares_router
from .routes.skills import router as skills_router
from .routes.tools import router as tools_router

from ..plugins import get_registered_routers

internal_v1_router = APIRouter(prefix="/internal/v1")
internal_v1_router.include_router(health_router)
# `/apps/assets/*` must be registered before `apps_router`, otherwise
# `/apps/{app_id}` matches "assets" first and the catalog endpoints 404/422.
internal_v1_router.include_router(assets_router)
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
internal_v1_router.include_router(metrics_router)
internal_v1_router.include_router(multi_agent_router)
internal_v1_router.include_router(prompt_optimizer_router)
internal_v1_router.include_router(public_share_router)
internal_v1_router.include_router(release_shares_router)
internal_v1_router.include_router(skills_router)
internal_v1_router.include_router(tools_router)

# Routers already attached by `mount_registered_routers` (idempotency guard).
# APIRouter is not hashable, so remember identity rather than the object.
_mounted: set[int] = set()


def mount_registered_routers() -> None:
    """Attach enterprise-registered routers to the internal API.

    The community market's write entry (`/external-catalog/*`) and whole-package
    approve orchestration live in the private enterprise-extensions package, not
    in this repository. They reach the process through ``plugins.register_router``
    and are mounted here; the open-source build registers nothing, so those
    routes do not exist in the community edition.

    Must run before ``create_app`` includes ``internal_v1_router``. Idempotent:
    each registered router is mounted at most once per process, so repeated
    ``create_app`` calls (tests) do not duplicate routes.
    """

    for entry in get_registered_routers():
        router = entry["router"]
        if id(router) in _mounted:
            continue
        internal_v1_router.include_router(
            router,
            prefix=entry["prefix"],
            tags=entry["tags"] or None,
        )
        _mounted.add(id(router))


__all__ = ["internal_v1_router", "mount_registered_routers"]
