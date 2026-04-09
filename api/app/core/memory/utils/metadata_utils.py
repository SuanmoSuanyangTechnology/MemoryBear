"""
Metadata utility functions for cleaning and validating user metadata.
"""

import logging
from typing import Optional

from app.core.memory.models.metadata_models import UserMetadata

logger = logging.getLogger(__name__)


def clean_metadata(raw: dict) -> dict:
    """
    Clean metadata by removing empty string values and empty array fields recursively.
    Only keeps fields with actual content. If a nested dict becomes empty after cleaning,
    it is removed too.
    """
    cleaned = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            nested = clean_metadata(value)
            if nested:
                cleaned[key] = nested
        elif isinstance(value, list):
            if len(value) > 0:
                cleaned[key] = value
        elif isinstance(value, str):
            if value != "":
                cleaned[key] = value
        else:
            cleaned[key] = value
    return cleaned


def validate_metadata(raw: dict) -> Optional[UserMetadata]:
    """
    Validate metadata structure using the Pydantic UserMetadata model.
    Returns None and logs a WARNING on validation failure.
    """
    try:
        return UserMetadata.model_validate(raw)
    except Exception as e:
        logger.warning("Metadata validation failed: %s", e)
        return None
