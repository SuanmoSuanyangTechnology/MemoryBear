"""Bundle landing for cross-tenant installs (§7.4 / §7.5.5).

One transaction, one order, no half-landed cluster:

```
① plugins / skills   (zip → import_package(commit=False))
② tools              (mcp rebuilt with cleared credentials;
                      custom / workflow → placeholder row only)
③ record local_dep_map: bundle ref → new local id
④ apps               (sub-agents first, then root, import_dsl(commit=False,
                      dep_map=…, resolve_cross_app_refs=False))
⑤ back-fill builtin tool mapping / cluster references
⑥ commit
```

Why this shape:

* **`commit=False` everywhere.** The landing must roll back as a unit; an inner
  commit puts already-written apps/skills beyond the reach of the caller's
  `rollback()` and leaves a half cluster.
* **`dep_map` everywhere.** Refs in the bundle are `tool:` / `skill:` / `app:`
  local ids. Looking them up by *name* in the target tenant would bind to an
  unrelated same-named resource (§7.5.3) — a silent, wrong dependency edge.
* **`resolve_cross_app_refs=False`.** Sub-agents are landed moments earlier in
  this same transaction; `_locate_agent_app` would look them up by workspace and
  drop every entry it cannot see, turning a cluster into an empty shell.
* **Placeholders are rows, not absences.** `custom` / `workflow` tools carry no
  configuration across tenants, but the dependency edge must still exist, so the
  Agent gets a `ToolConfig(status=unconfigured)` to point at instead of losing
  the tool silently.
"""
from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import threading
import uuid
import zipfile
from typing import Any, Callable, Coroutine

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models import (
    App,
    MCPToolConfig,
    SkillPackage,
    ToolConfig,
)
from ..models.app_model import AppType
from ..models.tool_model import ToolStatus, ToolType
from ..repositories.skill_package_repository import SkillPackageRepository
from ..storage import StorageFactory
from ..utils.datetime_utils import utcnow_naive
from .app_dsl_service import AppDslService
from .asset_bundle import BundleError, parse_bundle
from .skill_package_service import SkillPackageService

logger = logging.getLogger(__name__)

PLACEHOLDER_TOOL_TYPES = frozenset({ToolType.CUSTOM.value, ToolType.WORKFLOW.value})


def _run_async(coro_factory: "Callable[[], Coroutine]"):
    """Run a coroutine from sync code: direct when no loop, else a private thread."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
            box["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


class BundleInstallService:
    """Land one verified bundle into a tenant / workspace."""

    def __init__(self, db: Session, dsl_service: AppDslService | None = None):
        self.db = db
        self.dsl_service = dsl_service or AppDslService(db)
        # 本次安装的市场来源 `{resource_id, version}`；空字典表示不是市场安装。
        # 两个入口都会在开头覆盖它，这里给个初值只是为了不依赖调用顺序。
        self._origin: dict[str, str] = {}

    # ==================== entry ====================

    def install(
            self,
            *,
            manifest: dict[str, Any],
            tenant_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
            user_id: uuid.UUID | None,
            commit: bool = True,
            market_origin: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Land every dependency then the application cluster; returns the report.

        ``missing`` holds one `MissingDependency` per degraded item; ``blocking``
        ones are raised as 422 before anything is written.
        ``market_origin`` = ``{resource_id, version}`` when the bundle came from the
        market, so every landed row records where its copy came from (§11.2).
        """

        self._origin = market_origin or {}

        try:
            parse_bundle(manifest)
        except BundleError as error:
            raise BusinessException(
                str(error), BizCode.EXTERNAL_CATALOG_INVALID, data={"reason": "INVALID_BUNDLE"}
            ) from error

        dependencies = manifest.get("dependencies") or {}
        bindings = manifest.get("bindings") or []
        payload = manifest.get("payload") or {}
        app_type = payload.get("app_type")
        if app_type not in (AppType.AGENT, AppType.MULTI_AGENT, AppType.WORKFLOW,
                            AppType.PURE_WORKFLOW):
            raise BusinessException(
                f"不支持的应用类型: {app_type}", BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "INVALID_BUNDLE"},
            )

        apps = dependencies.get("apps") or []
        if app_type == AppType.MULTI_AGENT:
            self._assert_cluster_complete(payload.get("config") or {}, apps)

        dep_map: dict[str, str] = {}
        missing: list[dict[str, Any]] = []

        # ① skills / plugins（随包依赖：`_origin` 是应用的资源 id，不是技能的）
        self._land_skills(dependencies, tenant_id, dep_map, missing)
        # ② tools
        self._land_tools(dependencies, tenant_id, dep_map, missing)
        # ③ cluster sub-agents, then the root
        root_app = self._land_apps(
            manifest=manifest,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            dep_map=dep_map,
            missing=missing,
        )

        if commit:
            self.db.commit()
        else:
            self.db.flush()

        pending_bindings = [
            {
                "kind": binding.get("kind"),
                "name": binding.get("name"),
                "ref": binding.get("ref"),
                "required": binding.get("required", True),
            }
            for binding in bindings
            if binding.get("required", True)
        ]
        return {
            "app": root_app,
            "local_dep_map": dep_map,
            "missing_report": missing,
            "pending_bindings": pending_bindings,
            "result": "partial" if missing else "success",
        }

    def install_dependency(
            self,
            *,
            manifest: dict[str, Any],
            tenant_id: uuid.UUID,
            commit: bool = True,
            market_origin: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Land one **tool / skill_package** bundle (no application involved).

        市场的技能与工具 tab 是独立条目，装它时不该先造一个应用。落地逻辑与
        应用包内的同名依赖完全同源（同一个 `_land_tools` / `_land_skills`），
        否则「随包装下来的技能」和「单独装的技能」会在目标租户里长成两种样子。
        """

        self._origin = market_origin or {}
        try:
            parse_bundle(manifest)
        except BundleError as error:
            raise BusinessException(
                str(error), BizCode.EXTERNAL_CATALOG_INVALID, data={"reason": "INVALID_BUNDLE"}
            ) from error

        resource_type = manifest.get("resource_type")
        payload = manifest.get("payload") or {}
        dependencies = manifest.get("dependencies") or {}
        dep_map: dict[str, str] = {}
        missing: list[dict[str, Any]] = []

        if resource_type == "tool":
            if str(payload.get("tool_type")) != str(ToolType.MCP):
                raise BusinessException(
                    "工具市场只提供 MCP 工具", BizCode.EXTERNAL_CATALOG_INVALID,
                    data={"reason": "UNSUPPORTED_TOOL_TYPE"},
                )
            entry = {
                # 独立条目的 ref 用市场资源 id：落地后没有应用引用来对照它
                "ref": f"tool:{manifest['source']['local_resource_id']}",
                "name": payload.get("name"),
                "tool_type": payload.get("tool_type"),
                "payload": payload.get("config") or {},
            }
            self._land_tools(
                {"tools": [entry], "skills": [], "plugins": []},
                tenant_id, dep_map, missing,
            )
            if not dep_map.get(entry["ref"]):
                raise BusinessException(
                    "工具落地失败", BizCode.EXTERNAL_CATALOG_INVALID,
                    data={"reason": "TOOL_LANDING_FAILED", "missing": missing},
                )
            local_id = dep_map[entry["ref"]]
            local_name = self.db.get(ToolConfig, uuid.UUID(local_id)).name
        elif resource_type == "skill_package":
            # 单独装的这一个技能/插件，`_origin` 指的就是它自己 → 能写全 source_ref
            self._land_skills(
                _skill_dependencies(payload, dependencies),
                tenant_id, dep_map, missing,
                own_market_ref=True,
            )
            local_id, local_name = self._skill_result(
                tenant_id=tenant_id,
                kind=payload.get("kind"),
                name=payload.get("name"),
            )
        else:
            raise BusinessException(
                f"该资源类型不支持单独安装: {resource_type}",
                BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "UNSUPPORTED_RESOURCE_TYPE"},
            )

        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return {
            "resource_type": resource_type,
            "local_resource_id": local_id,
            "local_name": local_name,
            "missing_report": missing,
            "result": "partial" if missing else "success",
        }

    def _skill_result(
            self,
            *,
            tenant_id: uuid.UUID,
            kind: str | None,
            name: str | None,
    ) -> tuple[str | None, str | None]:
        """落地后的本地 id 与展示名。

        `dep_map` 的值是 `skills` 行 id（Agent 绑定用），而安装响应要的是技能包/
        插件这一层，所以回包表按 `(tenant, name)` 取——落地上一步刚建好它，
        同租户同 name 唯一（`uq_skill_pkg_tenant_name`），不会取错。
        """

        repo = SkillPackageRepository(self.db)
        if kind == "plugin":
            plugin = repo.get_plugin_by_name(tenant_id, str(name or ""))
            if plugin is None:
                return None, None
            return str(plugin.id), plugin.name

        package = repo.get_by_name(tenant_id, str(name or ""))
        if package is None:
            return None, None
        return str(package.id), (package.display_name or package.name)

    # ==================== cluster gate ====================

    @staticmethod
    def _assert_cluster_complete(config: dict[str, Any], apps: list[dict[str, Any]]) -> None:
        """Every pinned sub-agent must be inside the bundle (§6.3 rule 5)."""

        landed = {str(item.get("local_id")) for item in apps}
        absent = [
            entry.get("name") or entry.get("agent_id")
            for entry in (config.get("sub_agents") or [])
            if str(entry.get("agent_id")) not in landed
        ]
        if absent:
            raise BusinessException(
                "应用包的子 Agent 不完整，无法安装：" + "、".join(str(n) for n in absent),
                BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "CLUSTER_INCOMPLETE", "missing": absent},
            )

    # ==================== ① skills / plugins ====================

    def _land_skills(
            self,
            dependencies: dict[str, Any],
            tenant_id: uuid.UUID,
            dep_map: dict[str, str],
            missing: list[dict[str, Any]],
            *,
            own_market_ref: bool = False,
    ) -> None:
        """Each plugin lands as one multi-member zip; standalone skills land alone.

        A skill that already exists in the target tenant is *overwritten* when its
        `call_name` matches (that is the whole idempotency key of the skill
        runtime); a package-name clash with a different call_name is degraded
        instead of aborting the install.

        ``own_market_ref``: 技能包没有 `origin` 列，来源靠 `source_type` /
        `source_ref` 两列表达（§11.2）。 ``True`` 只在「从市场技能/工具 tab 直接
        装这一个技能」时为真——那时 `_origin` 里的资源 id 就是它自己的，能写全
        `<resource_id>@<version>`。随应用包下来时包里只有 `skill:<name>` 引用，
        市场资源 id 要等投稿时才产生，装的时候拿不到，于是只写
        `source_type=market`（照样被「市场副本不可再投稿」拦下），不去编造一个
        查不到的资源 id。
        """

        # 市场来源（§11.2）：两列一起传，避免上次只写 source_ref 导致
        # `market_source_ref()` 因 source_type 仍是 imported 而判成本地。
        source_type = "market" if self._origin else "imported"
        source_ref = self._market_source_ref() if own_market_ref else None

        plugin_meta = {
            str(plugin.get("ref")): plugin for plugin in (dependencies.get("plugins") or [])
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in dependencies.get("skills") or []:
            ref = str(entry.get("ref"))
            plugin_ref = (entry.get("payload") or {}).get("plugin_ref")
            grouped.setdefault(str(plugin_ref) if plugin_ref else "", []).append(entry)

        for plugin_ref, entries in grouped.items():
            member_refs: dict[str, dict[str, Any]] = {}
            try:
                archive = self._build_skill_zip(entries, nested=bool(plugin_ref), member_refs=member_refs)
                plugin_row = plugin_meta.get(plugin_ref) if plugin_ref else None
                response = SkillPackageService.import_package(
                    self.db,
                    tenant_id,
                    zip_bytes=archive,
                    plugin_name=(plugin_row or {}).get("name"),
                    plugin_description=((plugin_row or {}).get("payload") or {}).get("description"),
                    source_type=source_type,
                    source_ref=source_ref,
                    conflict_strategy="rename",
                    commit=False,
                )
            except BusinessException as error:
                for entry in entries:
                    missing.append(_missing("skill", entry.get("name"), str(error)))
                continue

            for member in response.get("members") or []:
                entry = member_refs.get(str(member.get("call_name")))
                if entry is None:
                    continue
                if member.get("package_id"):
                    dep_map[str(entry.get("ref"))] = str(member.get("skill_id") or member["package_id"])
                else:
                    missing.append(
                        _missing("skill", entry.get("name"), member.get("skip_reason") or "跳过")
                    )
            if plugin_ref and response.get("plugin_id"):
                dep_map[plugin_ref] = str(response["plugin_id"])

    def _build_skill_zip(
            self,
            entries: list[dict[str, Any]],
            *,
            nested: bool,
            member_refs: dict[str, dict[str, Any]],
    ) -> bytes:
        """Rebuild a standard pack zip from the bundle's `files[]` object keys."""

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry in entries:
                payload = entry.get("payload") or {}
                call_name = payload.get("call_name")
                package_name = _slug(str(entry.get("name") or call_name or "skill"))
                root = f"{package_name}/" if nested else ""
                if call_name:
                    member_refs[str(call_name)] = entry
                files = entry.get("files") or []
                if not files:
                    raise BusinessException(
                        f"技能「{entry.get('display_name') or entry.get('name')}」没有随包文件",
                        BizCode.EXTERNAL_CATALOG_INVALID,
                        data={"reason": "SKILL_FILES_MISSING"},
                    )
                for file_entry in files:
                    rel_path = str(file_entry.get("path") or "")
                    content = self._read_object(file_entry.get("object_key"))
                    info = zipfile.ZipInfo(f"{root}{rel_path}", date_time=(2026, 1, 1, 0, 0, 0))
                    info.flag_bits |= 0x800
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, content)
        return buffer.getvalue()

    @staticmethod
    def _read_object(object_key: Any) -> bytes:
        key = str(object_key or "")
        if not key:
            raise BusinessException(
                "技能文件缺少 object_key", BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "SKILL_FILES_MISSING"},
            )
        storage = StorageFactory.get_storage()
        return _run_async(lambda: storage.download(key))

    # ==================== ② tools ====================

    def _land_tools(
            self,
            dependencies: dict[str, Any],
            tenant_id: uuid.UUID,
            dep_map: dict[str, str],
            missing: list[dict[str, Any]],
    ) -> None:
        """`mcp` is rebuilt (credentials cleared); `custom` / `workflow` get a row only."""

        for entry in dependencies.get("tools") or []:
            ref = str(entry.get("ref"))
            tool_type = str(entry.get("tool_type") or "")
            payload = entry.get("payload") or {}

            reuse = payload.get("reuse") or {}
            if reuse:
                # 已在目标市场装过同一资源：直接复用本地副本，不重复落地。
                local_id = self._find_external_tool(reuse.get("market_resource_id"))
                if local_id:
                    dep_map[ref] = local_id
                    continue

            if tool_type == ToolType.MCP.value:
                dep_map[ref] = self._land_mcp(entry, tenant_id, missing)
            elif tool_type in PLACEHOLDER_TOOL_TYPES:
                dep_map[ref] = self._land_placeholder(entry, tenant_id)
            elif tool_type == ToolType.BUILTIN.value:
                # builtin 不随包：落地时按 tool_class 在目标租户找同类，找不到就置空
                continue
            else:
                missing.append(_missing("tool", entry.get("name"), f"未知工具类型 {tool_type}"))

    def _land_mcp(
            self,
            entry: dict[str, Any],
            tenant_id: uuid.UUID,
            missing: list[dict[str, Any]],
    ) -> str:
        payload = entry.get("payload") or {}
        server_url = payload.get("server_url")
        if not server_url:
            missing.append(_missing("tool", entry.get("name"), "缺少 server_url"))
            return ""

        now = utcnow_naive()
        row = ToolConfig(
            id=uuid.uuid4(),
            name=self._unique_tool_name(str(entry.get("name") or "MCP 工具"), ToolType.MCP.value, tenant_id),
            description=None,
            # 工具按产品口径没有图标入口（§6.1.1 口径 5）
            icon=None,
            tool_type=ToolType.MCP.value,
            tenant_id=tenant_id,
            # 凭据随包剥离 → 结构就绪但不可用，等用户补填
            status=ToolStatus.UNCONFIGURED.value,
            config_data={
                "server_url": server_url,
                "connection_config": {},
                "available_tools": payload.get("available_tools") or [],
            },
            tags=[],
            is_active=True,
            created_at=now,
            updated_at=now,
            **self._origin_columns(),
        )
        self.db.add(row)
        self.db.flush()
        self.db.add(
            MCPToolConfig(
                id=row.id,
                server_url=server_url,
                connection_config={},
                available_tools=payload.get("available_tools") or [],
            )
        )
        self.db.flush()
        return str(row.id)

    def _land_placeholder(self, entry: dict[str, Any], tenant_id: uuid.UUID) -> str:
        """One `ToolConfig` row, no sub-table: "a tool of this type belongs here".

        No `schema_content` / `base_url` / `auth_config` / `workflow_config_id` is
        carried: those point at the source tenant's systems and would be dead
        references. `config_data.placeholder` is what the UI keys the
        "待配置" badge off (`ToolInfo.is_placeholder`).
        """

        now = utcnow_naive()
        tool_type = str(entry.get("tool_type"))
        row = ToolConfig(
            id=uuid.uuid4(),
            name=self._unique_tool_name(str(entry.get("name") or "未配置工具"), tool_type, tenant_id),
            description=None,
            icon=None,
            tool_type=tool_type,
            tenant_id=tenant_id,
            status=ToolStatus.UNCONFIGURED.value,
            config_data={"placeholder": True},
            tags=[],
            is_active=True,
            created_at=now,
            updated_at=now,
            **self._origin_columns(),
        )
        self.db.add(row)
        self.db.flush()
        return str(row.id)

    def _market_source_ref(self) -> str | None:
        """`<market_resource_id>@<version>`（§11.2，反解按最后一个 `@` 切）。"""

        resource_id = self._origin.get("resource_id")
        if not resource_id:
            return None
        return f"{resource_id}@{self._origin.get('version') or ''}"

    def _origin_columns(self) -> dict[str, Any]:
        """Provenance columns for a row that came from the market (§11.2)."""

        if not self._origin:
            return {}
        return {
            "origin": "external",
            "external_source": "community_market",
            "external_resource_id": self._origin.get("resource_id"),
            "external_version": self._origin.get("version"),
        }

    def _unique_tool_name(self, name: str, tool_type: str, tenant_id: uuid.UUID) -> str:
        # (tenant, name, tool_type) is not unique in the schema, but a duplicate
        # name makes the tool picker ambiguous in the target tenant.
        taken = {
            row[0]
            for row in self.db.execute(
                select(ToolConfig.name).where(
                    ToolConfig.tenant_id == tenant_id,
                    ToolConfig.tool_type == tool_type,
                    ToolConfig.is_active.is_(True),
                )
            ).all()
        }
        if name not in taken:
            return name
        counter = 2
        while f"{name} ({counter})" in taken:
            counter += 1
        return f"{name} ({counter})"

    def _find_external_tool(self, market_resource_id: Any) -> str | None:
        """Reuse pointer: the tool already landed from the same market resource."""

        if not market_resource_id:
            return None
        row = self.db.execute(
            select(ToolConfig).where(
                ToolConfig.external_resource_id == str(market_resource_id),
                ToolConfig.is_active.is_(True),
            )
        ).scalars().first()
        return str(row.id) if row is not None else None

    # ==================== ③ apps ====================

    def _land_apps(
            self,
            *,
            manifest: dict[str, Any],
            tenant_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
            user_id: uuid.UUID | None,
            dep_map: dict[str, str],
            missing: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Sub-agents first (they need ids), root last; all in one workspace."""

        if workspace_id is None:
            raise BusinessException(
                "安装应用必须指定目标空间", BizCode.INVALID_PARAMETER,
                data={"reason": "WORKSPACE_REQUIRED"},
            )

        payload = manifest.get("payload") or {}
        dependencies = manifest.get("dependencies") or {}
        display = manifest.get("display") or {}
        owner_id = user_id or uuid.uuid4()

        for member in dependencies.get("apps") or []:
            app, _warnings = self._import_member(
                member=member,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=owner_id,
                dep_map=dep_map,
                display_name=member.get("name"),
                description=None,
            )
            dep_map[str(member.get("local_id"))] = str(app.id)

        root, warnings = self._import_member(
            member={
                "name": display.get("name"),
                "app_type": payload.get("app_type"),
                "config": payload.get("config") or {},
                "bindings": manifest.get("bindings") or [],
                "dependencies": dependencies,
            },
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=owner_id,
            dep_map=dep_map,
            display_name=display.get("name"),
            description=display.get("summary"),
            # 只有根应用对应一个市场资源；子 Agent 是同一份包的内部成员，
            # 没有自己的 market_resource_id，标记成 external 会让溯源指向错的行
            origin_columns=self._origin_columns(),
        )
        missing.extend(
            _missing("app", display.get("name"), warning) for warning in warnings
        )
        return {
            "id": str(root.id),
            "name": root.name,
            "app_type": root.type,
            "workspace_id": str(root.workspace_id),
        }

    def _import_member(
            self,
            *,
            member: dict[str, Any],
            tenant_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: uuid.UUID,
            dep_map: dict[str, str],
            display_name: str | None,
            description: str | None,
            origin_columns: dict[str, Any] | None = None,
    ) -> tuple[App, list[str]]:
        config = dict(member.get("config") or {})
        app_type = member.get("app_type") or AppType.AGENT
        model_ref = _model_ref_from_bindings(member)

        if app_type == AppType.AGENT:
            agent_config = {
                "system_prompt": config.get("system_prompt"),
                "model_parameters": config.get("model_parameters"),
                "default_model_config_ref": model_ref,
                "knowledge_retrieval": config.get("knowledge_retrieval"),
                "memory": config.get("memory"),
                "variables": config.get("variables") or [],
                "tools": config.get("tools") or [],
                "skills": config.get("skills") or {},
                "features": config.get("features") or {},
            }
            dsl = {
                "app": _app_meta(display_name or member.get("name"), description, app_type),
                "agent_config": agent_config,
            }
        elif app_type == AppType.MULTI_AGENT:
            multi_agent_config = {
                "orchestration_mode": config.get("orchestration_mode") or "supervisor_loop",
                "master_agent_name": config.get("master_agent_name"),
                "model_parameters": config.get("model_parameters"),
                "default_model_config_ref": model_ref,
                # 主 Agent 的 release 属于源应用；落地后由发布重新指向自身版本
                "master_agent_ref": None,
                "sub_agents": config.get("sub_agents") or [],
                "routing_rules": config.get("routing_rules") or [],
                "execution_config": config.get("execution_config") or {},
                "supervisor_config": config.get("supervisor_config"),
                "aggregation_strategy": config.get("aggregation_strategy") or "merge",
            }
            dsl = {
                "app": _app_meta(display_name or member.get("name"), description, app_type),
                "multi_agent_config": multi_agent_config,
            }
        else:
            dsl = {
                "app": _app_meta(display_name or member.get("name"), description, app_type),
                "workflow": {
                    "nodes": config.get("nodes") or [],
                    "edges": config.get("edges") or [],
                    "variables": config.get("variables") or [],
                    "environment_variables": config.get("environment_variables") or [],
                    "execution_config": config.get("execution_config") or {},
                    "features": config.get("features") or {},
                    "triggers": config.get("triggers") or [],
                    "workflow_type": config.get("workflow_type") or AppType.WORKFLOW,
                },
            }

        return self.dsl_service.import_dsl(
            dsl,
            workspace_id,
            tenant_id,
            user_id,
            commit=False,
            dep_map=dep_map,
            resolve_cross_app_refs=False,
            origin_columns=origin_columns,
        )


def _app_meta(name: str | None, description: str | None, app_type: str) -> dict[str, Any]:
    return {
        "name": name or "导入应用",
        "description": description,
        "icon": None,
        "icon_type": None,
        "type": app_type,
        "tags": [],
    }


def _model_ref_from_bindings(member: dict[str, Any]) -> dict[str, Any] | None:
    """A model never crosses tenants: the binding only carries the name to re-match."""

    for binding in member.get("bindings") or []:
        if binding.get("kind") == "model" and binding.get("name"):
            return {"name": binding["name"]}
    return None


def _skill_dependencies(
        payload: dict[str, Any], dependencies: dict[str, Any]
) -> dict[str, Any]:
    """独立技能包的 bundle 把内容放在 `payload`，随包依赖段是空的。

    `_land_skills` 只认 `dependencies.skills[]`（应用包内就是那个形状），所以
    这里把 `payload` 折成同样的一条 entry：技能只有一种落地路径，不因为「随应用
    装」还是「单独装」而分叉。插件条目本来就有完整的 `dependencies`，原样透传。
    """

    if payload.get("kind") == "plugin" or dependencies.get("skills"):
        return dependencies

    name = str(payload.get("name") or "")
    return {
        "tools": [],
        "skills": [
            {
                "ref": f"skill:{name}",
                "name": name,
                "display_name": payload.get("display_name"),
                "payload": {
                    "call_name": name,
                    "content_hash": payload.get("content_hash"),
                    "frontmatter_raw": payload.get("frontmatter_raw") or {},
                },
                "files": payload.get("files") or [],
            }
        ],
        "plugins": [],
    }


def _missing(kind: str, name: Any, reason: Any) -> dict[str, Any]:
    return {"kind": kind, "name": name, "reason": str(reason)}


def _slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.lower())
    return cleaned.strip("-") or "skill"


__all__ = ["BundleInstallService", "PLACEHOLDER_TOOL_TYPES"]