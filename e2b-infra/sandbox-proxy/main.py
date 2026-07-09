"""
Sandbox Proxy Service

Routes external requests to the appropriate sandbox container.
Supports HTTP and WebSocket proxying.
"""
from fastapi import FastAPI

app = FastAPI(title="E2B Sandbox Proxy", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "sandbox-proxy"}
