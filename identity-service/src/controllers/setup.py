"""Setup routes (public bootstrap, host-compatible)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.response_utils import success
from src.db import get_db
from src.schemas.response_schema import ApiResponse
from src.services import user_service

router = APIRouter(prefix="/setup", tags=["Setup"])


@router.post("", response_model=ApiResponse)
def setup_initial_user(db: Session = Depends(get_db)):
    """Create the initial superuser. This can only be run once.

    Reads credentials from environment variables.
    """

    user = user_service.create_initial_superuser(db)
    if not user:
        return success(msg="Superuser already exists.")
    return success(msg="Superuser created successfully.")
