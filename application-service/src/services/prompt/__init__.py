"""Prompt templates for the prompt-optimizer service."""

from pathlib import Path

from ...utils.prompt_manager import PromptManager

prompt_manager = PromptManager(Path(__file__).parent)

__all__ = ["prompt_manager"]
