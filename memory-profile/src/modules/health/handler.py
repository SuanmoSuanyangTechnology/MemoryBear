"""Health probe handlers."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from src.runtime import ProfileRuntime


class HealthHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime

    @staticmethod
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    async def ready(self) -> JSONResponse:
        components = await self.runtime.readiness()
        healthy = all(state == "up" for state in components.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ready" if healthy else "not_ready",
                "components": components,
            },
        )
