"""Outbound integrations to other split services (east-west direct channels)."""

from .model_invoke import ModelInvokeClient, ModelInvokeError

__all__ = ["ModelInvokeClient", "ModelInvokeError"]
