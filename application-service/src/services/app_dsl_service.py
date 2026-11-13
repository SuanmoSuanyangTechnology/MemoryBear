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
import logging
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
from .multi_agent_release_resolver import resolve_effective_release_id
from .workflow_service import WorkflowService

logger = logging.getLogger(__name__)


class AppDslService:

    def __init__(self, db: Session):
        self.db = db

    # ==================== Export ====================

    def export_dsl(
        self,
        app_id: uuid.UUID,
        release_id: uuid.UUID | None = None,
        *,
        include_cluster: bool = True,
    ) -> tuple[str, str]:
        """Build the app DSL yaml string; returns (yaml_str, filename).

        ``include_cluster`` (default on) adds the top-level ``cluster:`` section to a
        multi-agent app: every sub-agent exported as a full config, so one file
        carries the whole cluster. Old readers ignore the unknown key, and files
        without it import exactly as before.
        """
        app = self.db.query(App).filter(App.id == app_id, App.is_active.is_(True)).first()
        if not app:
            raise ResourceNotFoundException(
                "应用", str(app_id), code=BizCode.APP_NOT_FOUND
            )

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
            return self._export_release(
                app, release_id, meta, app_meta, include_cluster=include_cluster
            )

        return self._export_draft(app, meta, app_meta, include_cluster=include_cluster)

    def _export_release(
        self, app: App, release_id: uuid.UUID, meta: dict, app_meta: dict,
        *, include_cluster: bool = True,
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
        if include_cluster:
            self._attach_cluster(dsl, app)
            self._attach_workflow_members(dsl, app)
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

    def _export_draft(
        self, app: App, meta: dict, app_meta: dict, *, include_cluster: bool = True
    ) -> tuple[str, str]:
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

        if include_cluster:
            self._attach_cluster(dsl, app)
            self._attach_workflow_members(dsl, app)
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

    # ==================== Cluster export ====================

    def _attach_cluster(self, dsl: dict, app: App) -> None:
        """Add the top-level ``cluster:`` section carrying every sub-agent config.

        Root and members deliberately use **different sources**: the root follows
        the caller's choice (release snapshot, or the draft when ``release_id`` is
        omitted), while every member is exported from its *effective release* — a
        sub-agent has no "draft is runtime" meaning, and cluster config cannot even
        be saved with an unpublished member (``resolve_release_for_save`` requires
        ``current_release_id``).
        """
        if app.type != AppType.MULTI_AGENT:
            return
        config = dsl.get("multi_agent_config") or {}
        if not config.get("sub_agents"):
            return

        members, warnings = self.collect_cluster_members(app, config)
        if not members:
            if warnings:
                dsl["export_warnings"] = warnings
            return

        self._rewrite_cluster_local_ids(config, members, warnings)
        dsl["cluster"] = members
        if warnings:
            dsl["export_warnings"] = warnings

    def _attach_workflow_members(self, dsl: dict, app: App) -> None:
        """Add the ``cluster:`` section for a workflow's referenced Agent apps.

        工作流与集群共用同一个随包载体：被引用的 Agent 应用各出一份完整配置，
        装到目标环境时由 `import_dsl` 落地并按 `dep_map` 绑定。节点里的
        `reference.app_id` 改写成 `app:<源 app id>` local id，安装侧只认这个前缀
        串，绝不拿源环境的裸 id 去目标空间里碰运气。

        与 `collect_cluster_members` 的两处差别都是刻意的：

        * 引用不可用（应用已删 / 不是 Agent / 跨空间 / 没有可用版本）只跳过并
          告警，不让整个导出失败 —— 工作流里一个脏引用把整份 YAML 打回去，
          用户没有任何自救路径；
        * 节点引用**无条件**改写成本地 id，即使该应用没能收进 `cluster[]`。
          留着源环境的裸 UUID 更危险：它长得像真的 id，导入侧会当成目标空间的
          应用去查，可能绑到别人的同名应用上。改写后 `dep_map` 查不到，落地方
          走既有的 inline 降级 + 告警，节点和连线都还在。
        """

        if app.type not in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            return
        workflow = dsl.get("workflow")
        if not isinstance(workflow, dict):
            return
        nodes = workflow.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return

        members: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen: set[uuid.UUID] = set()

        for node in nodes:
            if not isinstance(node, dict) or node.get("type") != NodeType.AGENT.value:
                continue
            config = node.get("config")
            if not isinstance(config, dict) or config.get("mode") != "reference":
                continue
            reference = config.get("reference")
            if not isinstance(reference, dict):
                continue

            label = node.get("name") or node.get("id")
            ref = reference.get("_ref") if isinstance(reference.get("_ref"), dict) else {}
            raw_id = ref.get("id") or reference.get("app_id")
            # `_ref.id` 可能是历史形态的 AppRelease.id，统一问一次真实应用行。
            target_app, _ = self._locate_any_app(raw_id)

            policy = str(reference.get("release_policy") or "current")
            pinned_release_id = reference.get("release_id")

            # 引用改写放在可用性判断**之前**，且不看能否随包：源环境的裸 UUID
            # 长得就像目标空间的应用 id，留着会被 `_locate_agent_app` 当成真 id
            # 去查，可能绑到别人的同名应用上。改成本地 id 后，`dep_map` 查不到就
            # 走既有的 inline 降级 + 告警，节点与连线都还在。
            reference = dict(reference)
            reference["app_id"] = (
                f"app:{target_app.id}" if target_app is not None
                else self._app_local_id(raw_id)
            )
            reference["release_policy"] = "current"
            reference["release_id"] = None
            config["reference"] = reference
            node["config"] = config

            if target_app is None:
                warnings.append(f"[{label}] 引用的 Agent 应用不存在，未随包导出")
                continue
            if target_app.id in seen:
                continue
            if not target_app.is_active or target_app.type != AppType.AGENT:
                warnings.append(
                    f"Agent 节点引用的「{target_app.name}」不是可用的 Agent 应用，未随包导出"
                )
                continue
            if target_app.workspace_id != app.workspace_id:
                warnings.append(
                    f"Agent 节点引用的「{target_app.name}」是只读引用"
                    "（所属空间与本应用不同），未随包导出"
                )
                continue

            release = self._referenced_agent_release(
                target_app, policy=policy, release_id=pinned_release_id
            )
            if release is None:
                warnings.append(
                    f"Agent 节点引用的「{target_app.name}」没有可用的发布版本，未随包导出"
                )
                continue

            seen.add(target_app.id)
            members.append(
                {
                    "local_id": f"app:{target_app.id}",
                    # 工作流引用不是集群子 Agent，节点之间没有从属关系。
                    "parent_local_id": None,
                    "app_type": target_app.type,
                    "name": target_app.name,
                    "description": target_app.description,
                    "config": self._enrich_release_config(
                        AppType.AGENT,
                        release.config or {},
                        release.default_model_config_id,
                    ),
                }
            )

        if members:
            dsl["cluster"] = members
        if warnings:
            existing = dsl.get("export_warnings")
            dsl["export_warnings"] = (
                list(existing) if isinstance(existing, list) else []
            ) + warnings

    @staticmethod
    def _app_local_id(value: Any) -> str:
        """`app:<uuid>` local id for a reference we could not resolve to a row.

        Falls back to `"app:"` + the raw string when the value is not a UUID, so
        the install side still sees a namespaced local id instead of a bare source
        id (or `None`) and routes it through the inline downgrade.
        """

        try:
            return f"app:{uuid.UUID(str(value))}"
        except (ValueError, AttributeError, TypeError):
            return f"app:{value}"

    def _referenced_agent_release(
        self, app: App, *, policy: str, release_id: Any
    ) -> AppRelease | None:
        """The release a workflow node's `reference` resolves to, or None.

        `pinned` 指向具体版本；其余（含缺省）跟随被引用应用自己的当前版本 ——
        与市场投稿侧 `_workflow_ref_release` 同一口径，两个入口不能导出不同的版本。
        """

        if policy == "pinned" and release_id:
            try:
                release = self.db.get(AppRelease, uuid.UUID(str(release_id)))
            except (ValueError, AttributeError, TypeError):
                release = None
            if (
                release is not None
                and release.is_active
                and release.app_id == app.id
            ):
                return release

        current = app.current_release_id
        if not current:
            return None
        release = self.db.get(AppRelease, current)
        if release is None or not release.is_active or release.app_id != app.id:
            return None
        return release

    def collect_cluster_members(
        self, root_app: App, config: dict
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Collect each referenced sub-agent's config, deduped by owning app.

        Unlike ``collect_cluster`` this does **not** require the root to be
        published — it only needs each member's effective release, which is always
        a real release (``release_policy=pinned`` → its ``release_id``, otherwise
        the app's ``current_release_id``).

        Two kinds of member are treated differently, and the asymmetry is the
        point: a member app that exists but belongs to another workspace is a
        read-only catalog reference (``link_mode=reference`` builds no local app
        row), so it is skipped with a warning — failing the whole export would
        leave the user no way to salvage a dirty cluster. A missing or unpublished
        member fails the export instead, because silently shipping a cluster with
        holes is worse than an explicit error.
        """
        members: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen: set[uuid.UUID] = set()
        root_local_id = f"app:{root_app.id}"

        for entry in config.get("sub_agents") or []:
            agent_id = self._entry_agent_id(entry)
            app, legacy_app_id = self._locate_any_app(agent_id)
            label = entry.get("name") or (entry.get("_ref") or {}).get("name") or str(agent_id)

            if app is None:
                raise self._incomplete_cluster(
                    f"子 Agent「{label}」的应用不存在", label, "sub-agent app not found"
                )
            if app.workspace_id != root_app.workspace_id:
                warnings.append(
                    f"子 Agent「{app.name}」是只读引用（所属空间与本应用不同），未随包导出"
                )
                continue
            if app.type != AppType.AGENT:
                raise self._incomplete_cluster(
                    f"子 Agent「{label}」不是 Agent 类型应用",
                    label,
                    "sub-agent is not an agent app",
                )
            if not app.is_active:
                raise self._incomplete_cluster(
                    f"子 Agent「{label}」已被删除", label, "sub-agent app deleted"
                )
            if app.id in seen:
                continue

            try:
                release_id = resolve_effective_release_id(self.db, entry, strict=True)
            except BusinessException as error:
                raise self._incomplete_cluster(
                    f"子 Agent「{label}」没有可用的发布版本", label, str(error.message)
                ) from error

            release = self.db.get(AppRelease, release_id)
            if release is None:
                raise self._incomplete_cluster(
                    f"子 Agent「{label}」的发布版本不存在", label, "release not found"
                )

            seen.add(app.id)
            members.append(
                {
                    "local_id": f"app:{app.id}",
                    "parent_local_id": root_local_id,
                    "app_type": app.type,
                    "name": app.name,
                    "description": app.description,
                    "config": self._enrich_release_config(
                        AppType.AGENT, release.config or {}, release.default_model_config_id
                    ),
                }
            )
        return members, warnings

    @staticmethod
    def _entry_agent_id(entry: dict) -> Any:
        ref = entry.get("_ref") or {}
        return ref.get("id") or entry.get("agent_id")

    def _locate_any_app(self, agent_id: Any) -> tuple[App | None, uuid.UUID | None]:
        """Any app an entry may point at, regardless of workspace/type/status.

        ``collect_cluster_members`` needs the unfiltered row to tell "missing"
        (hard error) from "exists but another workspace's" (read-only reference,
        skipped with a warning), so it cannot reuse ``_locate_agent_app``.
        """
        if not agent_id:
            return None, None
        try:
            agent_uuid = uuid.UUID(str(agent_id))
        except (ValueError, AttributeError, TypeError):
            return None, None
        app = self.db.query(App).filter(App.id == agent_uuid).first()
        if app is not None:
            return app, None
        # Legacy shape: agent_id stores AppRelease.id.
        release = self.db.query(AppRelease).filter(AppRelease.id == agent_uuid).first()
        if release is None:
            return None, None
        return self.db.query(App).filter(App.id == release.app_id).first(), release.app_id

    @staticmethod
    def _incomplete_cluster(message: str, name: Any, reason: str) -> BusinessException:
        return BusinessException(
            message,
            BizCode.EXTERNAL_CATALOG_INVALID,
            data={
                "reason": "INCOMPLETE_BUNDLE",
                "missing": [{"kind": "sub_agent", "name": name, "reason": reason}],
            },
        )

    def _rewrite_cluster_local_ids(
        self, config: dict, members: list[dict[str, Any]], warnings: list[str]
    ) -> None:
        """Rewrite cross-app references to ``app:<源 app id>`` local ids.

        This is what lets the import side reuse ``_resolve_bundle_members``: it
        binds strictly through ``dep_map``, never by name. ``release_id`` is
        dropped because release ids are meaningless across environments;
        ``release_policy`` becomes ``current`` so the landed cluster follows each
        member's own release.
        """
        local_ids = {str(member["local_id"]).removeprefix("app:"): member["local_id"]
                     for member in members}

        def to_local(value: Any) -> str | None:
            if not value:
                return None
            mapped = local_ids.get(str(value))
            if mapped is not None:
                return mapped
            # Legacy shape: the entry stores an AppRelease.id. Resolve it to the
            # owning app so the id-keyed map still matches instead of silently
            # dropping a perfectly exportable member.
            app, _ = self._locate_any_app(value)
            return local_ids.get(str(app.id)) if app is not None else None

        # Partition *before* mutating: the rewrite replaces ``agent_id`` with an
        # ``app:`` prefixed local id, so re-reading it afterwards would no longer
        # match the id-keyed map and would drop every entry.
        kept_sub_agents = []
        for entry in config.get("sub_agents") or []:
            if to_local(self._entry_agent_id(entry)) is None:
                # Skipped member (read-only reference): drop the entry rather than
                # emit a dangling reference into the package.
                warnings.append(
                    f"子 Agent「{entry.get('name') or entry.get('agent_id')}」"
                    "未能导出，已从包中移除"
                )
                continue
            kept_sub_agents.append(entry)

        for entry in kept_sub_agents:
            entry["agent_id"] = to_local(self._entry_agent_id(entry))
            entry["release_policy"] = "current"
            entry["release_id"] = None
        config["sub_agents"] = kept_sub_agents

        if config.get("routing_rules"):
            kept_rules = []
            for rule in config["routing_rules"]:
                local_id = to_local(rule.get("target_agent_id"))
                if local_id is None:
                    continue
                rule["target_agent_id"] = local_id
                kept_rules.append(rule)
            config["routing_rules"] = kept_rules

        if not kept_sub_agents:
            warnings.append("集群未包含任何可导出的子 Agent，cluster 段为空")

    # ==================== Import ====================

    def import_dsl(
        self,
        dsl: dict,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        app_id: uuid.UUID | None = None,
        redis_client: Any = None,
        *,
        commit: bool = True,
        dep_map: dict[str, str] | None = None,
        resolve_cross_app_refs: bool = True,
        origin_columns: dict[str, Any] | None = None,
        cluster_report: list[dict[str, Any]] | None = None,
    ) -> tuple[App, list[str]]:
        """Parse the DSL, create or overwrite the app config; returns (app, warnings).
        When app_id is set: validate type consistency then overwrite; when None: create.

        The three keyword-only parameters serve bundle landing (§7.5.5) and default
        to the historical behaviour, so the console's "import a YAML file" path is
        untouched:

        - ``commit=False``: the caller owns the transaction. Otherwise a cluster
          landing commits each app as soon as it is written, so a failure on the
          third app leaves the first two behind and the caller's rollback cannot
          reach them.
        - ``dep_map``: ``local_id`` → new local id, so a packaged dependency is
          bound to the exact row just created instead of being re-matched by name
          (which can hit an unrelated same-named tool in the target tenant).
        - ``resolve_cross_app_refs=False``: sub-agent entries already carry
          ``local_id``s resolved through ``dep_map``. Running them through
          ``_locate_agent_app`` would look for app ids that do not exist yet and
          silently drop the entries.
        - ``origin_columns``: 来源列（``origin`` / ``external_source`` /
          ``external_resource_id`` / ``external_version``），只有市场落地传。
          不传就落到模型默认值 ``origin='local'``，控制台的「导入 YAML」路径
          因此完全不受影响。
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
            # Validate the overwrite target *before* landing members: a bad app id
            # or a type mismatch must not leave freshly created sub-agents behind.
            target_app = self._assert_overwrite_target(app_id, app_type, workspace_id)
            cluster_dep_map = self._land_cluster_members(
                dsl, workspace_id, tenant_id, user_id, warnings, now, redis_client,
                dep_map=dep_map, cluster_report=cluster_report,
                # Overwrite target already exists: keep its master-agent binding
                # instead of clearing it just because the incoming YAML carries a
                # source-environment ref.
                create_root=False,
            )
            if cluster_dep_map is not None:
                return self._overwrite_dsl(
                    dsl, app_id, app_type, workspace_id, tenant_id, warnings, now, redis_client,
                    commit=commit, dep_map=cluster_dep_map,
                    resolve_cross_app_refs=False, app=target_app,
                )
            return self._overwrite_dsl(
                dsl, app_id, app_type, workspace_id, tenant_id, warnings, now, redis_client,
                commit=commit, dep_map=dep_map,
                resolve_cross_app_refs=resolve_cross_app_refs, app=target_app,
            )

        cluster_dep_map = self._land_cluster_members(
            dsl, workspace_id, tenant_id, user_id, warnings, now, redis_client,
            dep_map=dep_map, cluster_report=cluster_report,
            create_root=True,
        )
        if cluster_dep_map is not None:
            dep_map = cluster_dep_map
            resolve_cross_app_refs = False

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
            # 市场落地时记录「这一行是从哪个市场资源、哪个版本复制来的」；
            # 控制台导入 YAML 不传，落到模型默认值 origin='local'（§11.2）。
            **(origin_columns or {}),
        )
        self.db.add(new_app)
        self.db.flush()

        self._write_config(
            new_app.id, app_type, dsl, workspace_id, tenant_id, warnings, now,
            create=True, redis_client=redis_client,
            dep_map=dep_map, resolve_cross_app_refs=resolve_cross_app_refs,
        )

        if commit:
            self.db.commit()
            self.db.refresh(new_app)
        else:
            # The caller owns the transaction; flush only, so the app id is
            # readable but nothing is durable yet (§7.5.5).
            self.db.flush()
        return new_app, warnings

    def _land_cluster_members(
        self,
        dsl: dict,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        warnings: list[str],
        now: datetime.datetime,
        redis_client: Any = None,
        *,
        dep_map: dict[str, str] | None = None,
        cluster_report: list[dict[str, Any]] | None = None,
        create_root: bool = False,
    ) -> dict[str, str] | None:
        """Land every ``cluster[]`` member before the root app, returning dep_map.

        Returns ``None`` when the DSL is not a packaged cluster, so the console's
        ordinary single-app import keeps its historical behaviour byte for byte.

        Reuse rule (deliberately **id-only**): a member whose source app id still
        exists in the target workspace as an active agent is bound to that row and
        left untouched. Names never decide a rebind — a same-named but different
        agent would be silently hijacked, and two clusters would then share one
        editable app row. An id miss creates a fresh draft copy; name collisions
        are resolved by ``_unique_app_name`` into ``X(1)``.

        Nothing here commits: the root app and every member must land in the
        caller's single transaction, otherwise a failure on the third member
        leaves the first two behind.
        """
        members = dsl.get("cluster")
        if not isinstance(members, list) or not members:
            return None

        landed: dict[str, str] = dict(dep_map or {})
        report = cluster_report if cluster_report is not None else []

        for member in members:
            local_id = str(member.get("local_id") or "")
            if not local_id:
                continue
            name = member.get("name") or "导入子 Agent"

            existing = self._find_reusable_member(local_id, workspace_id)
            if existing is not None:
                landed[local_id] = str(existing.id)
                warnings.append(f"已复用目标空间已有的子 Agent「{existing.name}」，其配置未改动")
                report.append(
                    {
                        "local_id": local_id,
                        "app_id": str(existing.id),
                        "name": existing.name,
                        "published": existing.current_release_id is not None,
                        "reused": True,
                    }
                )
                continue

            member_app = App(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                created_by=user_id,
                name=self._unique_app_name(name, workspace_id, AppType.AGENT),
                description=member.get("description"),
                icon=None,
                icon_type=None,
                type=AppType.AGENT,
                visibility="private",
                status="draft",
                tags=[],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            self.db.add(member_app)
            self.db.flush()
            self._write_config(
                member_app.id,
                AppType.AGENT,
                {"app": {"type": AppType.AGENT}, "agent_config": member.get("config") or {}},
                workspace_id,
                tenant_id,
                warnings,
                now,
                create=True,
                redis_client=redis_client,
            )
            landed[local_id] = str(member_app.id)
            if member_app.name != name:
                # Id missed but a same-named agent exists: the copy got a suffix, and
                # the user should know these are two separate apps from now on.
                warnings.append(
                    f"目标空间已存在同名应用「{name}」，本次已新建独立副本「{member_app.name}」"
                )
            report.append(
                {
                    "local_id": local_id,
                    "app_id": str(member_app.id),
                    "name": member_app.name,
                    "published": False,
                    "reused": False,
                }
            )

        if create_root and dsl.get("app", {}).get("type") == AppType.MULTI_AGENT:
            # Only when the root is a *fresh* draft: its stored master-agent release
            # reference points at the source environment and can never match here.
            # Blank it silently — the "未匹配" warning would only be noise, since
            # publishing re-points it at the cluster's own release.
            #
            # Overwrite import (``create_root=False``) must NOT take this branch: the
            # root already exists and may hold a valid binding in this tenant. Blanking
            # it there silently drops that binding, and `_resolve_release(None)` then
            # writes ``master_agent_id=None`` without even a warning.
            dsl.setdefault("multi_agent_config", {})["master_agent_ref"] = None

        # 复用成员没有「导入」，只是绑定到空间里已有的应用；两者都未发布时
        # 都会挡住集群发布，但混在一句「已导入 N 个子 Agent（草稿）」里会让用户
        # 以为本次新建了它们，因此分开报，且都保留，避免漏掉阻塞发布的那一项。
        created_unpublished = [
            item["name"] for item in report
            if not item["published"] and not item.get("reused")
        ]
        reused_unpublished = [
            item["name"] for item in report
            if not item["published"] and item.get("reused")
        ]
        # 工作流与集群共用这套落地逻辑，措辞按根应用类型区分，否则用户会看到
        # 「本集群才能发布」出现在一个工作流上。
        container = (
            "本集群"
            if (dsl.get("app") or {}).get("type") == AppType.MULTI_AGENT
            else "本工作流"
        )
        if created_unpublished:
            warnings.append(
                f"已导入 {len(created_unpublished)} 个子 Agent（草稿），请先逐个发布它们，"
                f"{container}才能发布：" + "、".join(created_unpublished)
            )
        if reused_unpublished:
            warnings.append(
                f"另有 {len(reused_unpublished)} 个复用的子 Agent 尚未发布，"
                f"需先发布它们{container}才能发布：" + "、".join(reused_unpublished)
            )
        return landed

    def count_importable_cluster_members(
        self, dsl: dict, workspace_id: uuid.UUID
    ) -> int:
        """How many apps a cluster import will create next to the root.

        Members bound to an existing app (same id still present) are reused and
        consume no quota slot, so the count is "members minus reusable ones".
        Used by the route to reserve quota *before* landing anything.
        """
        members = dsl.get("cluster")
        if not isinstance(members, list):
            return 0
        created = 0
        for member in members:
            local_id = str(member.get("local_id") or "")
            if not local_id:
                continue
            if self._find_reusable_member(local_id, workspace_id) is None:
                created += 1
        return created

    def _find_reusable_member(self, local_id: str, workspace_id: uuid.UUID) -> App | None:
        """The existing sub-agent a ``cluster[].local_id`` points at, if any.

        Only ``App.id`` is consulted (the id embedded in ``app:<uuid>``). Shared
        apps are out of scope on purpose: a read-only catalog reference has no
        local app row of its own, so it can never be a reusable member.
        """
        raw = local_id.removeprefix("app:")
        try:
            app_uuid = uuid.UUID(raw)
        except (ValueError, AttributeError, TypeError):
            return None
        return self.db.query(App).filter(
            App.id == app_uuid,
            App.workspace_id == workspace_id,
            App.type == AppType.AGENT,
            App.is_active.is_(True),
        ).first()

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
        *,
        commit: bool = True,
        dep_map: dict[str, str] | None = None,
        resolve_cross_app_refs: bool = True,
        app: App | None = None,
    ) -> tuple[App, list[str]]:
        """Overwrite an existing app's config; raises on type mismatch.

        ``app`` lets the caller pass the target it already validated, so members
        are landed only after the overwrite target is known to be valid.
        """
        app = app or self._assert_overwrite_target(app_id, app_type, workspace_id)

        self._write_config(
            app_id, app_type, dsl, workspace_id, tenant_id, warnings, now,
            create=False, redis_client=redis_client,
            dep_map=dep_map, resolve_cross_app_refs=resolve_cross_app_refs,
        )

        if commit:
            self.db.commit()
            self.db.refresh(app)
        else:
            self.db.flush()
        return app, warnings

    def _assert_overwrite_target(
        self, app_id: uuid.UUID, app_type: str, workspace_id: uuid.UUID
    ) -> App:
        """The app an overwrite import would write into; raises if it cannot."""
        app = self.db.query(App).filter(
            App.id == app_id,
            App.workspace_id == workspace_id,
            App.is_active.is_(True)
        ).first()
        if not app:
            raise ResourceNotFoundException(
                "应用", str(app_id), code=BizCode.APP_NOT_FOUND
            )
        if app.type != app_type:
            raise BusinessException(
                f"YAML 类型 '{app_type}' 与应用类型 '{app.type}' 不一致，无法导入",
                BizCode.BAD_REQUEST
            )
        return app

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
        *,
        dep_map: dict[str, str] | None = None,
        resolve_cross_app_refs: bool = True,
    ) -> None:
        """Write (create or overwrite) the app config.

        ``dep_map`` / ``resolve_cross_app_refs`` only matter on the bundle path
        (§7.5.5); with their defaults every resolver behaves exactly as before.
        """
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
                tools=self._resolve_tools(
                    cfg.get("tools", []), tenant_id, warnings, dep_map=dep_map
                ),
                skills=self._resolve_skills(
                    cfg.get("skills", {}), tenant_id, warnings, dep_map=dep_map
                ),
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
                    cfg.get("sub_agents", []), warnings, workspace_id,
                    dep_map=dep_map,
                    resolve_cross_app_refs=resolve_cross_app_refs,
                ),
                routing_rules=self._resolve_routing_rules(
                    cfg.get("routing_rules"), warnings, workspace_id,
                    dep_map=dep_map,
                    resolve_cross_app_refs=resolve_cross_app_refs,
                ),
                execution_config=cfg.get("execution_config", {}),
                supervisor_config=self._resolve_supervisor_config(
                    cfg.get("supervisor_config"), workspace_id, tenant_id, warnings,
                    dep_map=dep_map,
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
            pending_config_nodes: set[str] = set()
            resolved_nodes = self._resolve_workflow_nodes(
                raw_nodes, tenant_id, workspace_id, warnings, dep_map=dep_map,
                deferred_config_nodes=pending_config_nodes,
            )
            resolved_dsl = {**dsl, "workflow": {**raw_wf, "nodes": resolved_nodes}}
            adapter = MemoryBearAdapter(
                resolved_dsl, deferred_config_node_ids=pending_config_nodes
            )
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
            # `workflow_type` 决定这份配置是对话工作流还是纯工作流
            # （`WorkflowService._supports_conversation`）。DSL 没写时按应用类型补齐：
            # 不传会落到 `create_workflow_config` 的默认值 `"workflow"`，纯工作流
            # 装到目标空间后会变成「可对话」，行为和源空间不一致。
            workflow_type = raw_wf.get("workflow_type") or app_type or AppType.WORKFLOW
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
                    workflow_type=workflow_type,
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
                    existing.workflow_type = workflow_type
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
                        workflow_type=workflow_type,
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
        self, sub_agents: list, warnings: list, workspace_id: uuid.UUID,
        *, dep_map: dict[str, str] | None = None,
        resolve_cross_app_refs: bool = True,
    ) -> list:
        if not resolve_cross_app_refs:
            # Bundle path: agent_id is already a `app:<源 app_id>` local_id and the
            # sub-agent was landed moments ago. Re-locating by id would miss
            # (or worse, match the wrong app) — resolve through dep_map only.
            return self._resolve_bundle_members(
                sub_agents, warnings, dep_map or {}, key="agent_id", label="子 Agent"
            )

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
        self, rules: list | None, warnings: list, workspace_id: uuid.UUID,
        *, dep_map: dict[str, str] | None = None,
        resolve_cross_app_refs: bool = True,
    ) -> list | None:
        if rules is None:
            return None
        if not resolve_cross_app_refs:
            return self._resolve_bundle_members(
                rules, warnings, dep_map or {}, key="target_agent_id", label="路由目标 Agent"
            )

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

    def _resolve_bundle_members(
        self, entries: list, warnings: list, dep_map: dict[str, str],
        *, key: str, label: str,
    ) -> list:
        """Bundle path: bind cross-app references strictly through `dep_map`.

        Entries whose ``key`` is not in the map are dropped with a warning — an
        unmapped local_id means the referenced app was never landed, so keeping
        the entry would leave a dangling reference. Never fall back to a name
        lookup: that is exactly the "same name, different tool" failure §7.5.3
        is about.
        """

        result = []
        for entry in entries or []:
            item = {k: v for k, v in entry.items() if k != "_ref"}
            local_id = item.get(key)
            mapped = dep_map.get(str(local_id)) if local_id else None
            if mapped is None:
                warnings.append(
                    f"{label} '{item.get('name') or local_id}' 不在本次随包内容中，已清空"
                )
                continue
            item[key] = str(mapped)
            if key == "agent_id":
                # Landed sub-agents are fresh drafts; they have no own release yet.
                item["release_policy"] = "current"
                item["release_id"] = None
            result.append(item)
        return result

    def _resolve_supervisor_config(
        self,
        sc: dict | None,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        warnings: list,
        *,
        dep_map: dict[str, str] | None = None,
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
            resolved["tools"] = self._resolve_tools(
                resolved["tools"], tenant_id, warnings, dep_map=dep_map
            )
        if resolved.get("skills"):
            resolved["skills"] = self._resolve_skills(
                resolved["skills"], tenant_id, warnings, dep_map=dep_map
            )
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
        self, nodes: list, tenant_id: uuid.UUID, workspace_id: uuid.UUID, warnings: list,
        *, dep_map: dict[str, str] | None = None,
        deferred_config_nodes: set[str] | None = None,
    ) -> list:
        """Resolve tool/KB/model references in workflow nodes; clear when unmatched.

        `deferred_config_nodes` 收集「引用未匹配、已按约定置空」的节点 id。这些节点的
        必填字段（`model_id` / `tool_id`）本来就是空着等导入后人工补配置，再跑一遍
        pydantic 校验只会把「待配置」报成 `[节点错误] ... input_value=None`。调用方拿
        这个集合让 adapter 跳过这些节点的 config 校验。
        """

        def _mark_deferred(node_key) -> None:
            if deferred_config_nodes is not None and node_key:
                deferred_config_nodes.add(str(node_key))

        resolved_nodes = []
        for node in nodes:
            node_type = node.get("type")
            config = dict(node.get("config") or {})
            node_label = node.get("name") or node.get("id")
            node_id = node.get("id")
            if node_type == NodeType.TOOL.value:
                tool_id = config.get("tool_id")
                if not tool_id:
                    # tool_id is already empty; clear without a duplicate warning
                    config["tool_id"] = None
                    config["tool_parameters"] = {}
                elif dep_map and isinstance(tool_id, str) and tool_id in dep_map:
                    # Bundle path: the node reference is a `tool:` local id whose
                    # row was just created (mcp rebuilt / custom-workflow
                    # placeholder). Bind it or the edge disappears silently.
                    mapped = dep_map.get(tool_id)
                    if mapped:
                        config["tool_id"] = str(mapped)
                    else:
                        warnings.append(
                            f"[{node_label}] 工具 '{tool_id}' 不在本次随包内容中，已置空"
                        )
                        config["tool_id"] = None
                        config["tool_parameters"] = {}
                        _mark_deferred(node_id)
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
                        _mark_deferred(node_id)
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

                # Reference mode: locate the referenced Agent app in the target
                # workspace. The bundle path rewrites `reference.app_id` to an
                # `app:<源 app id>` local id and lands that app in
                # `dependencies.apps[]`, so `dep_map` decides; only the console /
                # legacy paths look the id up directly. A miss (cross-environment,
                # reference not bundled) falls back to inline mode and clears the
                # reference; nodes and edges are kept for manual re-configuration.
                if config.get("mode") == "reference":
                    reference = dict(config.get("reference") or {})
                    ref = reference.pop("_ref", None)
                    ref_id = ref.get("id") if isinstance(ref, dict) else None
                    ref_name = ref.get("name") if isinstance(ref, dict) else None
                    raw_app_id = reference.get("app_id")
                    mapped = (
                        dep_map.get(str(raw_app_id))
                        if dep_map and isinstance(raw_app_id, str)
                        else None
                    )
                    target_app = (
                        self._locate_agent_app(mapped, workspace_id)
                        if mapped
                        else self._locate_agent_app(ref_id or raw_app_id, workspace_id)
                    )
                    if target_app:
                        reference["app_id"] = str(target_app.id)
                        reference["release_policy"] = "current"
                        reference["release_id"] = None
                        config["reference"] = reference
                    else:
                        _ref_label = ref_name or ref_id or raw_app_id
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
                            _mark_deferred(node_id)
                            if "model_ref" in config:
                                del config["model_ref"]
                    else:
                        warnings.append(f"[{node_label}] 模型未匹配，已置空，请导入后手动配置")
                        config["model_id"] = None
                        _mark_deferred(node_id)
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

    def _resolve_skills(
        self, skills: dict | None, tenant_id: uuid.UUID, warnings: list,
        *, dep_map: dict[str, str] | None = None,
    ) -> dict:
        if not skills:
            return skills or {}
        resolved_ids = []
        for entry in (skills.get("skill_ids") or []):
            # Bundle path: entries are already ``skill:<包名>`` local ids. Resolve
            # strictly through dep_map so a same-named skill in the target tenant
            # cannot be picked up instead (§7.5.3).
            if dep_map and isinstance(entry, str) and entry in dep_map:
                mapped = dep_map.get(entry)
                if mapped:
                    resolved_ids.append(str(mapped))
                else:
                    warnings.append(f"技能 '{entry}' 不在本次随包内容中，已清空")
                continue
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

    def _resolve_tools(
        self, tools: list, tenant_id: uuid.UUID, warnings: list,
        *, dep_map: dict[str, str] | None = None,
    ) -> list:
        result = []
        for t in (tools or []):
            # Bundle path: ``tool_id`` is already a ``tool:<名称>`` local id. A
            # mapped hit means the row (usually a custom/workflow placeholder) was
            # just created — bind to it instead of dropping the edge, otherwise the
            # Agent loses the dependency silently (§7.5.2).
            raw_id = t.get("tool_id")
            if dep_map and isinstance(raw_id, str) and raw_id in dep_map:
                mapped = dep_map.get(raw_id)
                entry = {k: v for k, v in t.items() if k != "_ref"}
                if mapped:
                    entry["tool_id"] = str(mapped)
                    result.append(entry)
                else:
                    warnings.append(f"工具 '{raw_id}' 不在本次随包内容中，已清空")
                continue
            ref = t.get("_ref") or ({"name": t.get("tool_id")} if t.get("tool_id") else None)
            entry = {k: v for k, v in t.items() if k != "_ref"}
            resolved_id = self._resolve_tool(ref, tenant_id, warnings)
            if resolved_id is None:
                # Outside the bundle path an unmatched tool is dropped: there is no
                # row to point at (the placeholder path always resolves through
                # ``dep_map`` above).
                continue
            entry["tool_id"] = resolved_id
            result.append(entry)
        return result

    # ==================== Cluster collection (§7.1.1) ====================

    def collect_cluster(
        self, app_id: uuid.UUID, release_id: uuid.UUID | None = None
    ) -> list[dict[str, Any]]:
        """Collect the root plus every sub-agent, one pass, no recursion.

        Reads the **raw** ``release.config.sub_agents[]``: publishing pins
        ``{agent_id, release_policy: "pinned", release_id}`` there
        (``app_service._pin_multi_agent_sub_agents``), whereas the enriched
        export shape runs through ``_agent_ref`` which drops ``release_id``. Using
        the enriched form would export each sub-agent's *current draft* instead of
        the version the cluster pinned.

        Each member carries its own pinned release; callers export that release,
        not the sub-agent's current draft, so the bundle matches what was
        reviewed.
        """

        app = self.db.query(App).filter(App.id == app_id, App.is_active.is_(True)).first()
        if not app:
            raise ResourceNotFoundException(
                "应用", str(app_id), code=BizCode.APP_NOT_FOUND
            )

        target_release_id = release_id or app.current_release_id
        if not target_release_id:
            raise BusinessException("应用尚未发布，无法导出", BizCode.RELEASE_NOT_FOUND)
        release = self.db.query(AppRelease).filter(
            AppRelease.app_id == app_id,
            AppRelease.id == target_release_id,
            AppRelease.is_active.is_(True),
        ).first()
        if not release:
            raise ResourceNotFoundException("版本", str(target_release_id))

        root_local_id = f"app:{app.id}"
        members: list[dict[str, Any]] = [
            {
                "role": "root",
                "app_id": app.id,
                "release_id": release.id,
                "name": release.name,
                "local_id": root_local_id,
                "parent_local_id": None,
                "app_type": app.type,
            }
        ]
        if app.type != AppType.MULTI_AGENT:
            return members

        for entry in (release.config or {}).get("sub_agents") or []:
            sub_release_id = entry.get("release_id")
            sub_release = (
                self.db.get(AppRelease, sub_release_id) if sub_release_id else None
            )
            if sub_release is None or not sub_release.is_active:
                raise BusinessException(
                    f"子 Agent「{entry.get('name') or entry.get('agent_id')}」的钉住版本不存在或已下线",
                    BizCode.EXTERNAL_CATALOG_INVALID,
                    data={
                        "reason": "INCOMPLETE_BUNDLE",
                        "missing": [
                            {
                                "kind": "sub_agent",
                                "name": entry.get("name"),
                                "reason": "pinned release missing or inactive",
                            }
                        ],
                    },
                )
            sub_app = self.db.query(App).filter(
                App.id == sub_release.app_id, App.is_active.is_(True)
            ).first()
            if sub_app is None:
                raise BusinessException(
                    f"子 Agent 应用不存在：{sub_release.app_id}",
                    BizCode.EXTERNAL_CATALOG_INVALID,
                    data={"reason": "INCOMPLETE_BUNDLE", "missing": []},
                )
            members.append(
                {
                    "role": "sub_agent",
                    "app_id": sub_app.id,
                    "release_id": sub_release.id,
                    "name": sub_release.name,
                    "local_id": f"app:{sub_app.id}",
                    "parent_local_id": root_local_id,
                    "app_type": sub_app.type,
                }
            )
        return members
