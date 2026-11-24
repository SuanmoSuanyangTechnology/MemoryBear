"""Internal route for the migrated ``POST /memory/files/upload`` endpoint.

The host mounts the route at ``/api/memory/files/upload`` (JWT face) and
``/v1/memory/files/upload`` (API-key face); both map onto this internal path,
which is why one route serves both faces (the principal carries the face).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.files.handler import FileHandler

router = APIRouter(prefix="/memory/files", tags=["Memory files"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> FileHandler:
    return FileHandler(request.app.state.runtime)


@router.post("/upload", response_model=None)
async def upload_memory_file(
        request: Request,
        context: Context,
        file: Annotated[UploadFile, File()],
) -> JSONResponse:
    return await _handler(request).upload(request, context, file)