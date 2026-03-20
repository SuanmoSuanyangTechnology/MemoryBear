"""应用 DSL 导入导出服务"""
import uuid
import datetime
from typing import Optional

import yaml
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.models import AgentConfig, MultiAgentConfig
from app.models.app_model import App, AppType
from app.models.appshare_model import AppShare
from app.models.app_release_model import AppRelease
from app.models.knowledge_model import Knowledge
from app.models.models_model import ModelConfig
from app.models.tool_model import ToolConfig as ToolConfigModel
from app.models.skill_model import Skill
from app.models.workflow_model import WorkflowConfig
from app.services.workflow_service import WorkflowService
from app.core.workflow.adapters.memory_bear.memory_bear_adapter import MemoryBearAdapter
from app.models.memory_config_model import MemoryConfig as MemoryConfigModel


class AppDslService:

    def __init__(self, db: Session):
        self.db = db

    # ==================== 导出 ====================

    def export_dsl(self, app_id: uuid.UUID, release_id: Optional[uuid.UUID] = None) -> tuple[str, str]:
        """构建应用 DSL yaml 字符串，返回 (yaml_str, filename)"""
        app = self.db.query(App).filter(App.id == app_id, App.is_active.is_(True)).first()
        if not app:
            raise ResourceNotFoundException("应用", str(app_id))

        meta = {
            "version": settings.SYSTEM_VERSION,
            "platform": "MemoryBear",
            "exported_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
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

    def _export_release(self, app: App, release_id: uuid.UUID, meta: dict, app_meta: dict) -> tuple[str, str]:
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
            AppType.WORKFLOW: "workflow"
        }.get(app.type, "config")
        config_data = self._enrich_release_config(app.type, release.config or {})
        dsl = {**meta, "app": app_meta, config_key: config_data}
        return yaml.dump(dsl, default_flow_style=False, allow_unicode=True), f"{release.name}_v{release.version_name}.yaml"

    def _enrich_release_config(self, app_type: str, cfg: dict) -> dict:
        if app_type == AppType.AGENT:
            enriched = {**cfg}
            if "default_model_config_id" in cfg:
                enriched["default_model_config_ref"] = self._model_ref(cfg["default_model_config_id"])
            if "knowledge_retrieval" in cfg:
                enriched["knowledge_retrieval"] = self._enrich_knowledge_retrieval(cfg["knowledge_retrieval"])
            if "tools" in cfg:
                enriched["tools"] = self._enrich_tools(cfg.get("tools"))
            if "skills" in cfg:
                enriched["skills"] = self._enrich_skills(cfg.get("skills"))
            return enriched
        if app_type == AppType.MULTI_AGENT:
            enriched = {**cfg}
            if "default_model_config_id" in cfg:
                enriched["default_model_config_ref"] = self._model_ref(cfg["default_model_config_id"])
            if "master_agent_id" in cfg:
                enriched["master_agent_ref"] = self._release_ref(cfg["master_agent_id"])
            if "sub_agents" in cfg:
                enriched["sub_agents"] = self._enrich_sub_agents(cfg["sub_agents"])
            if "routing_rules" in cfg:
                enriched["routing_rules"] = [
                    {**r, "_ref": self._agent_ref(r.get("target_agent_id"))} for r in (cfg["routing_rules"] or [])
                ]
            return enriched
        return cfg

    def _export_draft(self, app: App, meta: dict, app_meta: dict) -> tuple[str, str]:
        if app.type == AppType.WORKFLOW:
            config = self.db.query(WorkflowConfig).filter(WorkflowConfig.app_id == app.id).first()
            config_data = {
                "variables": config.variables if config else [],
                "edges": config.edges if config else [],
                "nodes": config.nodes if config else [],
                "features": config.features if config else {},
                "execution_config": config.execution_config if config else {},
                "triggers": config.triggers if config else [],
            } if config else {}
            dsl = {**meta, "app": app_meta, "workflow": config_data}

        elif app.type == AppType.AGENT:
            config = self.db.query(AgentConfig).filter(AgentConfig.app_id == app.id).first()
            config_data = {
                "system_prompt": config.system_prompt if config else None,
                "model_parameters": self._to_dict(config.model_parameters) if config else None,
                "default_model_config_ref": self._model_ref(config.default_model_config_id) if config else None,
                "knowledge_retrieval": self._enrich_knowledge_retrieval(config.knowledge_retrieval) if config else None,
                "memory": config.memory if config else None,
                "variables": config.variables if config else [],
                "tools": self._enrich_tools(config.tools) if config else [],
                "skills": self._enrich_skills(config.skills) if config else {},
                "features": config.features if config else {}
            } if config else {}
            dsl = {**meta, "app": app_meta, "agent_config": config_data}

        elif app.type == AppType.MULTI_AGENT:
            config = self.db.query(MultiAgentConfig).filter(MultiAgentConfig.app_id == app.id).first()
            config_data = {
                "orchestration_mode": config.orchestration_mode if config else None,
                "master_agent_name": config.master_agent_name if config else None,
                "model_parameters": self._to_dict(config.model_parameters) if config else None,
                "default_model_config_ref": self._model_ref(config.default_model_config_id) if config else None,
                "master_agent_ref": self._release_ref(config.master_agent_id) if config else None,
                "sub_agents": self._enrich_sub_agents(config.sub_agents) if config else [],
                "routing_rules": [
                    {**r, "_ref": self._agent_ref(r.get("target_agent_id"))} for r in (config.routing_rules or [])
                ] if config else [],

                "execution_config": config.execution_config if config else {},
                "aggregation_strategy": config.aggregation_strategy if config else "merge",
            } if config else {}
            dsl = {**meta, "app": app_meta, "multi_agent_config": config_data}

        else:
            raise BusinessException(f"不支持的应用类型: {app.type}", BizCode.BAD_REQUEST)

        return yaml.dump(dsl, default_flow_style=False, allow_unicode=True), f"{app.name}.yaml"

    def _to_dict(self, value):
        """将 Pydantic 对象转为普通 dict，供 yaml.dump 安全序列化"""
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return value

    def _model_ref(self, model_config_id) -> Optional[dict]:
        if not model_config_id:
            return None
        m = self.db.query(ModelConfig).filter(ModelConfig.id == model_config_id).first()
        return {"id": str(model_config_id), "name": m.name, "provider": m.provider, "type": m.type} if m else {"id": str(model_config_id)}

    def _kb_ref(self, kb_id) -> Optional[dict]:
        if not kb_id:
            return None
        kb = self.db.query(Knowledge).filter(Knowledge.id == kb_id).first()
        return {"id": str(kb_id), "name": kb.name} if kb else {"id": str(kb_id)}

    def _tool_ref(self, tool_id) -> Optional[dict]:
        if not tool_id:
            return None
        t = self.db.query(ToolConfigModel).filter(ToolConfigModel.id == tool_id).first()
        return {"id": str(tool_id), "name": t.name, "tool_type": t.tool_type} if t else {"id": str(tool_id)}

    def _enrich_knowledge_retrieval(self, kr: Optional[dict]) -> Optional[dict]:
        if not kr:
            return kr
        kbs = [{**kb, "_ref": self._kb_ref(kb.get("kb_id"))} for kb in kr.get("knowledge_bases", [])]
        return {**kr, "knowledge_bases": kbs}

    def _enrich_tools(self, tools: list) -> list:
        return [{**t, "_ref": self._tool_ref(t.get("tool_id"))} for t in (tools or [])]

    def _skill_ref(self, skill_id) -> Optional[dict]:
        if not skill_id:
            return None
        s = self.db.query(Skill).filter(Skill.id == skill_id).first()
        return {"id": str(skill_id), "name": s.name} if s else {"id": str(skill_id)}

    def _enrich_skills(self, skills: Optional[dict]) -> Optional[dict]:
        if not skills:
            return skills
        skill_ids = skills.get("skill_ids", [])
        enriched_ids = [
            {"id": sid, "_ref": self._skill_ref(sid)}
            for sid in (skill_ids or [])
        ]
        return {**skills, "skill_ids": enriched_ids}

    def _agent_ref(self, agent_id) -> Optional[dict]:
        if not agent_id:
            return None
        a = self.db.query(App).filter(App.id == agent_id).first()
        return {"id": str(agent_id), "name": a.name} if a else {"id": str(agent_id)}

    def _release_ref(self, release_id) -> Optional[dict]:
        if not release_id:
            return None
        r = self.db.query(AppRelease).filter(AppRelease.id == release_id).first()
        return {"id": str(release_id), "name": r.name, "version": r.version, "app_id": str(r.app_id)} if r else {"id": str(release_id)}

    def _enrich_sub_agents(self, sub_agents: list) -> list:
        return [{**s, "_ref": self._agent_ref(s.get("agent_id"))} for s in (sub_agents or [])]

    # ==================== 导入 ====================

    def import_dsl(
        self,
        dsl: dict,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> tuple[App, list[str]]:
        """解析 DSL，创建应用及配置，返回 (new_app, warnings)"""
        app_meta = dsl.get("app", {})
        app_type = app_meta.get("type")
        if app_type not in (AppType.AGENT, AppType.MULTI_AGENT, AppType.WORKFLOW):
            raise BusinessException(f"不支持的应用类型: {app_type}", BizCode.BAD_REQUEST)

        warnings: list[str] = []
        now = datetime.datetime.now()

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

        if app_type == AppType.AGENT:
            cfg = dsl.get("agent_config") or {}
            self.db.add(AgentConfig(
                id=uuid.uuid4(),
                app_id=new_app.id,
                system_prompt=cfg.get("system_prompt"),
                model_parameters=cfg.get("model_parameters"),
                default_model_config_id=self._resolve_model(cfg.get("default_model_config_ref"), tenant_id, warnings),
                knowledge_retrieval=self._resolve_knowledge_retrieval(cfg.get("knowledge_retrieval"), workspace_id, warnings),
                memory=self._resolve_memory(cfg.get("memory"), workspace_id, warnings),
                variables=cfg.get("variables", []),
                tools=self._resolve_tools(cfg.get("tools", []), tenant_id, warnings),
                skills=self._resolve_skills(cfg.get("skills", {}), tenant_id, warnings),
                features=cfg.get("features", {}),
                is_active=True,
                created_at=now,
                updated_at=now,
            ))

        elif app_type == AppType.MULTI_AGENT:
            cfg = dsl.get("multi_agent_config") or {}
            self.db.add(MultiAgentConfig(
                id=uuid.uuid4(),
                app_id=new_app.id,
                orchestration_mode=cfg.get("orchestration_mode", "collaboration"),
                master_agent_name=cfg.get("master_agent_name"),
                model_parameters=cfg.get("model_parameters"),
                default_model_config_id=self._resolve_model(cfg.get("default_model_config_ref"), tenant_id, warnings),
                master_agent_id=self._resolve_release(cfg.get("master_agent_ref"), warnings),
                sub_agents=self._resolve_sub_agents(cfg.get("sub_agents", []), warnings),
                routing_rules=self._resolve_routing_rules(cfg.get("routing_rules"), warnings),
                execution_config=cfg.get("execution_config", {}),
                aggregation_strategy=cfg.get("aggregation_strategy", "merge"),
                is_active=True,
                created_at=now,
                updated_at=now,
            ))

        elif app_type == AppType.WORKFLOW:
            adapter = MemoryBearAdapter(dsl)
            if not adapter.validate_config():
                raise BusinessException("工作流配置格式无效", BizCode.BAD_REQUEST)
            result = adapter.parse_workflow()
            for e in result.errors:
                warnings.append(f"[节点错误] {e.node_name or e.node_id}: {e.detail}")
            for w in result.warnings:
                warnings.append(f"[节点警告] {w.node_name or w.node_id}: {w.detail}")
            wf = dsl.get("workflow") or {}
            WorkflowService(self.db).create_workflow_config(
                app_id=new_app.id,
                nodes=[n.model_dump() for n in result.nodes],
                edges=[e.model_dump() for e in result.edges],
                variables=[v.model_dump() for v in result.variables],
                execution_config=wf.get("execution_config", {}),
                features=wf.get("features", {}),
                triggers=wf.get("triggers", []),
                validate=False,
            )

        self.db.commit()
        self.db.refresh(new_app)
        return new_app, warnings

    def _unique_app_name(self, name: str, workspace_id: uuid.UUID, app_type: AppType) -> str:
        """生成唯一应用名称，同时检查本空间自有应用和共享到本空间的应用"""
        # 本空间自有应用名
        existing = {r[0] for r in self.db.query(App.name).filter(
            App.workspace_id == workspace_id,
            App.type == app_type,
            App.is_active.is_(True)
        ).all()}
        # 共享到本空间的应用名
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

    def _resolve_model(self, ref: Optional[dict], tenant_id: uuid.UUID, warnings: list) -> Optional[uuid.UUID]:
        if not ref:
            return None
        q = self.db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.name == ref.get("name"),
            ModelConfig.is_active.is_(True)
        )
        if ref.get("provider"):
            q = q.filter(ModelConfig.provider == ref["provider"])
        if ref.get("type"):
            q = q.filter(ModelConfig.type == ref["type"])
        m = q.first()
        if not m:
            warnings.append(f"模型 '{ref.get('name')}' 未匹配，已置空，请导入后手动配置")
        return m.id if m else None

    def _resolve_kb(self, ref: Optional[dict], workspace_id: uuid.UUID, warnings: list) -> Optional[str]:
        if not ref:
            return None
        kb = self.db.query(Knowledge).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.name == ref.get("name")
        ).first()
        if not kb:
            warnings.append(f"知识库 '{ref.get('name')}' 未匹配，已置空，请导入后手动配置")
        return str(kb.id) if kb else None

    def _resolve_tool(self, ref: Optional[dict], tenant_id: uuid.UUID, warnings: list) -> Optional[str]:
        if not ref:
            return None
        q = self.db.query(ToolConfigModel).filter(
            ToolConfigModel.tenant_id == tenant_id,
            ToolConfigModel.name == ref.get("name")
        )
        if ref.get("tool_type"):
            q = q.filter(ToolConfigModel.tool_type == ref["tool_type"])
        t = q.first()
        if not t:
            warnings.append(f"工具 '{ref.get('name')}' 未匹配，已置空，请导入后手动配置")
        return str(t.id) if t else None

    def _resolve_release(self, ref: Optional[dict], warnings: list) -> Optional[uuid.UUID]:
        if not ref:
            return None
        r = self.db.query(AppRelease).filter(
            AppRelease.app_id == ref.get("app_id"),
            AppRelease.version == ref.get("version"),
            AppRelease.is_active.is_(True)
        ).first()
        if not r:
            warnings.append(f"主 Agent 发布版本 '{ref.get('name')}' 未匹配，已置空，请导入后手动配置")
        return r.id if r else None

    def _resolve_sub_agents(self, sub_agents: list, warnings: list) -> list:
        result = []
        for s in (sub_agents or []):
            ref = s.get("_ref")
            entry = {k: v for k, v in s.items() if k != "_ref"}
            if ref:
                a = self.db.query(App).filter(App.name == ref.get("name"), App.is_active.is_(True)).first()
                if not a:
                    warnings.append(f"子 Agent '{ref.get('name')}' 未匹配，已置空，请导入后手动配置")
                entry["agent_id"] = str(a.id) if a else None
            result.append(entry)
        return result

    def _resolve_routing_rules(self, rules: Optional[list], warnings: list) -> Optional[list]:
        if rules is None:
            return None
        result = []
        for r in rules:
            ref = r.get("_ref")
            entry = {k: v for k, v in r.items() if k != "_ref"}
            if ref:
                a = self.db.query(App).filter(App.name == ref.get("name"), App.is_active.is_(True)).first()
                if not a:
                    warnings.append(f"路由目标 Agent '{ref.get('name')}' 未匹配，已置空，请导入后手动配置")
                entry["target_agent_id"] = str(a.id) if a else None
            result.append(entry)
        return result

    def _resolve_knowledge_retrieval(self, kr: Optional[dict], workspace_id: uuid.UUID, warnings: list) -> Optional[dict]:
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
        return {k: v for k, v in kr.items() if k != "knowledge_bases"} | {"knowledge_bases": resolved_kbs}

    def _resolve_memory(self, memory: Optional[dict], workspace_id: uuid.UUID, warnings: list) -> Optional[dict]:
        if not memory:
            return memory
        config_id = memory.get("memory_config_id") or memory.get("memory_content")
        if not config_id:
            return memory
        try:
            config_uuid = uuid.UUID(str(config_id))
        except (ValueError, AttributeError):
            exists = self.db.query(MemoryConfigModel).filter(
                MemoryConfigModel.config_id_old == int(config_id),
                MemoryConfigModel.workspace_id == workspace_id
            ).first()
            if not exists:
                warnings.append(f"记忆配置 '{config_id}' 未匹配，已置空，请导入后手动配置")
                return {**memory, "memory_config_id": None, "enabled": False}
            return memory
        exists = self.db.query(MemoryConfigModel).filter(
            MemoryConfigModel.config_id == config_uuid,
            MemoryConfigModel.workspace_id == workspace_id
        ).first()
        if not exists:
            warnings.append(f"记忆配置 '{config_id}' 未匹配，已置空，请导入后手动配置")
            return {**memory, "memory_config_id": None, "enabled": False}
        return memory

    def _resolve_skills(self, skills: Optional[dict], tenant_id: uuid.UUID, warnings: list) -> dict:
        if not skills:
            return skills or {}
        resolved_ids = []
        for entry in (skills.get("skill_ids") or []):
            # entry 可能是 {"id": "...", "_ref": {...}} 或直接是字符串
            if isinstance(entry, dict):
                ref = entry.get("_ref") or ({"name": None, "id": entry.get("id")} if entry.get("id") else None)
                skill_id = self._resolve_skill(ref, tenant_id, warnings)
            else:
                skill_id = self._resolve_skill({"id": str(entry)}, tenant_id, warnings)
            if skill_id:
                resolved_ids.append(str(skill_id))
        return {**{k: v for k, v in skills.items() if k != "skill_ids"}, "skill_ids": resolved_ids}

    def _resolve_skill(self, ref: Optional[dict], tenant_id: uuid.UUID, warnings: list) -> Optional[str]:
        if not ref:
            return None
        # 先按 id 匹配
        if ref.get("id"):
            try:
                s = self.db.query(Skill).filter(
                    Skill.id == uuid.UUID(str(ref["id"])),
                    Skill.tenant_id == tenant_id
                ).first()
                if s:
                    return str(s.id)
            except Exception:
                pass
        # 再按名称匹配
        if ref.get("name"):
            s = self.db.query(Skill).filter(
                Skill.name == ref["name"],
                Skill.tenant_id == tenant_id
            ).first()
            if s:
                return str(s.id)
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
