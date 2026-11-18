"""Bundle landing for cross-tenant installs (§7.4 / §7.5.5).

One transaction, one order, no half-landed cluster:

```
① plugins / skills   (zip → import_package(commit=False))
② tools              (only mcp crosses tenants, rebuilt with cleared
                      credentials; custom / workflow → dropped + reported)
③ record local_dep_map: bundle ref → new local id
④ apps               (sub-agents / workflow-referenced Agents first, then root,
                      import_dsl(commit=False, dep_map=…,
                      resolve_cross_app_refs=False))
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
**Only `mcp` tools cross a tenant boundary.** The other three are reported, never
  materialised:

* **`custom`**: no schema / base_url / credentials travel with the bundle, so a
  row created here could never be configured — it would only show up in the
  target tenant's tool list as a permanently unusable "待配置" entry. The
  reference is cleared and the name goes to `missing` instead.
* **`workflow`**: not configuration-free at all — it is a pointer at one
  source-tenant workflow (`workflow_tool_configs.app_id` + `release_id`), which
  the target tenant has no way to fill in. Same treatment.
* **`builtin`**: ships as `bindings[].kind=builtin_tool`, mapped by `tool_class`.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import threading
import uuid
import zipfile
from collections.abc import Callable, Coroutine
from typing import Any

from mem_storage.object_keys import (
    ObjectKeyInvalid,
    ObjectKeyMissing,
    normalise_object_key,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models import (
    AgentConfig,
    App,
    MCPToolConfig,
    Skill,
    SkillPackage,
    ToolConfig,
)
from ..models.app_model import AppType
from ..models.tool_model import ToolStatus, ToolType
from ..repositories.skill_package_repository import SkillPackageRepository
from ..storage import StorageFactory
from ..utils.datetime_utils import utcnow_naive
from ..utils.db_locks import acquire_advisory_xact_lock
from .app_dsl_service import AppDslService
from .asset_bundle import BundleError, parse_bundle
from .skill_package_service import SkillPackageService

logger = logging.getLogger(__name__)


# `(resource_type, market_resource_id, version) -> manifest`。随包内容不可用时按
# 引用从市场取那一版（接口文档 §3.4「依赖解析」）。用回调而不是直接持有 gateway，
# 是为了让本模块保持「不认识市场 HTTP」：企业扩展装配时注入，社区构建不传。
DependencyFetcher = Callable[[str, str, str], dict[str, Any]]

# `(resource_type, resource_id, version, context) -> zip bytes`。注入理由与
# `DependencyFetcher` 相同：本模块保持「不认识市场 HTTP」。企业扩展把市场侧的
# 版本文件包接口接进来；没有注入时（社区构建）退回直读共享桶的老路径。
DependencyFilesFetcher = Callable[[str, str, str, str], bytes]

# 市场 zip 的成员名 = 规范化后的 `object_key`；规则实现在
# `mem_storage.object_keys`，与 market-service 共用，避免两份实现漂移。
_FILE_SOURCE_CONTEXT_DEPENDENCY = "dependency"


def _zip_member_name(object_key: Any) -> str:
    try:
        return normalise_object_key(object_key)
    except ObjectKeyMissing as error:
        raise BusinessException(
            "技能文件缺少 object_key", BizCode.EXTERNAL_CATALOG_INVALID,
            data={"reason": "SKILL_FILES_MISSING"},
        ) from error
    except ObjectKeyInvalid as error:
        raise BusinessException(
            "技能文件的 object_key 非法", BizCode.EXTERNAL_CATALOG_INVALID,
            data={"reason": "SKILL_FILES_INVALID"},
        ) from error


def _run_async(coro_factory: Callable[[], Coroutine]):
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

    def __init__(
            self,
            db: Session,
            dsl_service: AppDslService | None = None,
            *,
            dependency_fetcher: DependencyFetcher | None = None,
            dependency_files_fetcher: DependencyFilesFetcher | None = None,
    ):
        self.db = db
        self.dsl_service = dsl_service or AppDslService(db)
        # 本次安装的市场来源 `{resource_id, version}`；空字典表示不是市场安装。
        # 两个入口都会在开头覆盖它，这里给个初值只是为了不依赖调用顺序。
        self._origin: dict[str, str] = {}
        self._submission_index: dict[str, dict[str, str]] = {}
        # 本包的凭据剥离路径（§8.7）；落地 MCP 时据此判断是否要重新填凭据。
        self._redacted_secrets: list[str] = []
        # 按市场引用补依赖内容的回调；社区构建不传 = 只认包内内容。
        self._dependency_fetcher = dependency_fetcher
        # 按市场引用取整版文件包的回调；不传 = 退回直读共享桶。
        self._dependency_files_fetcher = dependency_files_fetcher
        # 本次安装解析到的市场依赖，供安装响应的 `dependencies[]` 使用。
        self._resolved_dependencies: list[dict[str, Any]] = []
        # `(resource_type, resource_id@version)` → 展开后的依赖段，避免同一份
        # 依赖被多个子 Agent 引用时重复下载。
        self._dependency_cache: dict[str, dict[str, Any]] = {}
        # 父包来源（顶层安装的那个版本）：包内内嵌文件从这里取。
        self._origin_resource_type: str | None = None
        self._origin_context = "top_level"
        # `(resource_type, resource_id, version, context)` → 已打开的文件包。
        # 同一个市场版本只拉一次；安装器按请求构造，关闭交给 GC（BytesIO 无 fd）。
        self._file_archives: dict[tuple[str, str, str, str], zipfile.ZipFile] = {}
        # 本次安装落地的子 Agent 与它们的待绑定项（§3.4 `pending_agents[]`）。
        self._pending_agents: list[dict[str, Any]] = []

    def _index_dependency_submissions(
            self, manifest: dict[str, Any]
    ) -> dict[str, dict[str, str]]:
        """把投稿账本里的依赖解成 `submission_id` / 本地 ref → 市场引用。

        父包在审核前只写 `submission_id`。整包合并会改写 `market_sync` 里的
        指向，但不会改已经封口的父包，所以安装以账本为准。
        """

        origin_id = (self._origin or {}).get("resource_id")
        if not origin_id:
            return {}
        from ..models.app_asset_model import MarketSync

        origin_uuid = _coerce_uuid(origin_id)
        if origin_uuid is None:
            return {}
        rows = self.db.scalars(
            select(MarketSync).where(MarketSync.market_resource_id == origin_uuid)
        ).all()
        content_hash = manifest.get("content_hash")
        chosen = [
            row for row in rows
            if content_hash and row.bundle_content_hash == content_hash
        ] or list(rows)
        index: dict[str, dict[str, str]] = {}
        for row in chosen:
            for record in row.dependency_submissions or []:
                if not isinstance(record, dict):
                    continue
                resource_id = record.get("resource_id")
                version = record.get("version")
                if not resource_id or not version:
                    continue
                ref = {"resource_id": str(resource_id), "version": str(version)}
                if record.get("content_hash"):
                    ref["content_hash"] = str(record["content_hash"])
                submission_id = record.get("submission_id")
                if submission_id:
                    index[str(submission_id)] = ref
                for local_ref in record.get("refs") or []:
                    index[str(local_ref)] = ref
        return index

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
            origin_context: str = "top_level",
    ) -> dict[str, Any]:
        """Land every dependency then the application cluster; returns the report.

        ``missing`` holds one `MissingDependency` per degraded item; ``blocking``
        ones are raised as 422 before anything is written.
        ``market_origin`` = ``{resource_id, version}`` when the bundle came from the
        market, so every landed row records where its copy came from (§11.2).
        """

        self._origin = market_origin or {}
        if self._origin:
            # One lock order per tenant across apps, skills and tools. It covers
            # both the exact-copy probe and creation until commit/rollback.
            acquire_advisory_xact_lock(self.db, f"market-install:{tenant_id}")
        self._origin_resource_type = str(manifest.get("resource_type") or "") or None
        self._origin_context = origin_context
        self._submission_index = self._index_dependency_submissions(manifest)

        try:
            parse_bundle(manifest)
        except BundleError as error:
            raise BusinessException(
                str(error), BizCode.EXTERNAL_CATALOG_INVALID, data={"reason": "INVALID_BUNDLE"}
            ) from error

        dependencies = manifest.get("dependencies") or {}
        bindings = manifest.get("bindings") or []
        payload = manifest.get("payload") or {}
        self._redacted_secrets = [str(path) for path in manifest.get("redacted_secrets") or []]
        # §8.2 两种应用包：`release_snapshot`（租户投稿）与 `app_dsl`（运营导入
        # YAML）。按内容判断而不是只看 `format`：`format` 是后加的键，运营侧当前
        # 产物还没有它，而它已经是 DSL 结构了。`app_dsl` 的类型在 DSL 里，不在
        # payload 顶层。
        dsl = payload.get("dsl") if isinstance(payload.get("dsl"), dict) else None
        app_type = payload.get("app_type") or (
            (dsl.get("app") or {}).get("type") if dsl else None
        )
        if app_type not in (AppType.AGENT, AppType.MULTI_AGENT, AppType.WORKFLOW,
                            AppType.PURE_WORKFLOW):
            raise BusinessException(
                f"不支持的应用类型: {app_type}", BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "INVALID_BUNDLE"},
            )

        apps = dependencies.get("apps") or []
        if app_type == AppType.MULTI_AGENT and dsl is None:
            # 集群完整性只对 release 快照包有意义：`app_dsl` 的成员在 `cluster[]`
            # 里，由 `import_dsl` 自己落地并复用，不走 `dependencies.apps[]`。
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
            # 本次解析到的市场依赖（复用 / 新装），以及全部落成草稿的子 Agent。
            # 子 Agent 没发布前集群无法发布，用户必须先补齐绑定再逐个发布。
            "dependencies": list(self._resolved_dependencies),
            "pending_agents": list(self._pending_agents),
            "result": "partial" if missing else "success",
        }

    def install_dependency(
            self,
            *,
            manifest: dict[str, Any],
            tenant_id: uuid.UUID,
            commit: bool = True,
            market_origin: dict[str, str] | None = None,
            overrides: dict[str, Any] | None = None,
            origin_context: str = "top_level",
    ) -> dict[str, Any]:
        """Land one **tool / skill_package** bundle (no application involved).

        市场的技能与工具 tab 是独立条目，装它时不该先造一个应用。落地逻辑与
        应用包内的同名依赖完全同源（同一个 `_land_tools` / `_land_skills`），
        否则「随包装下来的技能」和「单独装的技能」会在目标租户里长成两种样子。
        """

        self._origin = market_origin or {}
        if self._origin:
            # One lock order per tenant across apps, skills and tools. It covers
            # both the exact-copy probe and creation until commit/rollback.
            acquire_advisory_xact_lock(self.db, f"market-install:{tenant_id}")
        self._origin_resource_type = str(manifest.get("resource_type") or "") or None
        self._origin_context = origin_context
        try:
            parse_bundle(manifest)
        except BundleError as error:
            raise BusinessException(
                str(error), BizCode.EXTERNAL_CATALOG_INVALID, data={"reason": "INVALID_BUNDLE"}
            ) from error

        resource_type = manifest.get("resource_type")
        payload = manifest.get("payload") or {}
        dependencies = manifest.get("dependencies") or {}
        self._redacted_secrets = [str(path) for path in manifest.get("redacted_secrets") or []]
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
                # 将市场条目自身也走 market_ref 路径，重复安装同一版本时能
                # 命中本租户已有 MCP，而不再创建第二个副本。
                "market_ref": self._origin,
            }
            self._land_tools(
                {"tools": [entry], "skills": [], "plugins": []},
                tenant_id, dep_map, missing,
                standalone=True,
            )
            if not dep_map.get(entry["ref"]):
                raise BusinessException(
                    "工具落地失败", BizCode.EXTERNAL_CATALOG_INVALID,
                    data={"reason": "TOOL_LANDING_FAILED", "missing": missing},
                )
            local_id = dep_map[entry["ref"]]
            local_name = self.db.get(ToolConfig, uuid.UUID(local_id)).name
        elif resource_type == "skill_package":
            # 单独装的这一个技能/插件，`_origin` 指的就是它自己 → 能写全 source_ref。
            # 插件是多成员容器，`call_name` 是**成员级**运行时标识（ADR-11）：从插件
            # 这一层给一个值会让每个成员抢同一个名字，`_plan_import_members` 会直接判
            # 「本次导入中重复」。所以插件只接受名称与描述：市场安装门面
            # （`external_catalog_service._skill_overrides`）对插件根本不传这一项，这里
            # 硬拒是给绕过门面的调用方的兜底，与资源中心同口径（那里也没有这层覆盖）。
            is_plugin = payload.get("kind") == "plugin" or bool(dependencies.get("plugins"))
            if is_plugin and (overrides or {}).get("call_name"):
                raise BusinessException(
                    "插件不支持自定义调用名称，请只填写名称与描述",
                    BizCode.INVALID_PARAMETER,
                    data={"reason": "PLUGIN_CALL_NAME_NOT_SUPPORTED"},
                )
            skill_dependencies = _skill_dependencies(payload, dependencies)
            # 插件包的内容全在 `dependencies.skills[]` 里（成员一律带 plugin_ref）。
            # 一条 skills 都没有的插件包没有任何可落地物：`_land_skills` 只遍历
            # skills[]，分组为空、一行都不写，而 `_skill_result` 会按包内原名回查
            # ——可能命中同租户同名的**旧插件**，把用户没装的东西当作
            # `local_resource_id` 报成功（改过名则命中不到，`result=success` +
            # id 为 null，卸载 / needs_config 全断）。`plugins[]` 只是插件的元数据，
            # 不能当落地依据。
            if is_plugin and not skill_dependencies.get("skills"):
                raise BusinessException(
                    f"插件「{payload.get('display_name') or payload.get('name')}」"
                    "的安装包里没有任何成员",
                    BizCode.EXTERNAL_CATALOG_INVALID,
                    data={"reason": "INCOMPLETE_BUNDLE"},
                )
            # `strict=True`：顶层安装只有这一个资源，落地失败就该整单失败并把原始
            # 错误码抛给前端（调用名称冲突是 DUPLICATE_NAME，用户改个名字重试），
            # 而不是「result=partial + 一条 missing」——那样用户会以为已经装好了。
            self._land_skills(
                skill_dependencies,
                tenant_id, dep_map, missing,
                own_market_ref=True,
                overrides=overrides,
                strict=True,
            )
            local_id, local_name = self._skill_result(
                tenant_id=tenant_id,
                is_plugin=is_plugin,
                name=payload.get("name"),
                dependencies=skill_dependencies,
                dep_map=dep_map,
            )
            # 落地了却查不回本地行：响应里的 `local_resource_id` 是卸载、
            # `needs_config`、共享校验的唯一凭证，带着 null 报成功比报错更糟。
            # 工具侧同样有这个断言（`TOOL_LANDING_FAILED`），保持对称。
            if local_id is None:
                raise BusinessException(
                    f"技能「{payload.get('display_name') or payload.get('name')}」"
                    "落地后未能找到本地记录",
                    BizCode.EXTERNAL_CATALOG_INVALID,
                    data={"reason": "SKILL_LANDING_FAILED", "missing": missing},
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
            is_plugin: bool,
            name: str | None,
            dependencies: dict[str, Any] | None = None,
            dep_map: dict[str, str] | None = None,
    ) -> tuple[str | None, str | None]:
        """落地后的本地 id 与展示名。

        `dep_map` 的值是 `skills` 行 id（Agent 绑定用），而安装响应要的是技能包/
        插件这一层：技能包与插件优先通过 `dep_map` 回查，避免改名后误取原有包。
        插件必须走 `dep_map` —— 用户在安装
        弹窗里改过 `display_name` 之后插件行的 `name` 已不是包内原名，按名字回查会
        取不到，安装响应就会带着空的 `local_resource_id` 出去（卸载、needs_config
        全部断掉）。
        """

        repo = SkillPackageRepository(self.db)
        if is_plugin:
            plugin_id = next(
                (
                    (dep_map or {}).get(str(entry.get("ref")))
                    for entry in (dependencies or {}).get("plugins") or []
                    if (dep_map or {}).get(str(entry.get("ref")))
                ),
                None,
            )
            plugin = (
                repo.get_plugin_by_id(uuid.UUID(plugin_id), tenant_id)
                if plugin_id
                # 兜底：老包或异常路径下 `dep_map` 没有插件引用时仍按包内原名回查。
                else repo.get_plugin_by_name(tenant_id, str(name or ""))
            )
            if plugin is None:
                return None, None
            return str(plugin.id), plugin.name

        skill_id = next((
            (dep_map or {}).get(str(entry.get("ref")))
            for entry in (dependencies or {}).get("skills") or []
            if (dep_map or {}).get(str(entry.get("ref")))
        ), None)
        skill = self.db.get(Skill, uuid.UUID(skill_id)) if skill_id else None
        package = (
            repo.get_by_id(skill.package_id, tenant_id) if skill is not None and skill.package_id
            else repo.get_by_name(tenant_id, str(name or ""))
        )
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

    @staticmethod
    def _group_skill_entries(
            dependencies: dict[str, Any],
            index: dict[str, dict[str, str]] | None = None,
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """按 `(插件 ref, 市场引用)` 分组。

        插件成员必须一起落地成一个 multi-member 包；两个同 `plugin_ref` 但来自
        不同市场资源的技能，也不能把来源折叠成一处。
        """

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in dependencies.get("skills") or []:
            plugin_ref = str(
                entry.get("plugin_ref")
                or (entry.get("payload") or {}).get("plugin_ref")
                or ""
            )
            market_ref = _market_ref_of(entry, index)
            market_key = (
                f"{market_ref['resource_id']}@{market_ref['version']}"
                if market_ref else (
                    "" if plugin_ref else f"embedded:{entry.get('ref') or entry.get('name')}"
                )
            )
            grouped.setdefault((plugin_ref, market_key), []).append(entry)
        return grouped

    def count_new_skill_units(
            self,
            manifest: dict[str, Any],
            tenant_id: uuid.UUID,
    ) -> int:
        """这次安装会新建多少个技能包单位（§3.4 的配额口径）。

        口径是**包**而不是 `skills` 行：插件包算 1 个单位、成员技能不重复计数，
        独立技能包也算 1 个单位；本租户已有同一市场资源的副本时复用、不占额度。
        与 `_land_skills` 共用 `_group_skill_entries` 和 `_find_market_skill_copy`，
        保证「预检算出来的数」和「真正会新建的包」是同一件事。

        调用方要在写库之前拿它补一次 `ensure_skill_quota(extra=...)`：只按当前
        计数校验的话，一个带技能依赖的整包会让用量越过上限。
        """

        payload = manifest.get("payload") or {}
        dependencies = manifest.get("dependencies") or {}
        # 单独装的技能/插件，`_origin` 指的就是它自己，不吃 `market_ref` 复用路径。
        own_market_ref = manifest.get("resource_type") == "skill_package"
        if own_market_ref:
            dependencies = _skill_dependencies(payload, dependencies)

        if self._origin:
            acquire_advisory_xact_lock(self.db, f"market-install:{tenant_id}")
        units = 0
        grouped = self._group_skill_entries(dependencies, self._submission_index)
        for (plugin_ref, _), entries in grouped.items():
            market_ref = _market_ref_of(entries[0], self._submission_index)
            copy_origin = market_ref or self._origin
            if copy_origin and not market_ref and not own_market_ref:
                group = repr((plugin_ref, sorted(str(entry.get("ref")) for entry in entries)))
                group_key = hashlib.sha256(group.encode("utf-8")).hexdigest()
                copy_origin = {
                    "resource_id": f"embedded:{copy_origin['resource_id']}:{group_key}",
                    "version": copy_origin["version"],
                }
            if copy_origin and self._find_market_skill_copy(
                copy_origin, tenant_id, entries
            ) is not None:
                continue
            units += 1
        return units

    def _land_skills(
            self,
            dependencies: dict[str, Any],
            tenant_id: uuid.UUID,
            dep_map: dict[str, str],
            missing: list[dict[str, Any]],
            *,
            own_market_ref: bool = False,
            overrides: dict[str, Any] | None = None,
            strict: bool = False,
    ) -> None:
        """Each plugin lands as one multi-member zip; standalone skills land alone.

        Market-origin installs disambiguate occupied names and create independent
        skills. Existing skill IDs and files must never be overwritten by an install.

        ``own_market_ref``: 技能包没有 `origin` 列，来源靠 `source_type` /
        `source_ref` 两列表达（§11.2）。 ``True`` 只在「从市场技能/工具 tab 直接
        装这一个技能」时为真——那时 `_origin` 里的资源 id 就是它自己的，能写全
        `<resource_id>@<version>`。随应用包下来时包里只有 `skill:<name>` 引用，
        市场资源 id 要等投稿时才产生，装的时候拿不到，于是只写
        `source_type=market`（照样被「市场副本不可再投稿」拦下），不去编造一个
        查不到的资源 id。
        """

        plugin_meta = {
            str(plugin.get("ref")): plugin for plugin in (dependencies.get("plugins") or [])
        }
        for (plugin_ref, _market_key), entries in self._group_skill_entries(
                dependencies, getattr(self, "_submission_index", None)
        ).items():
            member_refs: dict[str, dict[str, Any]] = {}
            market_ref = _market_ref_of(entries[0], getattr(self, "_submission_index", None))
            origin = market_ref or self._origin
            # Every dependency with market_ref is an exact copy of that market
            # version; persist it so a later app publication can reuse it.
            source_type = "market" if origin else "imported"
            copy_origin = origin
            embedded = bool(origin and not market_ref and not own_market_ref)
            if embedded:
                # An embedded member has no standalone market resource. Give its
                # group a local-only identity within the immutable parent version.
                group = repr((plugin_ref, sorted(str(entry.get("ref")) for entry in entries)))
                group_key = hashlib.sha256(group.encode("utf-8")).hexdigest()
                copy_origin = {
                    "resource_id": f"embedded:{origin['resource_id']}:{group_key}",
                    "version": origin["version"],
                }
            source_ref = self._market_source_ref(copy_origin) if copy_origin else None
            # 文件字节的来源默认是父包（顶层安装的那个版本）；按市场引用补全的
            # 依赖改为从那一版依赖自己的文件包取。
            file_source = self._origin_file_source()
            if origin:
                # 同一市场技能/插件版本已在本租户 → 只补 dep_map，不重复导入。
                local = self._find_market_skill_copy(copy_origin, tenant_id, entries)
                if local is not None:
                    for entry in entries:
                        skill_id = local["members"].get(str(entry.get("ref")))
                        if skill_id:
                            dep_map[str(entry.get("ref"))] = skill_id
                    if plugin_ref and local["plugin_id"]:
                        dep_map[plugin_ref] = local["plugin_id"]
                    if not embedded:
                        self._record_dependency(
                            "skill_package", origin,
                            local["plugin_id"] or local["package_id"],
                            reused=True,
                            version_mismatch=local["version"] != origin["version"],
                        )
                    continue
            if market_ref and not own_market_ref:
                # 存量包可能内嵌了文件：那时键空间仍属于父版本，只有真正从依赖
                # 清单补出来的条目才改指向依赖自己的版本文件包。
                embedded = all(entry.get("files") for entry in entries)
                entries = self._enrich_skill_entries(entries, market_ref, missing)
                if not entries:
                    continue
                if not embedded:
                    file_source = (
                        "skill_package",
                        str(market_ref["resource_id"]),
                        str(market_ref["version"]),
                        _FILE_SOURCE_CONTEXT_DEPENDENCY,
                    )
            # 顶层的独立技能可以逐项改名/改描述/改调用名称；插件只到插件级
            # （`meta` 留空，名称与描述走下面的 `plugin_name` / `plugin_description`）。
            member_meta = (
                _skill_meta_overrides(entries, overrides)
                if overrides and not plugin_ref
                else None
            )
            try:
                archive = self._build_skill_zip(
                    entries,
                    nested=bool(plugin_ref),
                    member_refs=member_refs,
                    file_source=file_source,
                )
                plugin_row = plugin_meta.get(plugin_ref) if plugin_ref else None
                response = SkillPackageService.import_package(
                    self.db,
                    tenant_id,
                    zip_bytes=archive,
                    meta=member_meta,
                    plugin_name=(overrides or {}).get("display_name")
                    or (plugin_row or {}).get("name"),
                    plugin_description=(overrides or {}).get("description")
                    or ((plugin_row or {}).get("payload") or {}).get("description"),
                    source_type=source_type,
                    source_ref=source_ref,
                    conflict_strategy="rename",
                    commit=False,
                    isolate_conflicts=bool(origin),
                )
            except BusinessException as error:
                if strict:
                    # 不在这里 rollback：事务边界归调用方（`commit=False` 只
                    # flush），外层 `sync_session` 异常退出时会统一回滚。自己
                    # 提前 rollback 会把调用方在本次调用之前已经写好的行一起抹掉。
                    raise
                for entry in entries:
                    missing.append(_missing("skill", entry.get("name"), str(error)))
                continue

            for member in response.get("members") or []:
                entry = member_refs.get(str(member.get("identifier")))
                if entry is None:
                    continue
                if member.get("package_id"):
                    dep_map[str(entry.get("ref"))] = str(
                        member.get("skill_id") or member["package_id"]
                    )
                else:
                    if strict:
                        raise BusinessException(
                            f"技能「{entry.get('display_name') or entry.get('name')}」"
                            f"安装失败：{member.get('skip_reason') or '名称冲突'}",
                            BizCode.DUPLICATE_NAME,
                            data={"reason": "SKILL_NAME_CONFLICT"},
                        )
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
            file_source: tuple[str, str, str, str] | None,
    ) -> bytes:
        """Rebuild a standard pack zip from the bundle's `files[]` object keys."""

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry in entries:
                payload = entry.get("payload") or {}
                call_name = payload.get("call_name")
                package_name = _slug(str(entry.get("name") or call_name or "skill"))
                root = f"{package_name}/" if nested else ""
                # Import identifiers describe archive members and survive local
                # call-name isolation; use them to bind the original app ref.
                identifier = package_name if nested else str(
                    (payload.get("frontmatter_raw") or {}).get("name") or call_name or ""
                )
                if identifier in member_refs:
                    raise BusinessException(
                        f"技能归档成员标识重复: {identifier}", BizCode.EXTERNAL_CATALOG_INVALID
                    )
                if identifier:
                    member_refs[identifier] = entry
                files = entry.get("files") or []
                if not files:
                    raise BusinessException(
                        f"技能「{entry.get('display_name') or entry.get('name')}」没有随包文件",
                        BizCode.EXTERNAL_CATALOG_INVALID,
                        data={"reason": "SKILL_FILES_MISSING"},
                    )
                for file_entry in files:
                    rel_path = str(file_entry.get("path") or "")
                    content = self._read_file(
                        file_entry.get("object_key"),
                        file_source=file_source,
                        expected_sha256=file_entry.get("sha256"),
                    )
                    info = zipfile.ZipInfo(f"{root}{rel_path}", date_time=(2026, 1, 1, 0, 0, 0))
                    info.flag_bits |= 0x800
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, content)
        return buffer.getvalue()

    def _origin_file_source(self) -> tuple[str, str, str, str] | None:
        """父包版本的文件来源；不是市场安装（没有 origin）时返回 None。"""

        origin = self._origin or {}
        resource_id = origin.get("resource_id")
        version = origin.get("version")
        if not resource_id or not version or not self._origin_resource_type:
            return None
        return (
            self._origin_resource_type,
            str(resource_id),
            str(version),
            self._origin_context,
        )

    def _read_file(
            self,
            object_key: Any,
            *,
            file_source: tuple[str, str, str, str] | None,
            expected_sha256: Any = None,
    ) -> bytes:
        """有市场文件回调时按版本 zip 取字节，否则退回直读共享桶。

        `market-uploads/` 键是内容寻址的，但 zip 走内网传输，仍按清单里的
        `sha256` 重算比对：不一致说明字节与审核过的清单不同源，装进去就是错包。
        """

        key = str(object_key or "")
        if not key:
            raise BusinessException(
                "技能文件缺少 object_key", BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "SKILL_FILES_MISSING"},
            )
        if self._dependency_files_fetcher is not None and file_source is not None:
            payload = self._read_market_file(key, file_source)
        else:
            storage = StorageFactory.get_storage()
            payload = _run_async(lambda: storage.download(key))
        expected = str(expected_sha256 or "")
        if expected and hashlib.sha256(payload).hexdigest() != expected:
            raise BusinessException(
                "技能文件与清单哈希不一致", BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "SKILL_FILES_INVALID", "object_key": key[:200]},
            )
        return payload

    def _read_market_file(
            self, object_key: str, file_source: tuple[str, str, str, str]
    ) -> bytes:
        member = _zip_member_name(object_key)
        archive = self._file_archive(file_source)
        try:
            return archive.read(member)
        except KeyError as error:
            raise BusinessException(
                "技能文件不在市场的版本文件包中", BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "SKILL_FILES_MISSING", "object_key": object_key[:200]},
            ) from error

    def _file_archive(
            self, file_source: tuple[str, str, str, str]
    ) -> zipfile.ZipFile:
        """按版本拉一次整包 zip 并缓存；同一版本被多次引用时只发一次请求。"""

        archive = self._file_archives.get(file_source)
        if archive is not None:
            return archive
        try:
            payload = self._dependency_files_fetcher(*file_source)  # type: ignore[misc]
        except BusinessException:
            raise
        except Exception as error:  # noqa: BLE001 —— 传输故障降级为一条 missing
            raise BusinessException(
                "市场文件包获取失败", BizCode.EXTERNAL_CATALOG_UNAVAILABLE,
                data={"reason": "MARKET_FILES_UNAVAILABLE"},
            ) from error
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as error:
            raise BusinessException(
                "市场文件包格式非法", BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "INVALID_FILES_ARCHIVE"},
            ) from error
        self._file_archives[file_source] = archive
        return archive

    # ==================== ② tools ====================

    def _land_tools(
            self,
            dependencies: dict[str, Any],
            tenant_id: uuid.UUID,
            dep_map: dict[str, str],
            missing: list[dict[str, Any]],
            *,
            standalone: bool = False,
    ) -> None:
        """Only `mcp` is rebuilt (nested connection shape, secrets already redacted).

        `custom` and `workflow` create **no row at all** — neither can be
        configured from what the bundle carries, so a row here would only be a
        permanently unusable entry in the target tenant's tool list. Both record
        their name in `missing`, and `dep_map[ref] = ""` tells
        `app_dsl_service._resolve_tools` "declared by the bundle, not landed here":
        the reference is **kept** as its original `tool:<名称>` local ref (not a
        valid UUID), so the batch method API reports it in `missing_tool_ids` and
        the UI can render "该工具已失效".

        Workflow **tool nodes** take the other branch on the same map value —
        `_resolve_workflow_nodes` **clears** `config.tool_id` to `None`, because a
        node needs one concrete UUID at runtime (otherwise
        `ToolNode.execute` raises TOOL_SELECTION_INACTIVE and the whole workflow
        fails, while the editor's non-empty check passes silently).
        """

        for entry in dependencies.get("tools") or []:
            ref = str(entry.get("ref"))
            tool_type = str(entry.get("tool_type") or "")
            market_ref = _market_ref_of(entry, getattr(self, "_submission_index", None))
            needs_reconfiguration = self._tool_needs_reconfiguration(
                ref, dependencies, standalone=standalone
            )

            if market_ref:
                # Only reuse the exact version; another version gets a separate
                # local tool so existing applications remain bound to their copy.
                #
                # 只对 mcp 复用：custom / workflow 现在一律不落地，若本租户还留着
                # 改造前安装生成的外部占位行（`is_active=True` 且带
                # `external_resource_id`），复用会把它绑定进来，等于让一条永远配
                # 不好的工具替代「缺失」上报。这类历史行按不存在处理，落到下面
                # 的 custom/workflow 分支报 missing。
                local = (
                    self._find_external_tool_copy(
                        market_ref["resource_id"], tenant_id, market_ref["version"]
                    )
                    if tool_type == ToolType.MCP.value
                    else None
                )
                if local is not None:
                    local_id, local_version = local
                    dep_map[ref] = local_id
                    self._record_dependency(
                        "tool", market_ref, local_id,
                        reused=True,
                        version_mismatch=local_version != market_ref["version"],
                    )
                    continue
                # B6-b 之后父包只存市场引用，内容得按引用去取；已内嵌内容的存量
                # 包走原路径，不额外发一次 HTTP。只有 mcp 需要内容（要建行），
                # custom / workflow 不落地，取回来也没用，还会多写一条
                # 「依赖包缺少 server_url」的假 missing。
                if tool_type == ToolType.MCP.value:
                    entry = self._enrich_tool_entry(entry, market_ref, missing)
                    tool_type = str(entry.get("tool_type") or tool_type)

            if tool_type == ToolType.MCP.value:
                landed_id = self._land_mcp(
                    entry, tenant_id, missing,
                    origin=market_ref or self._origin,
                    needs_reconfiguration=needs_reconfiguration,
                )
                dep_map[ref] = landed_id
                if market_ref:
                    self._record_dependency(
                        "tool", market_ref, landed_id,
                        reused=False, version_mismatch=False,
                    )
            elif tool_type in (ToolType.CUSTOM.value, ToolType.WORKFLOW.value):
                # 除 mcp 外一律不落地：custom 没有 schema / base_url / 凭据，
                # workflow 指向源租户的某个纯工作流——两者在目标租户都没有可填的
                # 落点，建行只会在工具列表里留一条永远配不好的记录。`dep_map` 记
                # 空串表示「包里声明了、但不落地」，`_resolve_tools` 据此**保留**
                # 引用（`tool_id` 仍是 `tool:<名称>` 本地 ref，不是合法 UUID），
                # 让批量方法接口把它报进 `missing_tool_ids`，前端才能渲染「该工具
                # 已失效」。同时名称进 `missing`，装完当次就能看到少了什么。
                dep_map[ref] = ""
                missing.append(
                    _missing(
                        "tool",
                        # 名称兜底成 ref：正常包内 `name` 恒有（`_collect_tools` 总写
                        # 这一项），但外部制作的包可能只给 ref，缺失时安装结果里会
                        # 出现 name=None，用户看不出少了哪个工具。
                        entry.get("name") or ref,
                        "自定义工具不支持跨租户携带，已移除"
                        if tool_type == ToolType.CUSTOM.value
                        else "工作流工具不支持跨租户携带，已移除",
                    )
                )
            elif tool_type == ToolType.BUILTIN.value:
                # builtin 不随包：落地时按 tool_class 在目标租户找同类，找不到就置空
                continue
            else:
                # 类型缺失或未知：同样兜底成 ref，否则 reason 里带一个空类型、
                # name 也可能是 None，这条 missing 对用户没有任何信息量。
                missing.append(
                    _missing(
                        "tool",
                        entry.get("name") or ref,
                        f"未知工具类型 {tool_type or '（包内未声明）'}",
                    )
                )

    def _land_mcp(
            self,
            entry: dict[str, Any],
            tenant_id: uuid.UUID,
            missing: list[dict[str, Any]],
            *,
            origin: dict[str, str] | None = None,
            needs_reconfiguration: bool = True,
    ) -> str:
        payload = entry.get("payload") or {}
        server_url = payload.get("server_url")
        if not server_url:
            missing.append(_missing("tool", entry.get("name"), "缺少 server_url"))
            return ""

        # 嵌套结构随包（headers / auth_type / auth_config），密钥值已在封包时剥离；
        # 目标租户拿到的是一份「结构就绪、值待填」的配置。
        connection_config = payload.get("connection_config")
        if not isinstance(connection_config, dict):
            connection_config = {}

        now = utcnow_naive()
        row = ToolConfig(
            id=uuid.uuid4(),
            name=self._unique_tool_name(
                str(entry.get("name") or "MCP 工具"), ToolType.MCP.value, tenant_id
            ),
            description=None,
            # 工具按产品口径没有图标入口（§6.1.1 口径 5）
            icon=None,
            tool_type=ToolType.MCP.value,
            tenant_id=tenant_id,
            # 有凭据被剥离 → 结构就绪但不可用，等用户补填；无凭据则可直接用
            status=(
                ToolStatus.UNCONFIGURED.value
                if needs_reconfiguration
                else ToolStatus.AVAILABLE.value
            ),
            config_data={
                "server_url": server_url,
                "connection_config": dict(connection_config),
                "available_tools": payload.get("available_tools") or [],
            },
            tags=[],
            is_active=True,
            created_at=now,
            updated_at=now,
            **self._origin_columns(origin),
        )
        self.db.add(row)
        self.db.flush()
        self.db.add(
            MCPToolConfig(
                id=row.id,
                server_url=server_url,
                connection_config=dict(connection_config),
                available_tools=payload.get("available_tools") or [],
            )
        )
        self.db.flush()
        return str(row.id)

    def _tool_needs_reconfiguration(
            self,
            ref: str,
            dependencies: dict[str, Any],
            *,
            standalone: bool,
    ) -> bool:
        """True when this tool's own payload lost credential keys at sealing.

        Mirrors `external_catalog.adapters.tool._redacted_paths_for_tool`: a
        standalone tool bundle prefixes paths with `payload.`, a dependency entry
        uses its index under `dependencies.tools[]`.
        """

        if standalone:
            prefixes = ["payload"]
        else:
            prefixes = [
                f"dependencies.tools[{index}].payload"
                for index, item in enumerate(dependencies.get("tools") or [])
                if isinstance(item, dict) and str(item.get("ref")) == ref
            ]
        for path in self._redacted_secrets:
            if not any(
                path == prefix or path.startswith(f"{prefix}.") for prefix in prefixes
            ):
                continue
            if "connection_config" in path or "auth_config" in path:
                return True
        return False

    @staticmethod
    def _market_source_ref(origin: dict[str, str] | None) -> str | None:
        """`<market_resource_id>@<version>`（§11.2，反解按最后一个 `@` 切）。"""

        resource_id = (origin or {}).get("resource_id")
        if not resource_id:
            return None
        return f"{resource_id}@{(origin or {}).get('version') or ''}"

    def _origin_columns(self, origin: dict[str, str] | None = None) -> dict[str, Any]:
        """Provenance columns for a row that came from the market (§11.2)."""

        resolved = origin if origin is not None else self._origin
        if not resolved:
            return {}
        return {
            "origin": "external",
            "external_source": "community_market",
            "external_resource_id": resolved.get("resource_id"),
            "external_version": resolved.get("version"),
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

    def _find_external_tool(
            self, market_resource_id: Any, tenant_id: uuid.UUID, version: str
    ) -> str | None:
        """Return this tenant's active copy of the exact market tool version."""

        if not market_resource_id:
            return None
        row = self.db.execute(
            select(ToolConfig).where(
                ToolConfig.tenant_id == tenant_id,
                ToolConfig.external_resource_id == str(market_resource_id),
                ToolConfig.external_version == str(version),
                ToolConfig.is_active.is_(True),
            )
        ).scalars().first()
        return str(row.id) if row is not None else None

    def _find_external_tool_copy(
            self, market_resource_id: Any, tenant_id: uuid.UUID, version: str
    ) -> tuple[str, str] | None:
        """Only reuse the exact requested market tool version; never upgrade another copy."""

        if not market_resource_id:
            return None
        row = self.db.execute(
            select(ToolConfig)
            .where(
                ToolConfig.tenant_id == tenant_id,
                ToolConfig.external_resource_id == str(market_resource_id),
                ToolConfig.external_version == str(version),
                ToolConfig.is_active.is_(True),
            )
            .order_by(ToolConfig.created_at.desc())
        ).scalars().first()
        if row is None:
            return None
        return str(row.id), str(row.external_version or "")

    def _find_market_skill_copy(
            self,
            market_ref: dict[str, str],
            tenant_id: uuid.UUID,
            entries: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Find a complete local copy of the exact requested market skill version."""

        resource_id = market_ref["resource_id"]
        packages = self.db.execute(
            select(SkillPackage)
            .where(
                SkillPackage.tenant_id == tenant_id,
                SkillPackage.source_type == "market",
                SkillPackage.source_ref == f"{resource_id}@{market_ref['version']}",
                SkillPackage.is_active.is_(True),
            )
            .order_by(SkillPackage.created_at.desc())
        ).scalars().all()
        if not packages:
            return None

        # A plugin consists of multiple packages, but a reusable copy must be
        # complete within ONE plugin. Standalone packages are separate groups.
        groups: dict[tuple[str, str], list] = {}
        for package in packages:
            key = (
                ("plugin", str(package.plugin_id)) if package.plugin_id
                else ("package", str(package.id))
            )
            groups.setdefault(key, []).append(package)
        for group in groups.values():
            by_name: dict[str, set[str]] = {}
            by_call: dict[str, set[str]] = {}
            for package in group:
                metadata = package.frontmatter_raw or {}
                rows = self.db.execute(select(Skill).where(
                    Skill.package_id == package.id, Skill.is_active.is_(True)
                )).scalars().all()
                for row in rows:
                    name = str(metadata.get("_market_original_name") or row.name)
                    call = str(
                        metadata.get("_market_original_call_name") or row.call_name or row.name
                    )
                    by_name.setdefault(name, set()).add(str(row.id))
                    by_call.setdefault(call, set()).add(str(row.id))
            members: dict[str, str] = {}
            for entry in entries:
                # Package names and call names are separate namespaces. Entries
                # with a package name resolve by that name, never another row's
                # identically-spelled call name. Ref keys stay unambiguous.
                name = entry.get("name")
                call = (entry.get("payload") or {}).get("call_name")
                candidates = (
                    by_name.get(str(name), set()) if name else by_call.get(str(call), set())
                )
                if len(candidates) != 1:
                    break
                members[str(entry.get("ref"))] = next(iter(candidates))
            else:
                if members:
                    first = group[0]
                    return {
                        "package_id": str(first.id),
                        "plugin_id": str(first.plugin_id) if first.plugin_id else None,
                        "version": market_ref["version"],
                        "members": members,
                    }
        return None

    def _record_dependency(
            self,
            resource_type: str,
            market_ref: dict[str, str] | None,
            local_id: Any,
            *,
            reused: bool,
            version_mismatch: bool,
    ) -> None:
        """把一条已解析的依赖记进安装响应（§3.4 `dependencies[]`）。

        同一 `(resource_type, resource_id, version)` 只记一次：多个子 Agent 引用
        同一依赖时，响应里不应该出现两条一模一样的行。
        """

        if not market_ref or not local_id:
            return
        key = (resource_type, str(market_ref["resource_id"]), str(market_ref["version"]))
        for item in self._resolved_dependencies:
            if (
                item["resource_type"],
                item["market_resource_id"],
                item["version"],
            ) == key:
                return
        self._resolved_dependencies.append(
            {
                "resource_type": resource_type,
                "market_resource_id": str(market_ref["resource_id"]),
                "version": str(market_ref["version"]),
                "local_resource_id": str(local_id),
                "reused": bool(reused),
                "version_mismatch": bool(version_mismatch),
            }
        )

    def _fetch_dependency_manifest(
            self,
            resource_type: str,
            market_ref: dict[str, str],
            missing: list[dict[str, Any]],
            *,
            name: Any = None,
    ) -> dict[str, Any] | None:
        """按市场引用取那一版的包并校验（§3.4「依赖解析」）。

        取数失败一律降级成 `missing` 里的一条，不中断整单安装：依赖坏掉不该让
        用户连主应用都装不进去。真正的阻断条件（依赖版本未通过审核）由市场侧在
        取包时用 `6024` 拒绝，那个错误会被调用方按 6024 抛出。
        """

        key = f"{resource_type}:{market_ref['resource_id']}@{market_ref['version']}"
        if key in self._dependency_cache:
            return self._dependency_cache[key]
        kind = "tool" if resource_type == "tool" else "skill"
        if self._dependency_fetcher is None:
            missing.append(
                _missing(kind, name, "包内未携带该依赖内容，且当前部署无法从市场补齐")
            )
            return None
        try:
            manifest = self._dependency_fetcher(
                resource_type, market_ref["resource_id"], market_ref["version"]
            )
        except BusinessException:
            raise
        except Exception as error:
            missing.append(_missing(kind, name, f"依赖从市场获取失败：{error}"))
            return None
        try:
            parse_bundle(manifest)
        except BundleError as error:
            missing.append(_missing(kind, name, f"依赖包校验失败：{error}"))
            return None
        # 父包随引用冻结了依赖指纹：取回的内容对不上就是内容被换过，按数据非法
        # （6024）整单拒绝，而不是装一份来路不明的副本。
        expected_hash = market_ref.get("content_hash")
        if expected_hash and expected_hash != manifest.get("content_hash"):
            raise BusinessException(
                f"依赖「{name or market_ref['resource_id']}」的内容与父包记录不一致",
                BizCode.EXTERNAL_CATALOG_INVALID,
                data={"reason": "INVALID_BUNDLE"},
            )
        self._dependency_cache[key] = manifest
        return manifest

    def _enrich_tool_entry(
            self,
            entry: dict[str, Any],
            market_ref: dict[str, str],
            missing: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """包内没带工具内容时，用市场那一版的 `payload.config` 补上。"""

        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        if payload.get("server_url"):
            return entry
        manifest = self._fetch_dependency_manifest(
            "tool", market_ref, missing, name=entry.get("name")
        )
        if manifest is None:
            return entry
        fetched = manifest.get("payload") or {}
        merged = {**(fetched.get("config") or {}), **payload}
        if not merged.get("server_url"):
            missing.append(_missing("tool", entry.get("name"), "依赖包缺少 server_url"))
            return entry
        return {**entry, "payload": merged}

    def _enrich_skill_entries(
            self,
            entries: list[dict[str, Any]],
            market_ref: dict[str, str],
            missing: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """包内没带技能文件时，用市场那一版的 `payload` / `dependencies` 补上。"""

        if all(entry.get("files") for entry in entries):
            return entries
        manifest = self._fetch_dependency_manifest(
            "skill_package",
            market_ref,
            missing,
            name=entries[0].get("name") if entries else None,
        )
        if manifest is None:
            return entries
        payload = manifest.get("payload") or {}
        if payload.get("kind") == "plugin":
            members = [
                item
                for item in ((manifest.get("dependencies") or {}).get("skills") or [])
                if isinstance(item, dict)
            ]
            return members or entries
        name = payload.get("name")
        return [
            {
                "ref": entries[0].get("ref") if entries else f"skill:{name}",
                "name": name,
                "display_name": payload.get("display_name"),
                "payload": {
                    "call_name": name,
                    "content_hash": payload.get("content_hash"),
                    "frontmatter_raw": payload.get("frontmatter_raw") or {},
                },
                "files": payload.get("files") or [],
            }
        ]

    def _unresolved_bindings(
            self, app_id: Any, member_config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """子 Agent 落地后仍然缺的绑定（模型 / 知识库 / 记忆）。

        判定方式是「拿源配置的意图和落地行的实际值比」而不是解析 warning 文本：
        跨租户的东西（模型、知识库）本来就不可能按 id 命中，落地会置空，这里把
        置空的那些逐条报给用户去补。
        """

        app_uuid = _coerce_uuid(app_id)
        if app_uuid is None:
            return []
        cfg = self.db.execute(
            select(AgentConfig).where(
                AgentConfig.app_id == app_uuid, AgentConfig.is_active.is_(True)
            )
        ).scalars().first()
        if cfg is None:
            return []

        pending: list[dict[str, Any]] = []
        model_ref = member_config.get("default_model_config_ref")
        if isinstance(model_ref, dict) and model_ref and not cfg.default_model_config_id:
            pending.append(
                {
                    "kind": "model",
                    "name": model_ref.get("name"),
                    "ref": str(model_ref.get("id") or model_ref.get("name") or ""),
                    "required": True,
                }
            )

        # 知识库只按 id 解析（`_resolve_kb`），跨租户必然置空；只要源配置里有
        # 知识库而落地行一个都没留下，就整项报给用户，不去猜哪些「其实是同名的
        # 另一个库」。
        source_kb = _kb_labels(member_config.get("knowledge_retrieval"))
        if source_kb and not _kb_ids(cfg.knowledge_retrieval):
            pending.extend(
                {"kind": "knowledge_base", "name": label, "ref": label, "required": True}
                for label in source_kb
            )

        memory = member_config.get("memory") or {}
        if isinstance(memory, dict) and memory.get("memory_config_id"):
            if not (cfg.memory or {}).get("memory_config_id"):
                ref = str(memory.get("memory_config_id"))
                pending.append(
                    {"kind": "memory", "name": ref, "ref": ref, "required": True}
                )
        return pending

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

        # §8.2 两种应用包：`release_snapshot`（租户投稿，下面按 release 快照复制）
        # 与 `app_dsl`（运营导入 YAML，安装走 `import_dsl`）。按内容而不是只按
        # `format` 分支：`format` 是同事侧新加的键，存量包与运营侧当前产物都没有
        # 它，光看它会把结构不一样的包送进错误路径。
        if isinstance(payload.get("dsl"), dict):
            return self._land_app_dsl(
                dsl=payload["dsl"],
                payload=payload,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=owner_id,
                dep_map=dep_map,
                missing=missing,
                display=display,
            )

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
            # 子 Agent 是草稿落地（没有 release），集群在它们发布前不能发布；
            # 把每个子 Agent 和它落地后仍缺的绑定一起回给用户（§3.4）。
            self._pending_agents.append(
                {
                    "local_resource_id": str(app.id),
                    "name": app.name,
                    "publish_required": app.current_release_id is None,
                    "pending_bindings": self._unresolved_bindings(
                        app.id, member.get("config") or {}
                    ),
                }
            )

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

    def _land_app_dsl(
            self,
            *,
            dsl: dict[str, Any],
            payload: dict[str, Any],
            tenant_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: uuid.UUID,
            dep_map: dict[str, str],
            missing: list[dict[str, Any]],
            display: dict[str, Any],
    ) -> dict[str, Any]:
        """落一个 `format=app_dsl` 的应用包（运营导入 YAML，§8.2）。

        `dsl` 是应用 YAML 的结构，安装时按 `import_dsl` 走，与「控制台导入
        YAML」同一条路径 —— 市场安装不该有第二套落地语义。三个约束与
        `bundle_install_service` 顶部那段注释一致：

        * `commit=False`：整个安装是外层一个事务；
        * `dep_map`：随包的技能/工具 ref 已在本事务里落到目标空间，按 id 绑定；
        * `resolve_cross_app_refs=False`：DSL 里的子 Agent 由 `cluster[]` 带进来
          并由 `import_dsl` 自己落地/复用，不能再按名字去目标空间里找。

        `payload.config` 在这条路径上是空的：DSL 自己带配置。
        """

        warnings: list[str] = []
        # `import_dsl` 把 `cluster[]` 的落地结果（复用/新建/是否需要发布）写进这里，
        # 安装响应据此组装 `pending_agents[]`。
        cluster_report: list[dict[str, Any]] = []
        member = dsl.get("app") or {}
        if not isinstance(member, dict) or not member.get("name"):
            # 运营导入的包可能没写 app.name（用了 display.name）：补一份最小的
            # app 元信息，而不是让 import_dsl 直接报「不支持的应用类型」。
            dsl = {
                **dsl,
                "app": {
                    **(member if isinstance(member, dict) else {}),
                    "name": display.get("name") or "导入应用",
                    "description": display.get("summary"),
                    # `dsl["app"]` 的类型没有上游保证（install 只校验 dsl 是 dict），
                    # 非 dict 的真值（list/str）也会走进这个分支，不能直接 .get。
                    "type": (member.get("type") if isinstance(member, dict) else None)
                    or payload.get("app_type"),
                },
            }
        root, warnings = self.dsl_service.import_dsl(
            dsl,
            workspace_id,
            tenant_id,
            user_id,
            commit=False,
            dep_map=dep_map,
            resolve_cross_app_refs=False,
            origin_columns=self._origin_columns(),
            cluster_report=cluster_report,
        )
        missing.extend(
            _missing("app", display.get("name"), warning) for warning in warnings
        )
        self._record_dsl_pending_agents(dsl, cluster_report)
        return {
            "id": str(root.id),
            "name": root.name,
            "app_type": root.type,
            "workspace_id": str(root.workspace_id),
        }

    def _record_dsl_pending_agents(
            self, dsl: dict[str, Any], cluster_report: list[dict[str, Any]]
    ) -> None:
        """把 `import_dsl` 落地的 `cluster[]` 成员折进 `pending_agents[]`。

        `cluster_report` 只记「哪个 local_id 落成了哪个 app_id、是否复用、有没有
        release」；用户真正要补的是每个子 Agent 缺的模型/知识库，所以按
        `local_id` 回 `dsl["cluster"]` 里取源配置，再和落地行比对。复用已有
        app 的成员不报待绑定（配置未改动，缺什么本来就缺着，不属于本次安装引入）。
        """

        members = {
            str(member.get("local_id")): member
            for member in (dsl.get("cluster") or [])
            if isinstance(member, dict)
        }
        for item in cluster_report:
            if item.get("reused"):
                continue
            member = members.get(str(item.get("local_id"))) or {}
            self._pending_agents.append(
                {
                    "local_resource_id": item.get("app_id"),
                    "name": item.get("name"),
                    "publish_required": not item.get("published"),
                    "pending_bindings": self._unresolved_bindings(
                        item.get("app_id"), member.get("config") or {}
                    ),
                }
            )

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
                    # 纯工作流不能兜成 `workflow`：类型丢了会让落地后的应用变成
                    # 可对话工作流。优先用包里的值，其次按应用类型。
                    "workflow_type": config.get("workflow_type") or app_type,
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


def _market_ref_of(
        entry: dict[str, Any],
        index: dict[str, dict[str, str]] | None = None,
) -> dict[str, str] | None:
    """市场引用。扁平字段优先，旧包的嵌套 `market_ref` 仍可读。

    `submission_id` 在父包里只是审核前的占位。整包合并后真正的资源 id 写在
    `market_sync.dependency_submissions` 里，由 `index` 传进来。合并结果优先于
    父包里冻结的引用。
    """

    index = index or {}

    def complete(raw: dict[str, Any] | None) -> dict[str, str] | None:
        if not isinstance(raw, dict):
            return None
        resource_id = raw.get("resource_id") or raw.get("market_resource_id")
        version = raw.get("version")
        if not resource_id or not version:
            return None
        ref = {"resource_id": str(resource_id), "version": str(version)}
        content_hash = raw.get("content_hash")
        if isinstance(content_hash, str) and content_hash:
            ref["content_hash"] = content_hash
        return ref

    submission_id = entry.get("submission_id")
    if submission_id and str(submission_id) in index:
        resolved = complete(index[str(submission_id)])
        if resolved:
            return resolved
    local_ref = entry.get("ref")
    if local_ref and str(local_ref) in index:
        resolved = complete(index[str(local_ref)])
        if resolved:
            return resolved
    direct = complete(entry)
    if direct:
        return direct
    legacy = entry.get("market_ref")
    if isinstance(legacy, dict):
        return complete(legacy)
    return None


def _missing(kind: str, name: Any, reason: Any) -> dict[str, Any]:
    return {"kind": kind, "name": name, "reason": str(reason)}


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _kb_labels(kr: Any) -> list[str]:
    """源配置里声明的知识库展示名（优先 `_ref.name`，退回 id）。"""

    if not isinstance(kr, dict):
        return []
    labels: list[str] = []
    for kb in kr.get("knowledge_bases") or []:
        if not isinstance(kb, dict):
            continue
        ref = kb.get("_ref") if isinstance(kb.get("_ref"), dict) else {}
        label = ref.get("name") or kb.get("kb_id") or ref.get("id")
        if label:
            labels.append(str(label))
    return labels


def _kb_ids(kr: Any) -> list[str]:
    """落地行实际留下的知识库 id；空列表 = 一个都没匹配上。"""

    if not isinstance(kr, dict):
        return []
    return [
        str(kb.get("kb_id"))
        for kb in (kr.get("knowledge_bases") or [])
        if isinstance(kb, dict) and kb.get("kb_id")
    ]


def _skill_meta_overrides(
        entries: list[dict[str, Any]],
        overrides: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """把「添加技能到企业」弹窗的三个可编辑项折成 `import_package(meta=...)`。

    identifier 用 `SKILL.md` frontmatter 里的 name：独立技能的 zip 把 `SKILL.md`
    放在根，`_split_members` 的 member key 正是它（`_root_prefix` 在这种情况下
    不剥目录）。插件不在这里处理——多成员时目录会先被剥掉，identifier 会随包
    布局变化，逐成员覆盖不稳定，插件只改插件级名称与描述。
    """

    if not entries:
        return None
    entry = entries[0]
    payload = entry.get("payload") or {}
    frontmatter = payload.get("frontmatter_raw") or {}
    identifier = str(
        frontmatter.get("name") or payload.get("call_name") or entry.get("name") or ""
    ).strip()
    if not identifier:
        return None
    # 三个覆盖项都先 strip 再判空：「  」是真值，直接写进去会变成空串覆盖。
    # `import_package` 对「提供了 call_name 但为空」是硬报错
    # （`skill_package_service` 的成员级校验），用户会为自己没填的字段挨一条
    # 错误提示；空 `name` 同理会把技能名清空。全空 = 没改任何东西。
    item: dict[str, Any] = {"identifier": identifier}
    for key, field in (
            ("display_name", "name"),
            ("description", "description"),
            ("call_name", "call_name"),
    ):
        value = str(overrides.get(key) or "").strip()
        if value:
            item[field] = value
    # 只有 identifier 等于没改任何东西：不传 meta 才不会触发 identifier 校验
    return [item] if len(item) > 1 else None


def _slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.lower())
    return cleaned.strip("-") or "skill"


__all__ = ["BundleInstallService"]
