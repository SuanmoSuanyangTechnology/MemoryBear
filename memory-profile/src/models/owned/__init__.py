"""Explicit registry for memory-profile-owned ORM models.

Import each owned model module here so Alembic registers it in ProfileBase.metadata.
Never import host-owned reference models into this package.
"""

from src.models.base import ProfileBase
from src.models.owned.ai_personality_model import AiPersonality

__all__ = ["AiPersonality", "ProfileBase"]
