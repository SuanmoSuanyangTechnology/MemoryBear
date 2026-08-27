from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.health_service import check_required_dependencies_with_timeout


health_router = APIRouter(prefix="/health")


@health_router.get("/startup", include_in_schema=False)
async def startup_probe() -> dict[str, str]:
    return {"status": "started"}


@health_router.get("/live", include_in_schema=False)
async def liveness_probe() -> dict[str, str]:
    return {"status": "alive"}


@health_router.get("/ready", include_in_schema=False)
async def readiness_probe() -> JSONResponse:
    checks = await check_required_dependencies_with_timeout()
    ready = bool(checks) and all(status == "ok" for status in checks.values())

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "checks": checks,
        },
    )
