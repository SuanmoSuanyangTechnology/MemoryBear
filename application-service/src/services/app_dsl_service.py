"""App DSL import/export service.

Endpoint consumption paths (export shape / filename / warning strings / type
validation) mirror the host ``AppDslService``; cross-domain tables (knowledge
base / KB share / model config / tool / skill / memory config) are read via the
read-only ``cross_domain_read`` gateway.

Behavior notes:
- The overwrite-import branch also invalidates the shared cache
  (``delete_workflow_config``);
- Management-plane sync: ``import_dsl`` and internal methods take
  ``redis_client`` explicitly.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Collection
from typing import Any

import yaml
from sqlalchemy.orm import Session

from ..cache.workflow_cache import delete_workflow_config
from ..config import current_settings
from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..models.agent_app_config_model import AgentConfig
from ..models.app_model import App, AppType
from ..models.app_release_model import AppRelease
from ..models.appshare_model import AppShare
from ..models.multi_agent_model import MultiAgentConfig
from ..models.workflow_model import WorkflowConfig
from ..repositories.cross_domain_read import (
    find_importable_model_id_by_name,
    find_skill_id_by_name,
    find_tool_config_id_by_name,
    get_importable_model_id,
    get_importable_skill_id,
    get_importable_tool_id,
    get_knowledge_row,
    get_knowledge_share_row,
    get_model_config_row,
    get_skill_row,
    get_tool_config_row,
    get_workspace_published_knowledge_row,
    memory_config_exists_by_legacy_id,
    memory_config_exists_by_uuid,
)
from ..schemas.model_enums import LLM_FAMILY_TYPES, ModelType
from ..utils.datetime_utils import to_iso_z, utcnow_naive
from ..utils.workflow_node_types import NodeType
from ..workflow.adapters.memory_bear.memory_bear_adapter import MemoryBearAdapter
from .workflow_service import WorkflowService


class AppDslService:

    def __init__(self, db: Session):
        self.db = db

    # ==================== Export ====================

    def export_dsl(self, app_id: uuid.UUID, release_id: uuid.UUID | None = None) -> tuple[str, str]:
        """Build the app DSL yaml string; returns (yaml_str, filename)."""
        app = self.db.query(App).filter(App.id == app_id, App.is_active.is_(True)).first()
        if not app:
            raise ResourceNotFoundException("应用", str(app_id))

        meta = {
            "version": current_settings().system_version,
            "platform": "MemoryBear",
            "exported_at": to_iso_z(utcnow_naive()),
        }
        app_meta = {
            "name": app.name,
            "description": app.description,
            "icon": app.icon,
            "icon_type": app.icon_type,
            "type": app.type,
            "tags": app.tags or [],
        }

        if release_id is not None:
            return self._export_release(app, release_id, meta, app_meta)

        return self._export_draft(app, meta, app_meta)

    def _export_release(
        self, app: App, release_id: uuid.UUID, meta: dict, app_meta: dict
    ) -> tuple[str, str]:
        release = self.db.query(AppRelease).filter(
            AppRelease.app_id == app.id,
            AppRelease.id == release_id,
            AppRelease.is_active.is_(True)
        ).first()
        if not release:
            raise ResourceNotFoundException("版本", str(release_id))

        meta["release_version"] = release.version
        meta["release_name"] = release.version_name
        app_meta["name"] = release.name
        app_meta["description"] = release.description
        config_key = {
            AppType.AGENT: "agent_config",
            AppType.MULTI_AGENT: "multi_agent_config",
            AppType.WORKFLOW: "workflow",
            AppType.PURE_WORKFLOW: "workflow"
        }.get(app.type, "config")
        config_data = self._enrich_release_config(
            app.type, release.config or {}, release.default_model_config_id
        )
        dsl = {**meta, "app": app_meta, config_key: config_data}
        return (
            yaml.dump(dsl, default_flow_style=False, allow_unicode=True),
            f"{release.name}_v{release.version_name}.yaml",
        )

    def _enrich_release_config(
        self, app_type: str, cfg: dict, default_model_config_id=None
    ) -> dict:
        if app_type == AppType.AGENT:
            enriched = {**cfg}
            enriched["default_model_config_ref"] = self._model_ref(default_model_config_id)
            if "knowledge_retrieval" in cfg:
                enriched["knowledge_retrieval"] = self._enrich_knowledge_retrieval(
                    cfg["knowledge_retrieval"]
                )
            if "tools" in cfg:
                enriched["tools"] = self._enrich_tools(cfg.get("tools"))
            if "skills" in cfg:
                enriched["skills"] = self._enrich_skills(cfg.get("skills"))
            return enriched
        if app_type == AppType.MULTI_AGENT:
            enriched = {**cfg}
            enriched["default_model_config_ref"] = self._model_ref(default_model_config_id)
            if "master_agent_id" in cfg:
                enriched["master_agent_ref"] = self._release_ref(cfg["master_agent_id"])
            if "sub_agents" in cfg:
                enriched["sub_agents"] = self._enrich_sub_agents(cfg["sub_agents"])
            if "routing_rules" in cfg:
                enriched["routing_rules"] = [
                    {**r, "_ref": self._agent_ref(r.get("target_agent_id"))}
                    for r in (cfg["routing_rules"] or [])
                ]
            if cfg.get("supervisor_config"):
                enriched["supervisor_config"] = self._enrich_supervisor_config(
                    cfg["supervisor_config"]
                )
            return enriched
        if app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            enriched = {**cfg}
            if "nodes" in cfg:
                enriched["nodes"] = self._enrich_workflow_nodes(cfg["nodes"])
            if "environment_variables" in cfg:
                enriched["environment_variables"] = self._mask_environment_variables(
                    cfg["environment_variables"]
                )
            return enriched
        return cfg

    def _export_draft(self, app: App, meta: dict, app_meta: dict) -> tuple[str, str]:
        if app.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            config = self.db.query(WorkflowConfig).filter(WorkflowConfig.app_id == app.id).first()
            config_data = {
                "variables": config.variables if config else [],
                "environment_variables": (
                    self._mask_environment_variables(config.environment_variables) if config else []
                ),
                "edges": config.edges if config else [],
                "nodes": self._enrich_workflow_nodes(config.nodes) if config else [],
                "features": config.features if config else {},
                "execution_config": config.execution_config if config else {},
                "triggers": config.triggers if config else [],
                "workflow_type": config.workflow_type if config else AppType.WORKFLOW,
            } if config else {}
            dsl = {**meta, "app": app_meta, "workflow": config_data}

        elif app.type == AppType.AGENT:
            config = self.db.query(AgentConfig).filter(AgentConfig.app_id == app.id).first()
            config_data = {
                "system_prompt": config.system_prompt if config else None,
                "model_parameters": self._to_dict(config.model_parameters) if config else None,
                "default_model_config_ref": (
                    self._model_ref(config.default_model_config_id) if config else None
                ),
                "knowledge_retrieval": (
                    self._enrich_knowledge_retrieval(config.knowledge_retrieval)
                    if config
                    else None
                ),
                "memory": config.memory if config else None,
                "variables": config.variables if config else [],
                "tools": self._enrich_tools(config.tools) if config else [],
                "skills": self._enrich_skills(config.skills) if config else {},
                "features": config.features if config else {}
            } if config else {}
            dsl = {**meta, "app": app_meta, "agent_config": config_data}

        elif app.type == AppType.MULTI_AGENT:
            config = self.db.query(MultiAgentConfig).filter(
                MultiAgentConfig.app_id == app.id
            ).first()
            config_data = {
                "orchestration_mode": config.orchestration_mode if config else None,
                "master_agent_name": config.master_agent_name if config else None,
                "model_parameters": self._to_dict(config.model_parameters) if config else None,
                "default_model_config_ref": (
                    self._model_ref(config.default_model_config_id) if config else None
                ),
                "master_agent_ref": (
                    self._release_ref(config.master_agent_id) if config else None
                ),
                "sub_agents": self._enrich_sub_agents(config.sub_agents) if config else [],
                "routing_rules": [
                    {**r, "_ref": self._agent_ref(r.get("target_agent_id"))}
                    for r in (config.routing_rules or [])
                ] if config else [],

                "execution_config": config.execution_config if config else {},
                "supervisor_config": (
                    self._enrich_supervisor_config(self._to_dict(config.supervisor_config))
                    if config
                    else None
                ),
                "aggregation_strategy": config.aggregation_strategy if config else "merge",
            } if config else {}
            dsl = {**meta, "app": app_meta, "multi_agent_config": config_data}

        else:
            raise BusinessException(f"不支持的应用类型: {app.type}", BizCode.BAD_REQUEST)

        return yaml.dump(dsl, default_flow_style=False, allow_unicode=True), f"{app.name}.yaml"

    def _to_dict(self, value):
        """Convert a Pydantic object to a plain dict for safe yaml.dump serialization."""
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return value

    def _model_ref(self, model_config_id) -> dict | None:
        if not model_config_id:
            return None
        m = get_model_config_row(self.db, model_config_id)
        if m:
            return {
                "id": str(model_config_id),
                "name": m.name,
                "provider": m.provider,
                "type": m.type,
            }
        return {"id": str(model_config_id)}

    def _kb_ref(self, kb_id) -> dict | None:
        if not kb_id:
            return None
        kb = get_knowledge_row(self.db, kb_id)
        return {"id": str(kb_id), "name": kb.name} if kb else {"id": str(kb_id)}

    def _tool_ref(self, tool_id) -> dict | None:
        if not tool_id:
            return None
        t = get_tool_config_row(self.db, tool_id)
        if t:
            return {"id": str(tool_id), "name": t.name, "tool_type": t.tool_type}
        return {"id": str(tool_id)}

    def _enrich_knowledge_retrieval(self, kr: dict | None) -> dict | None:
        if not kr:
            return kr
        kbs = [
            {**kb, "_ref": self._kb_ref(kb.get("kb_id"))}
            for kb in kr.get("knowledge_bases", [])
        ]
        enriched = {**kr, "knowledge_bases": kbs}
        reranker_id = enriched.get("reranker_id")
        if reranker_id:
            enriched["reranker_ref"] = self._model_ref(reranker_id)
            enriched.pop("reranker_id", None)
        return enriched

    def _enrich_tools(self, tools: list) -> list:
        return [{**t, "_ref": self._tool_ref(t.get("tool_id"))} for t in (tools or [])]

    def _enrich_workflow_nodes(self, nodes: list) -> list:
        """Enrich model references in workflow nodes with name/provider/type info."""
        enriched_nodes = []
        for node in (nodes or []):
            node_type = node.get("type")
            config = dict(node.get("config") or {})

            if node_type in (
                NodeType.LLM.value,
                NodeType.QUESTION_CLASSIFIER.value,
                NodeType.PARAMETER_EXTRACTOR.value,
            ):
                model_id = config.get("model_id")
                if model_id:
                    config["model_ref"] = self._model_ref(model_id)
                    del config["model_id"]
            elif node_type == NodeType.AGENT.value:
                model = dict(config.get("model") or {})
                model_id = model.get("model_id")
                if model_id:
                    model["model_ref"] = self._model_ref(model_id)
                    model.pop("model_id", None)
                    config["model"] = model

                # Reference mode: convert the referenced Agent app_id into a name
                # reference so it can be rebound by name across environments.
                # release_id is invalid across environments; drop it and fall back
                # to `current` on import.
                if config.get("mode") == "reference":
                    reference = dict(config.get("reference") or {})
                    ref_app_id = reference.pop("app_id", None)
                    reference.pop("release_id", None)
                    if ref_app_id:
                        reference["_ref"] = self._agent_ref(ref_app_id)
                    config["reference"] = reference

                knowledge_retrieval = config.get("knowledge_retrieval")
                if knowledge_retrieval:
                    config["knowledge_retrieval"] = self._enrich_knowledge_retrieval(
                        knowledge_retrieval
                    )
                elif config.get("reranker_id"):
                    # Support the early frontend layout that flattened KB config onto the
                    # Agent node root
                    config["reranker_ref"] = self._model_ref(config["reranker_id"])
                    config.pop("reranker_id", None)
            elif node_type == NodeType.KNOWLEDGE_RETRIEVAL.value:
                reranker_id = config.get("reranker_id")
                if reranker_id:
                    config["reranker_ref"] = self._model_ref(reranker_id)
                    config.pop("reranker_id", None)
            elif node_type in (NodeType.MEMORY_READ.value, NodeType.MEMORY_WRITE.value):
                config.pop("config_id", None)

            enriched_nodes.append({**node, "config": config})
        return enriched_nodes

    @staticmethod
    def _mask_environment_variables(environment_variables: list[dict] | None) -> list[dict]:
        result = []
        for item in environment_variables or []:
            if item.get("value_type") == "secret":
                result.append({**item, "value": "__SECRET__"})
            else:
                result.append(item)
        return result

    @staticmethod
    def _clear_imported_secret_environment_variables(
        environment_variables: list[dict] | None,
    ) -> list[dict]:
        result = []
        for item in environment_variables or []:
            if item.get("value_type") == "secret":
                result.append({**item, "value": None})
            else:
                result.append(item)
        return result

    @staticmethod
    def _has_secret_environment_variables(environment_variables: list[dict] | None) -> bool:
        return any(item.get("value_type") == "secret" for item in (environment_variables or []))

    def _skill_ref(self, skill_id) -> dict | None:
        if not skill_id:
            return None
        s = get_skill_row(self.db, skill_id)
        if s:
            return {"id": str(skill_id), "name": s.name}
        return {"id": str(skill_id)}

    def _enrich_skills(self, skills: dict | None) -> dict | None:
        if not skills:
            return skills
        skill_ids = skills.get("skill_ids", [])
        enriched_ids = [
            {"id": sid, "_ref": self._skill_ref(sid)}
            for sid in (skill_ids or [])
        ]
        return {**skills, "skill_ids": enriched_ids}

    def _agent_ref(self, agent_id) -> dict | None:
        if not agent_id:
            return None
        a = self.db.query(App).filter(App.id == agent_id).first()
        if a is None:
            # Legacy sub_agents[*].agent_id stores AppRelease.id; resolve via its app
            r = self.db.query(AppRelease).filter(AppRelease.id == agent_id).first()
            if r is not None:
                a = self.db.query(App).filter(App.id == r.app_id).first()
        return {"id": str(agent_id), "name": a.name} if a else {"id": str(agent_id)}

    def _release_ref(self, release_id) -> dict | None:
        if not release_id:
            return None
        # Published cluster snapshots may store the string "None" as
        # master_agent_id (clusters without a master agent, str(None) persisted);
        # treat non-UUID values as no reference to avoid a UUID-column DataError
        try:
            release_id = (
                release_id if isinstance(release_id, uuid.UUID) else uuid.UUID(str(release_id))
            )
        except (ValueError, AttributeError, TypeError):
            return None
        r = self.db.query(AppRelease).filter(AppRelease.id == release_id).first()
        if r:
            return {
                "id": str(release_id),
                "name": r.name,
                "version": r.version,
                "app_id": str(r.app_id),
            }
        return {"id": str(release_id)}

    def _enrich_sub_agents(self, sub_agents: list) -> list:
        return [{**s, "_ref": self._agent_ref(s.get("agent_id"))} for s in (sub_agents or [])]

    def _enrich_supervisor_config(self, sc: dict | None) -> dict | None:
        """Export the supervisor capability surface: knowledge retrieval / tools /
        skills get reference info attached (same as Agent apps) so a cross-environment
        import can match them by name."""
        if not sc:
            return sc
        enriched = dict(sc)
        if enriched.get("knowledge_retrieval"):
            enriched["knowledge_retrieval"] = self._enrich_knowledge_retrieval(
                enriched["knowledge_retrieval"]
            )
        if enriched.get("tools"):
            enriched["tools"] = self._enrich_tools(enriched["tools"])
        if enriched.get("skills"):
            enriched["skills"] = self._enrich_skills(enriched["skills"])
        return enriched

    # ==================== Import ====================

    def import_dsl(
        self,
        dsl: dict,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        app_id: uuid.UUID | None = None,
        redis_client: Any = None,
    ) -> tuple[App, list[str]]:
        """Parse the DSL, create or overwrite the app config; returns (app, warnings).
        When app_id is set: validate type consistency then overwrite; when None: create.
        """
        app_meta = dsl.get("app", {})
        app_type = app_meta.get("type")
        if app_type not in (
            AppType.AGENT,
            AppType.MULTI_AGENT,
            AppType.WORKFLOW,
            AppType.PURE_WORKFLOW,
        ):
            raise BusinessException(f"不支持的应用类型: {app_type}", BizCode.BAD_REQUEST)

        warnings: list[str] = []
        now = utcnow_naive()

        if app_id is not None:
            return self._overwrite_dsl(
                dsl, app_id, app_type, workspace_id, tenant_id, warnings, now, redis_client
            )

        new_app = App(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            created_by=user_id,
            name=self._unique_app_name(app_meta.get("name", "导入应用"), workspace_id, app_type),
            description=app_meta.get("description"),
            icon=app_meta.get("icon"),
            icon_type=app_meta.get("icon_type"),
            type=app_type,
            visibility="private",
            status="draft",
            tags=app_meta.get("tags", []),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(new_app)
        self.db.flush()

        self._write_config(
            new_app.id, app_type, dsl, workspace_id, tenant_id, warnings, now,
            create=True, redis_client=redis_client,
        )

        self.db.commit()
        self.db.refresh(new_app)
        return new_app, warnings

    def _overwrite_dsl(
        self,
        dsl: dict,
        app_id: uuid.UUID,
        app_type: str,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        warnings: list,
        now: datetime.datetime,
        redis_client: Any = None,
    ) -> tuple[App, list[str]]:
        """Overwrite an existing app's config; raises on type mismatch."""
        app = self.db.query(App).filter(
            App.id == app_id,
            App.workspace_id == workspace_id,
            App.is_active.is_(True)
        ).first()
        if not app:
            raise ResourceNotFoundException("应用", str(app_id))
        if app.type != app_type:
            raise BusinessException(
                f"YAML 类型 '{app_type}' 与应用类型 '{app.type}' 不一致，无法导入",
                BizCode.BAD_REQUEST
            )

        self._write_config(
            app_id, app_type, dsl, workspace_id, tenant_id, warnings, now,
            create=False, redis_client=redis_client,
        )

        self.db.commit()
        self.db.refresh(app)
        return app, warnings

    def _write_config(
        self,
        app_id: uuid.UUID,
        app_type: str,
        dsl: dict,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        warnings: list,
        now: datetime.datetime,
        create: bool,
        redis_client: Any = None,
    ) -> None:
        """Write (create or overwrite) the app config."""
        if app_type == AppType.AGENT:
            cfg = dsl.get("agent_config") or {}
            fields = dict(
                system_prompt=cfg.get("system_prompt"),
                model_parameters=cfg.get("model_parameters"),
                default_model_config_id=self._resolve_model(
                    cfg.get("default_model_config_ref"), tenant_id, warnings
                ),
                knowledge_retrieval=self._resolve_knowledge_retrieval(
                    cfg.get("knowledge_retrieval"), workspace_id, tenant_id, warnings
                ),
                memory=self._resolve_memory(cfg.get("memory"), workspace_id, warnings),
                variables=cfg.get("variables", []),
                tools=self._resolve_tools(cfg.get("tools", []), tenant_id, warnings),
                skills=self._resolve_skills(cfg.get("skills", {}), tenant_id, warnings),
                features=cfg.get("features", {}),
                updated_at=now,
            )
            if create:
                self.db.add(AgentConfig(
                    id=uuid.uuid4(), app_id=app_id, is_active=True, created_at=now, **fields
                ))
            else:
                existing = self.db.query(AgentConfig).filter(AgentConfig.app_id == app_id).first()
                if existing:
                    for k, v in fields.items():
                        setattr(existing, k, v)
                else:
                    self.db.add(AgentConfig(
                        id=uuid.uuid4(), app_id=app_id, is_active=True, created_at=now, **fields
                    ))

        elif app_type == AppType.MULTI_AGENT:
            cfg = dsl.get("multi_agent_config") or {}
            fields = dict(
                orchestration_mode=cfg.get("orchestration_mode", "supervisor_loop"),
                master_agent_name=cfg.get("master_agent_name"),
                model_parameters=cfg.get("model_parameters"),
                default_model_config_id=self._resolve_model(
                    cfg.get("default_model_config_ref"), tenant_id, warnings
                ),
                master_agent_id=self._resolve_release(cfg.get("master_agent_ref"), warnings),
                sub_agents=self._resolve_sub_agents(
                    cfg.get("sub_agents", []), warnings, workspace_id
                ),
                routing_rules=self._resolve_routing_rules(
                    cfg.get("routing_rules"), warnings, workspace_id
                ),
                execution_config=cfg.get("execution_config", {}),
                supervisor_config=self._resolve_supervisor_config(
                    cfg.get("supervisor_config"), workspace_id, tenant_id, warnings
                ),
                aggregation_strategy=cfg.get("aggregation_strategy", "merge"),
                updated_at=now,
            )
            if create:
                self.db.add(MultiAgentConfig(
                    id=uuid.uuid4(), app_id=app_id, is_active=True, created_at=now, **fields
                ))
            else:
                existing = self.db.query(MultiAgentConfig).filter(
                    MultiAgentConfig.app_id == app_id
                ).first()
                if existing:
                    for k, v in fields.items():
                        setattr(existing, k, v)
                else:
                    self.db.add(MultiAgentConfig(
                        id=uuid.uuid4(), app_id=app_id, is_active=True, created_at=now, **fields
                    ))

        elif app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            raw_wf = dsl.get("workflow")
            if not raw_wf:
                raise BusinessException("工作流配置缺失", BizCode.BAD_REQUEST)
            raw_nodes = raw_wf.get("nodes") or []
            resolved_nodes = self._resolve_workflow_nodes(
                raw_nodes, tenant_id, workspace_id, warnings
            )
            resolved_dsl = {**dsl, "workflow": {**raw_wf, "nodes": resolved_nodes}}
            adapter = MemoryBearAdapter(resolved_dsl)
            if not adapter.validate_config():
                raise BusinessException("工作流配置格式无效", BizCode.BAD_REQUEST)
            result = adapter.parse_workflow()
            for e in result.errors:
                warnings.append(f"[节点错误] {e.node_name or e.node_id}: {e.detail}")
            for w in result.warnings:
                warnings.append(f"[节点警告] {w.node_name or w.node_id}: {w.detail}")
            wf_service = WorkflowService(self.db)
            raw_env_vars = [v.model_dump() for v in result.environment_variables]
            if self._has_secret_environment_variables(raw_env_vars):
                warnings.append(
                    "检测到 secret 类型环境变量，导入时其值已清空，请在导入后重新配置。"
                )
            environment_variables = self._clear_imported_secret_environment_variables(raw_env_vars)
            if create:
                wf_service.create_workflow_config(
                    app_id=app_id,
                    nodes=[n.model_dump() for n in result.nodes],
                    edges=[e.model_dump() for e in result.edges],
                    variables=[v.model_dump() for v in result.variables],
                    environment_variables=environment_variables,
                    execution_config=raw_wf.get("execution_config", {}),
                    features=raw_wf.get("features", {}),
                    triggers=raw_wf.get("triggers", []),
                    validate=False,
                    redis_client=redis_client,
                )
            else:
                existing = self.db.query(WorkflowConfig).filter(
                    WorkflowConfig.app_id == app_id
                ).first()
                if existing:
                    existing.nodes = [n.model_dump() for n in result.nodes]
                    existing.edges = [e.model_dump() for e in result.edges]
                    existing.variables = [v.model_dump() for v in result.variables]
                    existing.environment_variables = environment_variables
                    existing.execution_config = raw_wf.get("execution_config", {})
                    existing.features = raw_wf.get("features", {})
                    existing.triggers = raw_wf.get("triggers", [])
                    existing.updated_at = now
                    # Overwrite imports also invalidate the shared cache
                    delete_workflow_config(redis_client, app_id)
                else:
                    wf_service.create_workflow_config(
                        app_id=app_id,
                        nodes=[n.model_dump() for n in result.nodes],
                        edges=[e.model_dump() for e in result.edges],
                        variables=[v.model_dump() for v in result.variables],
                        environment_variables=environment_variables,
                        execution_config=raw_wf.get("execution_config", {}),
                        features=raw_wf.get("features", {}),
                        triggers=raw_wf.get("triggers", []),
                        validate=False,
                        redis_client=redis_client,
                    )

    def _unique_app_name(self, name: str, workspace_id: uuid.UUID, app_type: AppType) -> str:
        """Generate a unique app name, checking own and shared-to-workspace apps."""
        # App names of this workspace
        existing = {r[0] for r in self.db.query(App.name).filter(
            App.workspace_id == workspace_id,
            App.type == app_type,
            App.is_active.is_(True)
        ).all()}
        # App names shared to this workspace
        shared_names = {r[0] for r in self.db.query(App.name).join(
            AppShare, AppShare.source_app_id == App.id
        ).filter(
            AppShare.target_workspace_id == workspace_id,
            App.type == app_type,
            App.is_active.is_(True)
        ).all()}
        existing |= shared_names
        if name not in existing:
            return name
        counter = 1
        while f"{name}({counter})" in existing:
            counter += 1
        return f"{name}({counter})"

    def _resolve_model(
        self,
        ref: dict | None,
        tenant_id: uuid.UUID,
        warnings: list,
        allowed_types: Collection[str] | None = None,
    ) -> str | None:
        if not ref:
            return None
        model_id = ref.get("id")
        if model_id:
            try:
                model_uuid = uuid.UUID(str(model_id))
                resolved = get_importable_model_id(self.db, model_uuid, tenant_id, allowed_types)
                if resolved:
                    return str(resolved)
            except (ValueError, AttributeError):
                pass
        model_name = ref.get("name")
        if model_name:
            resolved = find_importable_model_id_by_name(
                self.db,
                tenant_id,
                model_name,
                provider=ref.get("provider"),
                ref_type=ref.get("type"),
                allowed_types=allowed_types,
            )
            if resolved:
                return str(resolved)
            warnings.append(f"模型 '{model_name}' 未匹配，已置空，请导入后手动配置")
        else:
            warnings.append(f"模型 ID '{model_id}' 未匹配，已置空，请导入后手动配置")
        return None

    def _resolve_kb(self, ref: dict | None, workspace_id: uuid.UUID, warnings: list) -> str | None:
        if not ref:
            return None
        kb_id = ref.get("id")
        if kb_id:
            try:
                kb_uuid = uuid.UUID(str(kb_id))
                kb_share = get_knowledge_share_row(self.db, workspace_id, kb_uuid)
                if kb_share:
                    kb = get_knowledge_row(self.db, kb_share.target_kb_id)
                    if kb and kb.status == 1:
                        return str(kb_share.target_kb_id)
                kb = get_workspace_published_knowledge_row(self.db, workspace_id, kb_uuid)
                if kb:
                    return str(kb.id)
            except (ValueError, AttributeError):
                pass
        warnings.append(f"知识库 '{kb_id}' 未匹配，已置空，请导入后手动配置")
        return None

    def _resolve_tool(self, ref: dict | None, tenant_id: uuid.UUID, warnings: list) -> str | None:
        if not ref:
            return None
        tool_id = ref.get("id")
        tool_name = ref.get("name")
        if tool_id:
            try:
                tool_uuid = uuid.UUID(str(tool_id))
                resolved = get_importable_tool_id(self.db, tool_uuid, tenant_id)
                if resolved:
                    return str(resolved)
            except (ValueError, AttributeError):
                pass
        if tool_name:
            resolved = find_tool_config_id_by_name(
                self.db, tenant_id, tool_name, tool_type=ref.get("tool_type")
            )
            if resolved:
                return str(resolved)
            warnings.append(f"工具 '{tool_name}' 未匹配，已置空，请导入后手动配置")
        else:
            warnings.append(f"工具 '{tool_id}' 未匹配，已置空，请导入后手动配置")
        return None

    def _resolve_release(self, ref: dict | None, warnings: list) -> uuid.UUID | None:
        if not ref:
            return None
        r = self.db.query(AppRelease).filter(
            AppRelease.app_id == ref.get("app_id"),
            AppRelease.version == ref.get("version"),
            AppRelease.is_active.is_(True)
        ).first()
        if not r:
            warnings.append(
                f"主 Agent 发布版本 '{ref.get('name')}' 未匹配，已置空，请导入后手动配置"
            )
        return r.id if r else None

    def _locate_agent_app(self, agent_id, workspace_id: uuid.UUID) -> App | None:
        """Locate a sub-agent app in the target workspace by an entry's agent_id.

        Backward compatible with older DSL: historical sub_agents[*].agent_id
        stored AppRelease.id — match App.id first (new shape), then resolve the
        owning app via AppRelease.id (old shape). Only apps owned by the target
        workspace are accepted; a source-workspace ID resolves to None
        (shared apps are not allowed as cluster sub-agents).
        """
        if not agent_id:
            return None
        try:
            agent_uuid = uuid.UUID(str(agent_id))
        except (ValueError, AttributeError, TypeError):
            return None
        app = self.db.query(App).filter(
            App.id == agent_uuid,
            App.workspace_id == workspace_id,
            App.type == AppType.AGENT,
            App.is_active.is_(True),
        ).first()
        if app:
            return app
        r = self.db.query(AppRelease).filter(AppRelease.id == agent_uuid).first()
        if r is not None:
            return self.db.query(App).filter(
                App.id == r.app_id,
                App.workspace_id == workspace_id,
                App.type == AppType.AGENT,
                App.is_active.is_(True),
            ).first()
        return None

    def _resolve_sub_agents(
        self, sub_agents: list, warnings: list, workspace_id: uuid.UUID
    ) -> list:
        result = []
        for s in (sub_agents or []):
            ref = s.get("_ref")
            entry = {k: v for k, v in s.items() if k != "_ref"}
            # Only the referenced Agent app ID decides whether the entry survives:
            # new DSL carries _ref.id, legacy DSL carries agent_id (a source-workspace
            # App.id or historical AppRelease.id). Referenced apps must be published,
            # so an ID hit is reused without re-checking status or rebinding by name;
            # an ID miss (cross-environment) drops the whole entry.
            agent_id = (ref or {}).get("id") or entry.get("agent_id")
            label = (ref or {}).get("name") or entry.get("name") or agent_id
            a = self._locate_agent_app(agent_id, workspace_id)
            if not a:
                warnings.append(
                    f"子 Agent '{label}' 在目标工作空间不存在，已清空，请导入后手动添加"
                )
                continue
            entry["agent_id"] = str(a.id)
            # agent_id is now the sub-agent app ID; release_id is invalid across
            # environments, so imports always follow the latest release
            entry["release_policy"] = "current"
            entry["release_id"] = None
            result.append(entry)
        return result

    def _resolve_routing_rules(
        self, rules: list | None, warnings: list, workspace_id: uuid.UUID
    ) -> list | None:
        if rules is None:
            return None
        result = []
        for r in rules:
            ref = r.get("_ref")
            entry = {k: v for k, v in r.items() if k != "_ref"}
            # Same ID-only rule as sub_agents: new DSL carries _ref.id, legacy DSL
            # carries target_agent_id; names never decide a rebind.
            agent_id = (ref or {}).get("id") or entry.get("target_agent_id")
            label = (ref or {}).get("name") or agent_id
            a = self._locate_agent_app(agent_id, workspace_id)
            if not a:
                # Target agent missing (possibly its sub-agent entry was dropped):
                # drop the rule to avoid a dangling route
                warnings.append(
                    f"路由目标 Agent '{label}' 在目标工作空间不存在，已清空该路由规则"
                )
                continue
            entry["target_agent_id"] = str(a.id)
            result.append(entry)
        return result

    def _resolve_supervisor_config(
        self,
        sc: dict | None,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        warnings: list,
    ) -> dict | None:
        """Import the supervisor capability surface: knowledge retrieval / tools /
        skills / memory are re-matched against the target environment; unmatched
        entries are cleared with a warning."""
        if not sc:
            return sc
        resolved = dict(sc)
        if resolved.get("knowledge_retrieval"):
            resolved["knowledge_retrieval"] = self._resolve_knowledge_retrieval(
                resolved["knowledge_retrieval"], workspace_id, tenant_id, warnings
            )
        if resolved.get("tools"):
            resolved["tools"] = self._resolve_tools(resolved["tools"], tenant_id, warnings)
        if resolved.get("skills"):
            resolved["skills"] = self._resolve_skills(resolved["skills"], tenant_id, warnings)
        if resolved.get("memory"):
            resolved["memory"] = self._resolve_memory(resolved["memory"], workspace_id, warnings)
        return resolved

    def _resolve_imported_model(
        self,
        model_id,
        model_ref,
        tenant_id: uuid.UUID,
        allowed_types: Collection[str],
    ) -> str | None:
        """Resolve a DSL model reference; accepts the new ref shape and legacy raw IDs."""
        ref = model_ref if isinstance(model_ref, dict) else None
        if ref is None and model_id:
            if isinstance(model_id, dict):
                ref = model_id
            else:
                try:
                    uuid.UUID(str(model_id))
                    ref = {"id": str(model_id)}
                except (ValueError, AttributeError):
                    ref = {"name": str(model_id)}
        return self._resolve_model(ref, tenant_id, [], allowed_types=allowed_types)

    def _resolve_reranker_config(
        self,
        config: dict,
        tenant_id: uuid.UUID,
        warnings: list,
        label: str,
    ) -> dict:
        """Resolve the Rerank model in a knowledge-retrieval config; strip DSL-only refs."""
        resolved = dict(config or {})
        reranker_ref = resolved.pop("reranker_ref", None)
        reranker_id = resolved.get("reranker_id")
        if not reranker_ref and not reranker_id:
            return resolved

        resolved_id = self._resolve_imported_model(
            reranker_id,
            reranker_ref,
            tenant_id,
            {ModelType.RERANK.value},
        )
        resolved["reranker_id"] = resolved_id
        if not resolved_id:
            model_label = (
                reranker_ref.get("name") or reranker_ref.get("id")
                if isinstance(reranker_ref, dict)
                else reranker_id
            )
            warnings.append(
                f"[{label}] Rerank 模型 '{model_label}' 未匹配或类型不正确，"
                "已置空，请导入后手动配置"
            )
        return resolved

    def _resolve_kb_entries(
        self,
        knowledge_bases: list,
        workspace_id: uuid.UUID,
        warnings: list,
        node_label: str,
    ) -> list:
        """Resolve KB entries one by one; drop unmatched ones with a warning."""
        resolved_kbs = []
        for kb in knowledge_bases:
            kb_id = kb.get("kb_id")
            if not kb_id:
                continue
            kb_ref = kb.get("_ref")
            if not isinstance(kb_ref, dict):
                kb_ref = {}
                if isinstance(kb_id, str):
                    try:
                        uuid.UUID(kb_id)
                        kb_ref["id"] = kb_id
                    except ValueError:
                        kb_ref["name"] = kb_id
                else:
                    kb_ref["name"] = kb_id
            resolved_id = self._resolve_kb(kb_ref, workspace_id, [])
            if resolved_id:
                entry = {k: v for k, v in kb.items() if k != "_ref"}
                resolved_kbs.append({**entry, "kb_id": resolved_id})
            else:
                warnings.append(f"[{node_label}] 知识库 '{kb_id}' 未匹配，已移除，请导入后手动配置")
        return resolved_kbs

    def _resolve_workflow_nodes(
        self, nodes: list, tenant_id: uuid.UUID, workspace_id: uuid.UUID, warnings: list
    ) -> list:
        """Resolve tool/KB/model references in workflow nodes; clear when unmatched."""
        resolved_nodes = []
        for node in nodes:
            node_type = node.get("type")
            config = dict(node.get("config") or {})
            node_label = node.get("name") or node.get("id")
            if node_type == NodeType.TOOL.value:
                tool_id = config.get("tool_id")
                if not tool_id:
                    # tool_id is already empty; clear without a duplicate warning
                    config["tool_id"] = None
                    config["tool_parameters"] = {}
                else:
                    tool_ref = {}
                    if isinstance(tool_id, str) and len(tool_id) >= 36:
                        try:
                            uuid.UUID(tool_id)
                            tool_ref["id"] = tool_id
                        except ValueError:
                            tool_ref["name"] = tool_id
                    else:
                        tool_ref["name"] = tool_id
                    resolved_tool_id = self._resolve_tool(tool_ref, tenant_id, [])
                    if resolved_tool_id:
                        config["tool_id"] = resolved_tool_id
                    else:
                        warnings.append(
                            f"[{node_label}] 工具 '{tool_id}' 未匹配，已置空，请导入后手动配置"
                        )
                        config["tool_id"] = None
                        config["tool_parameters"] = {}
            elif node_type == NodeType.KNOWLEDGE_RETRIEVAL.value:
                config["knowledge_bases"] = self._resolve_kb_entries(
                    config.get("knowledge_bases") or [], workspace_id, warnings, node_label
                )
                config = self._resolve_reranker_config(config, tenant_id, warnings, node_label)
            elif node_type == NodeType.AGENT.value:
                model = dict(config.get("model") or {})
                model_ref = model.pop("model_ref", None)
                model_id = model.get("model_id")
                if model_ref or model_id:
                    resolved_model_id = self._resolve_imported_model(
                        model_id,
                        model_ref,
                        tenant_id,
                        LLM_FAMILY_TYPES,
                    )
                    model["model_id"] = resolved_model_id
                    if not resolved_model_id:
                        model_label = (
                            model_ref.get("name") or model_ref.get("id")
                            if isinstance(model_ref, dict)
                            else model_id
                        )
                        warnings.append(
                            f"[{node_label}] 智能体模型 '{model_label}' 未匹配或类型不正确，"
                            "已置空，请导入后手动配置"
                        )
                    config["model"] = model

                # Reference mode: locate the referenced Agent app by ID in the
                # target workspace (_ref.id, or legacy reference.app_id). When the
                # ID misses (cross-environment), fall back to inline mode and clear
                # the reference; nodes and edges are kept for manual re-configuration.
                if config.get("mode") == "reference":
                    reference = dict(config.get("reference") or {})
                    ref = reference.pop("_ref", None)
                    ref_id = ref.get("id") if isinstance(ref, dict) else None
                    ref_name = ref.get("name") if isinstance(ref, dict) else None
                    target_app = self._locate_agent_app(
                        ref_id or reference.get("app_id"), workspace_id
                    )
                    if target_app:
                        reference["app_id"] = str(target_app.id)
                        reference["release_policy"] = "current"
                        reference["release_id"] = None
                        config["reference"] = reference
                    else:
                        _ref_label = ref_name or ref_id or reference.get("app_id")
                        warnings.append(
                            f"[{node_label}] 引用的 Agent 应用 '{_ref_label}' "
                            "在目标工作空间不存在，已回退为内置模式，请导入后手动配置"
                        )
                        config["mode"] = "inline"
                        config["reference"] = None

                # Agent nodes embed a KB retrieval tool: resolve KBs and validate Rerank
                knowledge_retrieval = config.get("knowledge_retrieval")
                if knowledge_retrieval:
                    resolved_knowledge_retrieval = dict(knowledge_retrieval)
                    resolved_knowledge_retrieval["knowledge_bases"] = self._resolve_kb_entries(
                        resolved_knowledge_retrieval.get("knowledge_bases") or [],
                        workspace_id,
                        warnings,
                        node_label,
                    )
                    config["knowledge_retrieval"] = self._resolve_reranker_config(
                        resolved_knowledge_retrieval, tenant_id, warnings, node_label
                    )

                # Support the early frontend layout that flattened KB config onto the
                # node root
                if config.get("knowledge_bases"):
                    config["knowledge_bases"] = self._resolve_kb_entries(
                        config["knowledge_bases"], workspace_id, warnings, node_label
                    )
                config = self._resolve_reranker_config(config, tenant_id, warnings, node_label)
            elif node_type in (
                NodeType.LLM.value,
                NodeType.QUESTION_CLASSIFIER.value,
                NodeType.PARAMETER_EXTRACTOR.value,
            ):
                model_ref = config.get("model_ref") or config.get("model_id")
                if model_ref:
                    ref_dict = None
                    if isinstance(model_ref, dict):
                        ref_dict = {
                            "id": model_ref.get("id"),
                            "name": model_ref.get("name"),
                            "provider": model_ref.get("provider"),
                            "type": model_ref.get("type")
                        }
                    elif isinstance(model_ref, str):
                        try:
                            uuid.UUID(model_ref)
                            ref_dict = {"id": model_ref}
                        except ValueError:
                            ref_dict = {"name": model_ref}
                    if ref_dict:
                        resolved_model_id = self._resolve_model(ref_dict, tenant_id, warnings)
                        if resolved_model_id:
                            config["model_id"] = resolved_model_id
                            if "model_ref" in config:
                                del config["model_ref"]
                        else:
                            warnings.append(f"[{node_label}] 模型未匹配，已置空，请导入后手动配置")
                            config["model_id"] = None
                            if "model_ref" in config:
                                del config["model_ref"]
                    else:
                        warnings.append(f"[{node_label}] 模型未匹配，已置空，请导入后手动配置")
                        config["model_id"] = None
                        if "model_ref" in config:
                            del config["model_ref"]
            elif node_type in (NodeType.MEMORY_READ.value, NodeType.MEMORY_WRITE.value):
                config.pop("config_id", None)
            resolved_nodes.append({**node, "config": config})
        return resolved_nodes

    def _resolve_knowledge_retrieval(
        self,
        kr: dict | None,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        warnings: list,
    ) -> dict | None:
        if not kr:
            return kr
        resolved_kbs = []
        for kb in kr.get("knowledge_bases", []):
            ref = kb.get("_ref") or ({"name": kb.get("kb_id")} if kb.get("kb_id") else None)
            entry = {k: v for k, v in kb.items() if k != "_ref"}
            resolved_id = self._resolve_kb(ref, workspace_id, warnings)
            if resolved_id is None:
                continue
            entry["kb_id"] = resolved_id
            resolved_kbs.append(entry)
        resolved = {k: v for k, v in kr.items() if k != "knowledge_bases"} | {
            "knowledge_bases": resolved_kbs
        }
        return self._resolve_reranker_config(resolved, tenant_id, warnings, "Agent 应用知识库配置")

    def _resolve_memory(
        self, memory: dict | None, workspace_id: uuid.UUID, warnings: list
    ) -> dict | None:
        if not memory:
            return memory
        config_id = memory.get("memory_config_id") or memory.get("memory_content")
        if not config_id:
            return memory
        try:
            config_uuid = uuid.UUID(str(config_id))
        except (ValueError, AttributeError):
            exists = memory_config_exists_by_legacy_id(self.db, int(config_id), workspace_id)
            if not exists:
                warnings.append(f"记忆配置 '{config_id}' 未匹配，已置空，请导入后手动配置")
                return {**memory, "memory_config_id": None, "enabled": memory.get("enabled", True)}
            return memory
        exists = memory_config_exists_by_uuid(self.db, config_uuid, workspace_id)
        if not exists:
            warnings.append(f"记忆配置 '{config_id}' 未匹配，已置空，请导入后手动配置")
            return {**memory, "memory_config_id": None, "enabled": memory.get("enabled", True)}
        return memory

    def _resolve_skills(self, skills: dict | None, tenant_id: uuid.UUID, warnings: list) -> dict:
        if not skills:
            return skills or {}
        resolved_ids = []
        for entry in (skills.get("skill_ids") or []):
            # entry may be {"id": "...", "_ref": {...}} or a plain string
            if isinstance(entry, dict):
                ref = entry.get("_ref") or (
                    {"name": None, "id": entry.get("id")} if entry.get("id") else None
                )
                skill_id = self._resolve_skill(ref, tenant_id, warnings)
            else:
                skill_id = self._resolve_skill({"id": str(entry)}, tenant_id, warnings)
            if skill_id:
                resolved_ids.append(str(skill_id))
        return {**{k: v for k, v in skills.items() if k != "skill_ids"}, "skill_ids": resolved_ids}

    def _resolve_skill(self, ref: dict | None, tenant_id: uuid.UUID, warnings: list) -> str | None:
        if not ref:
            return None
        # Match by id first
        if ref.get("id"):
            try:
                s = get_importable_skill_id(self.db, uuid.UUID(str(ref["id"])), tenant_id)
                if s:
                    return str(s)
            except Exception:
                pass
        # Then match by name
        if ref.get("name"):
            s = find_skill_id_by_name(self.db, tenant_id, ref["name"])
            if s:
                return str(s)
        warnings.append(f"未找到技能: {ref}")
        return None

    def _resolve_tools(self, tools: list, tenant_id: uuid.UUID, warnings: list) -> list:
        result = []
        for t in (tools or []):
            ref = t.get("_ref") or ({"name": t.get("tool_id")} if t.get("tool_id") else None)
            entry = {k: v for k, v in t.items() if k != "_ref"}
            resolved_id = self._resolve_tool(ref, tenant_id, warnings)
            if resolved_id is None:
                continue
            entry["tool_id"] = resolved_id
            result.append(entry)
        return result
