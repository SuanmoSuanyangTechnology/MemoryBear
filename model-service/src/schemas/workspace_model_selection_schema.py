"""Workspace model-selection validation request schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceModelSelectionRequest(BaseModel):
    """Host slot selection plus the validation phase to run.

    ``selection`` is the host's slot -> model_config_id map verbatim (missing
    or null means "not configured"). ``static_check`` runs only the static
    gate; otherwise each slot in ``probe_slots`` is live-probed.
    """

    selection: dict[str, str | None] = Field(default_factory=dict)
    static_check: bool = False
    probe_slots: list[str] = Field(default_factory=list)
