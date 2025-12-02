from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.response_utils import success
from app.db import get_db
from app.schemas.response_schema import ApiResponse
from app.services import user_service

router = APIRouter(
    prefix="/setup",
    tags=["Setup"],
)

@router.post("", summary="Create the first superuser", response_model=ApiResponse)
def setup_initial_user(db: Session = Depends(get_db)):
    """
    Create the initial superuser. This can only be run once.
    Reads credentials from environment variables.
    """
    user = user_service.create_initial_superuser(db)
    if not user:
        return success(msg="Superuser already exists.")
    return success(msg="Superuser created successfully.")
