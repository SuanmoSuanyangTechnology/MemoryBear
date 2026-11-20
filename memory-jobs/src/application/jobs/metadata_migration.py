"""Celery handlers for metadata migration scan and per-user units."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.application.dto import MetadataMigrationResult, MetadataMigrationScanReport
from src.application.jobs.payload import required_text
from src.application.metadata_migration import (
    MetadataMigrationCommand,
    MetadataMigrationUseCase,
    ScanMetadataMigrationUseCase,
)
from src.application.ports.jobs import JobSpec, ResourceClass


class ScanMetadataMigrationJob:
    spec = JobSpec(resource_class=ResourceClass.BULK, acks_late=False)

    def __init__(self, use_case: ScanMetadataMigrationUseCase) -> None:
        self._use_case = use_case

    def handle(
        self, _payload: Mapping[str, Any], *, attempt_token: str
    ) -> MetadataMigrationScanReport:
        return self._use_case.execute()


class DoMetadataMigrationJob:
    spec = JobSpec(resource_class=ResourceClass.LLM_BOUND, retryable=True)

    def __init__(self, use_case: MetadataMigrationUseCase) -> None:
        self._use_case = use_case

    def handle(self, payload: Mapping[str, Any], *, attempt_token: str) -> MetadataMigrationResult:
        language = payload.get("language", "zh")
        return self._use_case.execute(
            MetadataMigrationCommand(
                end_user_id=required_text(payload, "end_user_id"),
                workspace_id=required_text(payload, "workspace_id"),
                language=language if isinstance(language, str) else "zh",
                attempt_token=attempt_token,
            )
        )
