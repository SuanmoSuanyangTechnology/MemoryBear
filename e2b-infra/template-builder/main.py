"""
Template Builder Service

Receives Dockerfile content and builds Docker images that serve as
sandbox templates. In Phase 2, this would convert Docker images to
Firecracker rootfs snapshots.
"""
from fastapi import FastAPI

app = FastAPI(title="E2B Template Builder", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "template-builder"}
