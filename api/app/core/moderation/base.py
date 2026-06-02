from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ModerationAction(StrEnum):
    DIRECT_OUTPUT = "direct_output"
    OVERRIDDEN = "overridden"


class ModerationInputsResult(BaseModel):
    flagged: bool = False
    action: ModerationAction = ModerationAction.DIRECT_OUTPUT
    preset_response: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    query: str = ""


class ModerationOutputsResult(BaseModel):
    flagged: bool = False
    action: ModerationAction = ModerationAction.DIRECT_OUTPUT
    preset_response: str = ""
    text: str = ""


class ModerationError(Exception):
    pass


class ModerationBase(ABC):
    def __init__(self, app_id: str, tenant_id: str, config: dict[str, Any] | None = None):
        self.app_id = app_id
        self.tenant_id = tenant_id
        self.config = config or {}

    @classmethod
    @abstractmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def moderation_for_inputs(self, inputs: dict[str, Any], query: str = "") -> ModerationInputsResult:
        raise NotImplementedError

    @abstractmethod
    def moderation_for_outputs(self, text: str) -> ModerationOutputsResult:
        raise NotImplementedError

    @classmethod
    def _validate_inputs_outputs_config(cls, config: dict[str, Any], is_preset_response_required: bool) -> None:
        inputs_config = config.get("inputs_config")
        outputs_config = config.get("outputs_config")

        if not isinstance(inputs_config, dict):
            raise ValueError("inputs_config must be a dict")
        if not isinstance(outputs_config, dict):
            raise ValueError("outputs_config must be a dict")

        inputs_enabled = inputs_config.get("enabled")
        outputs_enabled = outputs_config.get("enabled")

        if not inputs_enabled and not outputs_enabled:
            raise ValueError("At least one of inputs_config or outputs_config must be enabled")

        if not is_preset_response_required:
            return

        if inputs_enabled and not inputs_config.get("preset_response"):
            raise ValueError("inputs_config preset_response is required when enabled")
        if outputs_enabled and not outputs_config.get("preset_response"):
            raise ValueError("outputs_config preset_response is required when enabled")
