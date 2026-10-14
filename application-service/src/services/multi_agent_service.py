"""Multi-agent config management service.

Covers the definition-plane read and update paths of the monolith's
``MultiAgentService`` consumed by the multi-agent config endpoints; the run /
stream / delete paths remain in the monolith.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..errors import ResourceNotFoundException
from ..models import App, AppRelease
from ..models.multi_agent_model import MultiAgentConfig
from ..repositories import cross_domain_read
from ..repositories.multi_agent_repository import MultiAgentConfigRepository
from ..schemas.multi_agent_schema import MultiAgentConfigUpdate
from .model_ref_gate import raise_model_unavailable, resolve_invoke_ref
from .multi_agent_release_resolver import describe_release_state, resolve_release_for_save

logger = logging.getLogger(__name__)


def convert_uuids_to_str(obj: Any) -> Any:
    """Recursively convert all UUIDs in an object to strings.

    Args:
        obj: Object to convert (dict, list, UUID, etc.)

    Returns:
        The converted object
    """
    if isinstance(obj, uuid.UUID):
        return str(obj)
    elif isinstance(obj, str):
        # PostgreSQL text/jsonb forbids NUL (U+0000). Node output (e.g. the raw
        # chunk text returned by knowledge retrieval) may contain this character;
        # storing it as-is raises UntranslatableCharacterError (22P05 unsupported
        # Unicode escape sequence). This function sanitizes values before write.
        return obj.replace("\x00", "") if "\x00" in obj else obj
    elif isinstance(obj, dict):
        return {k: convert_uuids_to_str(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_uuids_to_str(item) for item in obj]
    else:
        return obj


class MultiAgentService:
    """Multi-agent config management service."""

    def __init__(self, db: Session):
        self.db = db

    def get_config(self, app_id: uuid.UUID) -> MultiAgentConfig | None:
        """Get the multi-agent config.

        Args:
            app_id: App ID

        Returns:
            The multi-agent config, or None if it does not exist
        """
        return MultiAgentConfigRepository(self.db).get_by_app_id(app_id)

    def get_multi_agent_configs(self, app_id: uuid.UUID) -> dict[str, Any] | None:
        """Get the latest effective multi-agent config by app_id, converting
        agent_id to app_id.

        Args:
            app_id: App ID

        Returns:
            The converted config dict, or None if it does not exist
        """
        config = self.get_config(app_id)
        if not config:
            return None

        # Compatibility code
        if not config.default_model_config_id:
            master_release = self.db.get(AppRelease, config.master_agent_id)
            config.default_model_config_id = (
                master_release.default_model_config_id if master_release else None
            )

        # Convert sub_agents agent_id (release_id) to app_id and expose
        # release-policy info: release_policy / release_id (pinned only) /
        # current_release_id / has_newer_release
        converted_sub_agents = []
        for sub_agent in config.sub_agents:
            sub_agent_copy = sub_agent.copy()
            try:
                state = describe_release_state(self.db, sub_agent)
                if state["app_id"]:
                    sub_agent_copy["agent_id"] = state["app_id"]
                sub_agent_copy["release_policy"] = state["release_policy"]
                sub_agent_copy["release_id"] = state["release_id"]
                sub_agent_copy["current_release_id"] = state["current_release_id"]
                sub_agent_copy["has_newer_release"] = state["has_newer_release"]
            except Exception as e:
                logger.warning(
                    f"转换 sub_agent agent_id 失败: {sub_agent.get('agent_id')}, 错误: {str(e)}"
                )
            converted_sub_agents.append(sub_agent_copy)

        # Build the returned config dict
        return {
            "id": config.id,
            "app_id": config.app_id,
            "default_model_config_id": config.default_model_config_id,
            "model_parameters": config.model_parameters,
            "orchestration_mode": config.orchestration_mode,
            "sub_agents": converted_sub_agents,
            "routing_rules": config.routing_rules,
            "execution_config": config.execution_config,
            # Supervisor-as-agent: the read-back must include this key, otherwise
            # the frontend getData cannot see it, and default backfill resets the
            # supervisor capability surface to all defaults (the toggle
            # "turns off after save")
            "supervisor_config": config.supervisor_config,
            "aggregation_strategy": config.aggregation_strategy,
            "is_active": config.is_active,
            "created_at": config.created_at,
            "updated_at": config.updated_at
        }

    def check_config_data(
        self, app_id: uuid.UUID, data: MultiAgentConfigUpdate
    ) -> MultiAgentConfig:
        # 1. Validate the app exists
        app = self.db.get(App, app_id)
        if not app:
            raise ResourceNotFoundException("应用", str(app_id))

        # 2. Validate the model config (when provided)
        if data.default_model_config_id:
            tenant_id = cross_domain_read.get_workspace_tenant_id(self.db, app.workspace_id)
            invoke_ref = resolve_invoke_ref(
                self.db,
                data.default_model_config_id,
                tenant_id=tenant_id,
            )
            if invoke_ref is None:
                raise_model_unavailable(
                    self.db,
                    data.default_model_config_id,
                    tenant_id=tenant_id,
                )

        # 3. Validate sub-agents exist and resolve their release IDs
        # agent_id always stores the sub-agent's app ID (stable identity); the
        # release is expressed by release_policy / release_id: current=follow the
        # latest with release_id cleared; pinned=the stored, validated release_id.
        for sub_agent in (data.sub_agents or []):
            agent_app_release = resolve_release_for_save(
                self.db,
                sub_agent.agent_id,
                sub_agent.release_policy,
                sub_agent.release_id,
                label=sub_agent.name,
            )
            if sub_agent.release_policy == "pinned":
                sub_agent.release_id = agent_app_release.id
            else:
                sub_agent.release_id = None

        # 5. Build the config (convert UUIDs to strings for JSON serialization)
        # sub_agents=None means "keep existing" on the update branch; the create
        # branch fills the NOT NULL column with an empty list.
        sub_agents_data = (
            None
            if data.sub_agents is None
            else [convert_uuids_to_str(sub_agent.model_dump()) for sub_agent in data.sub_agents]
        )
        # routing_rules_data = [
        #     convert_uuids_to_str(rule.model_dump()) for rule in data.routing_rules
        # ] if data.routing_rules else None

        # Handle execution_config (may be None, a dict, or a Pydantic model)
        if data.execution_config is None:
            execution_config_data = {}
        elif isinstance(data.execution_config, dict):
            execution_config_data = convert_uuids_to_str(data.execution_config)
        else:
            execution_config_data = convert_uuids_to_str(data.execution_config.model_dump())

        # Handle supervisor_config (supervisor-as-agent; None=bare supervisor, column stays NULL)
        if getattr(data, "supervisor_config", None) is None:
            supervisor_config_data = None
        elif isinstance(data.supervisor_config, dict):
            supervisor_config_data = convert_uuids_to_str(data.supervisor_config)
        else:
            supervisor_config_data = convert_uuids_to_str(data.supervisor_config.model_dump())

        # Handle model_parameters (may be None, a dict, or a Pydantic model)
        if data.model_parameters is None:
            model_parameters_data = None
        else:
            model_parameters_data = data.model_parameters

        config = MultiAgentConfig(
            app_id=app_id,
            master_agent_id=data.master_agent_id,
            master_agent_name=data.master_agent_name,
            default_model_config_id=data.default_model_config_id,
            model_parameters=model_parameters_data,
            orchestration_mode=data.orchestration_mode,
            sub_agents=sub_agents_data,
            # routing_rules=routing_rules_data,
            execution_config=execution_config_data,
            supervisor_config=supervisor_config_data,
            aggregation_strategy=data.aggregation_strategy
        )
        return config

    def update_config(
        self,
        app_id: uuid.UUID,
        data: MultiAgentConfigUpdate
    ) -> MultiAgentConfig:
        """Update the multi-agent config.

        Args:
            app_id: App ID
            data: Update payload

        Returns:
            The updated config
        """
        config = self.get_config(app_id)
        new_config = self.check_config_data(app_id, data)
        if not config:
            config = new_config
            # "Keep original" has no meaning on the create branch; the NOT NULL
            # sub_agents column defaults to an empty list, mirroring the GET
            # default template.
            if config.sub_agents is None:
                config.sub_agents = []
            self.db.add(config)
            self.db.commit()
            self.db.refresh(config)
            logger.info(
                "创建多 Agent 配置成功",
                extra={
                    "config_id": str(config.id),
                    "app_id": str(app_id),
                    "mode": data.orchestration_mode,
                    "sub_agent_count": len(config.sub_agents)
                }
            )
            return config

        # Fully replace the config; for DB NOT NULL columns, keep the old value
        # when the new value is None
        config.default_model_config_id = new_config.default_model_config_id
        config.model_parameters = new_config.model_parameters
        config.orchestration_mode = new_config.orchestration_mode or config.orchestration_mode
        config.sub_agents = (
            new_config.sub_agents if new_config.sub_agents is not None else config.sub_agents
        )
        config.routing_rules = new_config.routing_rules
        config.execution_config = (
            new_config.execution_config if new_config.execution_config else config.execution_config
        )
        # Supervisor-as-agent: the supervisor_config column is nullable; None=no
        # update / clear to bare supervisor are semantically the same, so assign
        # directly (unlike execution_config's "replace only when non-empty": an
        # explicitly cleared capability surface is the user's intent)
        config.supervisor_config = new_config.supervisor_config
        config.aggregation_strategy = new_config.aggregation_strategy or config.aggregation_strategy
        self.db.commit()
        self.db.refresh(config)

        logger.info(
            "更新多 Agent 配置成功",
            extra={
                "config_id": str(config.id),
                "app_id": str(app_id)
            }
        )

        return config
