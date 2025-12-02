"""Base classes for LLM response models with common validators.

This module provides reusable base classes for Pydantic models that handle
common LLM response patterns and edge cases.

Classes:
    RobustLLMResponse: Base class for LLM response models with robust validation
"""

from typing import Any
from pydantic import BaseModel, ConfigDict, model_validator


class RobustLLMResponse(BaseModel):
    """Base class for LLM response models with robust validation.

    This base class provides:
    - Automatic handling of list-wrapped responses (e.g., [{"field": "value"}])
    - Ignoring extra fields from LLM output
    - Validation on assignment

    Usage:
        class MyResponse(RobustLLMResponse):
            field1: str
            field2: int
    """

    model_config = ConfigDict(
        extra="ignore",  # Allow extra fields to be ignored (more forgiving)
        validate_assignment=True  # Validate on assignment
    )

    @model_validator(mode='before')
    @classmethod
    def handle_list_input(cls, data: Any) -> Any:
        """Handle cases where LLM returns a list instead of a dict.

        Some LLMs may wrap the response in a list like [{"field": "value"}].
        This validator extracts the first item if that happens.

        Args:
            data: The input data from the LLM

        Returns:
            The unwrapped data (dict)

        Raises:
            ValueError: If the input is invalid (empty list, wrong type, etc.)
        """
        if isinstance(data, list):
            if len(data) == 0:
                raise ValueError("Received empty list from LLM")
            # Extract first item from list
            data = data[0]

        if not isinstance(data, dict):
            raise ValueError(f"Expected dict or list, got {type(data).__name__}")

        return data
