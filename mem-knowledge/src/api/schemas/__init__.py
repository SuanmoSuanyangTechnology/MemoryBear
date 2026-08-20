"""Internal API schemas."""

from .common import SuccessEnvelope
from .health import ComponentHealth, HealthResponse

__all__ = ["ComponentHealth", "HealthResponse", "SuccessEnvelope"]
