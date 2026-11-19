"""Parse shared task identity once at the inbound job boundary."""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from src.application.jobs.payload import required_text
from src.application.ports.task_context import TaskScope
from src.domain.shared.ids import parse_uuid


def uuid_field(payload: Mapping[str, Any], key: str) -> UUID:
    value = parse_uuid(required_text(payload, key))
    if value is None:
        raise ValueError(f"invalid {key}")
    return value


def task_scope(payload: Mapping[str, Any]) -> TaskScope:
    config = payload.get("config_id")
    if config is not None:
        if not isinstance(config, (str, int)) or isinstance(config, bool):
            raise ValueError("invalid config_id")
        config = str(config)
        if parse_uuid(config) is None and not config.isdecimal():
            raise ValueError("invalid config_id")
    return TaskScope(
        workspace_id=uuid_field(payload, "workspace_id"),
        tenant_id=uuid_field(payload, "tenant_id"),
        end_user_id=uuid_field(payload, "end_user_id"),
        actor_id=uuid_field(payload, "actor_id"),
        config_id=config,
    )
