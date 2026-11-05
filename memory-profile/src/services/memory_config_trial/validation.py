"""Model-slot liveness validation for M-R2 / M-W2."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from src.i18n import t
from src.infrastructure.postgres import PostgresManager
from src.repositories.memory_config import MemoryConfigReadRepository
from src.repositories.model_config import ModelConfigRepository
from src.services.memory_config.errors import ConfigNotFoundError

logger = logging.getLogger(__name__)

#: Workspace slot -> model-service probe type. Media slots are probed as LLM.
_MODEL_SLOTS: tuple[tuple[str, str], ...] = (
    ("llm", "llm"),
    ("embedding", "embedding"),
    ("rerank", "rerank"),
    ("vision", "llm"),
    ("video", "llm"),
    ("audio", "llm"),
)


class ValidConfigService:
    def __init__(self, postgres: PostgresManager, model_service: Any) -> None:
        self.repository = MemoryConfigReadRepository(postgres)
        self.models = ModelConfigRepository(postgres)
        self.model_service = model_service

    async def valid_config(self, config_id: UUID, *, locale: str = "zh") -> dict[str, Any]:
        row = await self.repository.get_with_workspace(config_id)
        if row is None:
            raise ConfigNotFoundError(str(config_id))

        tenant_id = row["tenant_id"]
        tenant_uuid = UUID(str(tenant_id)) if tenant_id else None

        warnings: list[dict[str, Any]] = []
        configured: list[tuple[str, str, str]] = []
        for slot, probe_type in _MODEL_SLOTS:
            model_id = row.get(slot)
            if not model_id:
                warnings.append(
                    {
                        "model_type": slot,
                        "model_id": None,
                        "source": "extracted",
                        "message": t(
                            "memory_config.model.not_configured",
                            locale,
                            model_type=slot,
                        ),
                    }
                )
            else:
                configured.append((slot, str(model_id), probe_type))

        # Parse every configured id first, then load all of them in one query:
        # the six slots usually share two or three distinct model ids.
        parsed: list[tuple[str, str, str, UUID]] = []
        for slot, model_id, probe_type in configured:
            try:
                parsed.append((slot, model_id, probe_type, UUID(model_id)))
            except (TypeError, ValueError, AttributeError) as exc:
                warnings.append(
                    {
                        "model_type": slot,
                        "model_id": model_id,
                        "source": "extracted",
                        "message": t(
                            "memory_config.model.not_found_with_error",
                            locale,
                            model_type=slot,
                            model_id=model_id,
                            error=str(exc),
                        ),
                    }
                )

        models = await self.models.get_models([item[3] for item in parsed], tenant_id)

        resolved: list[tuple[str, str, str, UUID, str, UUID]] = []
        for slot, model_id, probe_type, model_uuid in parsed:
            model = models.get(model_uuid)
            if model is None:
                warnings.append(
                    {
                        "model_type": slot,
                        "model_id": model_id,
                        "source": "extracted",
                        "message": t(
                            "memory_config.model.not_found_with_error",
                            locale,
                            model_type=slot,
                            model_id=model_id,
                            error="not found",
                        ),
                    }
                )
                continue
            if not model["is_active"]:
                warnings.append(
                    {
                        "model_type": slot,
                        "model_id": model_id,
                        "source": "extracted",
                        "message": t(
                            "memory_config.model.no_api_key",
                            locale,
                            model_type=slot,
                            model_name=model["name"],
                        ),
                    }
                )
                continue
            resolved.append((slot, model_id, model["name"], model_uuid, probe_type, tenant_uuid))

        async def _probe(
            slot: str,
            model_id: str,
            name: str,
            model_uuid: UUID,
            probe_type: str,
            tenant_uuid: UUID,
        ):
            outcome = await self.model_service.probe_stored_config(
                model_uuid, tenant_uuid, probe_type
            )
            if outcome.kind == "valid":
                return None
            key = (
                "memory_config.model.no_api_key"
                if outcome.kind == "no_credential"
                else "memory_config.model.api_verify_failed"
            )
            return {
                "model_type": slot,
                "model_id": model_id,
                "source": "extracted",
                "message": t(
                    key,
                    locale,
                    model_type=slot,
                    model_name=name,
                    error=outcome.detail or outcome.kind,
                ),
            }

        if resolved:
            results = await asyncio.gather(*(_probe(*item) for item in resolved))
            warnings.extend(item for item in results if item is not None)

        result: dict[str, Any] = {
            "valid": not warnings,
            "config_id": str(config_id),
            "config_name": row["config_name"],
        }
        if warnings:
            result["warnings"] = warnings
        return result
