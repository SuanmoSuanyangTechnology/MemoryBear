"""App config conversion helpers; mirrors the host ``app_config_utils``
subset used by the release snapshot builder."""

from __future__ import annotations

from typing import Any


def model_parameters_to_dict(model_parameters: Any) -> dict[str, Any] | None:
    """Convert a ModelParameters object to a dictionary.

    Args:
        model_parameters: ModelParameters object, dict, or None

    Returns:
        Model parameters as a dictionary; None if the input is None
    """
    if model_parameters is None:
        return None

    if isinstance(model_parameters, dict):
        return model_parameters

    # Pydantic v2
    if hasattr(model_parameters, 'model_dump'):
        return model_parameters.model_dump()

    # Pydantic v1
    if hasattr(model_parameters, 'dict'):
        return model_parameters.dict()

    # Fall back to a generic conversion for other cases
    try:
        return dict(model_parameters)
    except (TypeError, ValueError):
        return None


__all__ = ["model_parameters_to_dict"]
