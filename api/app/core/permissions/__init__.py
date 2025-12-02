"""
Permission management module.

This module provides a unified permission service for managing access control
across the application.
"""

from app.core.permissions.models import Action, ResourceType, Resource, Subject
from app.core.permissions.service import permission_service

__all__ = [
    "Action",
    "ResourceType",
    "Resource",
    "Subject",
    "permission_service",
]
