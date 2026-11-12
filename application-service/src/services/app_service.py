"""Application definition-plane service: app CRUD, sharing, release and publishing.

Mirrors the monolith's AppService behavior for the app-definition plane: app
create/update/delete/copy/list/serialization, release/rollback and release
snapshot reads, the ontology binding seam, and Agent/Workflow draft config
reads and writes. Shared-app methods live in ``app_share_service``.

Provides app-management business logic:
- App creation, update and queries
- Persistence construction for Agent / multi-agent / workflow configs
- Agent and Workflow config reads/writes (including shared-app release
  snapshot fallback and same-key cache invalidation)
- Publish (snapshot build + model gate + pinned sub-agent/agent references)
  and rollback
"""
import copy
import datetime
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, column, exists, func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from ..cache.workflow_cache import delete_workflow_config
from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..memory.ontology_binding import ONTOLOGY_BINDING_PLUGIN, OntologyUpstreamError
from ..models import (
    AgentConfig,
    App,
    AppAsset,
    AppAssetInstallation,
    AppRelease,
    AppShare,
    AssetStatus,
    InstallStatus,
    LinkMode,
    MultiAgentConfig,
    WorkflowConfig,
)
from ..models.app_model import AppStatus, AppType
from ..plugins import get_plugin
from ..repositories import cross_domain_read
from ..repositories.app_repository import AppRepository, get_apps_by_id
from ..repositories.multi_agent_repository import MultiAgentConfigRepository
from ..repositories.workflow_repository import WorkflowConfigRepository
from ..schemas import app_schema, workflow_schema
from ..utils.app_config_utils import model_parameters_to_dict
from ..utils.datetime_utils import utcnow_naive
from ..utils.model_config_refs import config_ref_ids
from ..utils.workflow_template_loader import load_workflow_template
from ..utils.workflow_validator import WorkflowValidator
from .agent_config_converter import AgentConfigConverter
from .asset_release_resolver import AssetReleaseResolver
from .category_service import CategoryService
from .model_ref_gate import assert_refs_publishable, raise_model_unavailable, resolve_invoke_ref
from .multi_agent_release_resolver import POLICY_PINNED, resolve_effective_release_id
from .tool_service import ToolService
from .workflow_service import WorkflowService

logger = logging.getLogger(__name__)


def _reference_install_app_ids(workspace_id: uuid.UUID):
    """Apps reachable in ``workspace_id`` through a **read-only** catalog install.

    资源中心取代了空间到空间的共享，所以「共享给我」现在有两个来源：旧的
    ``app_shares`` 行，以及 ``app_asset_installations`` 里 ``link_mode = reference``
    的安装。只读引用**不建本地应用**（``local_app_id`` 为 NULL），因此不并进这条
    子查询，这些应用对列表接口就是隐形的——而详情/对话的鉴权
    （``AssetReleaseResolver``）早就认了安装行，于是出现「能打开但列表里没有」。

    ``link_mode = copy`` 的安装**故意排除**：副本本身已是目标空间的新应用，会以自有
    应用的身份出现在「我的应用」里，并进来会让同一个应用在两个 tab 各出一条。

    ``app_assets.status = published`` 与 resolver 的口径一致：取消共享后只读引用立即
    不可读，也就不该再出现在列表里。
    """

    return (
        select(AppAsset.source_app_id)
        .join(AppAssetInstallation, AppAssetInstallation.asset_id == AppAsset.id)
        .where(
            AppAssetInstallation.target_workspace_id == workspace_id,
            AppAssetInstallation.status == InstallStatus.ACTIVE,
            AppAssetInstallation.link_mode == LinkMode.REFERENCE,
            AppAsset.status == AssetStatus.PUBLISHED,
            AppAsset.is_active.is_(True),
        )
    )


def _pinned_name_app_ids(pattern: str, workspace_id: uuid.UUID):
    """Apps whose **pinned catalog name** matches ``pattern`` (already lowered/%-wrapped).

    列表里只读引用显示的是条目钉住版本的名字（`_convert_to_schema` 的
    `install_display` 覆盖），所以按名字搜索不能只看 `apps.name`：源应用改名后
    `apps.name` 已经是新名，而界面显示的还是快照名。这里把快照名也纳入搜索。

    ``workspace_id`` 与 `_reference_install_app_ids` 同口径：只看装进**本空间**的
    安装行。少了它这条子查询会跨所有空间扫描，别处空间钉住的名字会把本空间的应用
    捞进搜索结果（名字与搜索词并不匹配）。

    ``AppRelease.is_active`` 也与 resolver 对齐：`resolve_release` 见到已下线的
    版本直接抛 `RELEASE_NOT_FOUND`，那条引用读不出来，就不该再被快照名搜到。

    ``App.workspace_id != workspace_id`` 是展示口径的另一半：只有「别人的应用」
    才会以快照名展示（`_build_serialization_context` 只收
    `workspace_id != current_workspace_id` 的行），本空间自有应用即使被拉进本空间，
    列表里显示的仍是 `App.name`。不排除它们就会出现「按 X 搜到一行、名字显示成
    Y」；顺带也让这条 OR 谓词在 `include_shared=False`（scope 恰好是
    `App.workspace_id == workspace_id`）下恒不成立，不可能把外空间应用捞进来。
    """

    return (
        select(AppAsset.source_app_id)
        .join(AppAssetInstallation, AppAssetInstallation.asset_id == AppAsset.id)
        .join(AppRelease, AppRelease.id == AppAsset.published_release_id)
        .join(App, App.id == AppAsset.source_app_id)
        .where(
            App.workspace_id != workspace_id,
            AppAssetInstallation.target_workspace_id == workspace_id,
            AppAssetInstallation.status == InstallStatus.ACTIVE,
            AppAssetInstallation.link_mode == LinkMode.REFERENCE,
            AppAsset.status == AssetStatus.PUBLISHED,
            AppAsset.is_active.is_(True),
            AppRelease.is_active.is_(True),
            func.lower(AppRelease.name).like(pattern),
        )
    )


def _store_usage_examples(items: list[str] | None) -> list[dict[str, Any]]:
    """list[str] -> [{'order': n, 'content': s}]，对外按 order 展开（方案 §4.3）。"""

    return [
        {"order": index, "content": text}
        for index, text in enumerate(items or [], start=1)
    ]


def _expand_usage_examples(value: Any) -> list[str]:
    """展开为前端直接渲染的字符串数组；兼容历史结构体与裸字符串两种存法。"""

    if not value:
        return []
    expanded: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("content")
            if isinstance(text, str) and text.strip():
                expanded.append(text.strip())
        elif isinstance(item, str) and item.strip():
            expanded.append(item.strip())
    return expanded


@dataclass
class _AppSerializationContext:
    """Batched lookups shared by one serialization pass (avoids N+1 queries).

    The host serializes each app with lazy ORM relationships; the service-side
    models have the cross-domain relationships omitted, so the same values are
    loaded in batches up front. Lookup keys are application ids (shares,
    release versions); workspace/user rows are keyed by their own ids.
    """

    shares_by_app: dict[uuid.UUID, AppShare] = field(default_factory=dict)
    workspaces: dict[uuid.UUID, Any] = field(default_factory=dict)
    usernames: dict[uuid.UUID, str] = field(default_factory=dict)
    release_versions: dict[uuid.UUID, str] = field(default_factory=dict)
    # app_id -> (source_workspace_id, permission, shared_at)；资源中心只读引用没有
    # app_shares 行，来源空间与权限只能从安装记录取（见
    # `_build_serialization_context`）。
    install_sources: dict[uuid.UUID, tuple[uuid.UUID, str, Any]] = field(
        default_factory=dict
    )
    # app_id -> 条目钉住快照的展示字段 (name, description, icon, icon_type)。
    # 只读引用消费的是**条目钉住的快照**（`AssetReleaseResolver` 对 reference
    # 安装返回 `asset.published_release_id`），所以 name/icon 取快照而不是 `apps`
    # 当前行的值；description 取 `app_assets.description`——那是上架时冻结、资源
    # 中心详情原样返回的那一份（`get_asset` 的 `description`），不是源应用现在的
    # 草稿描述。取错任何一项都会出现「资源中心叫 A、列表叫 B」。
    install_display: dict[
        uuid.UUID, tuple[str, str | None, str | None, str | None]
    ] = field(default_factory=dict)


class AppService:
    """App service class.

    Handles all app-related business logic, following the single-responsibility
    principle.
    """

    def __init__(self, db: Session):
        """Initialize the app service.

        Args:
            db: Database session (management-plane sync session)
        """
        self.db = db
        self.app_repo = AppRepository(self.db)

    # ==================== Private helper methods ====================

    def _validate_workspace_access(self, app: App, workspace_id: uuid.UUID | None) -> None:
        """Validate workspace access (strict mode, used for write operations).

        Args:
            app: App object
            workspace_id: Workspace ID

        Raises:
            BusinessException: When the app is not in the given workspace
        """
        if workspace_id is not None and app.workspace_id != workspace_id:
            logger.warning(
                "工作空间访问被拒",
                extra={"app_id": str(app.id), "workspace_id": str(workspace_id)}
            )
            raise BusinessException("应用不在指定工作空间中", BizCode.WORKSPACE_NO_ACCESS)

    def _check_app_accessible(self, app: App, workspace_id: uuid.UUID | None) -> bool:
        """Check whether the app is accessible (including shared apps).

        The catalog replaced workspace-to-workspace sharing: accessibility for a
        foreign workspace now means an active catalog installation (§6.1). The
        legacy `app_shares` row is still honoured inside
        ``AssetReleaseResolver`` so un-backfilled history keeps working (§11.1 M3).
        """

        if workspace_id is None:
            return True

        # 1. Check whether it is an app of this workspace
        if app.workspace_id == workspace_id:
            return True

        # 2. Shared into this workspace (catalog installation, then legacy share)
        return AssetReleaseResolver(self.db).can_access(
            app_id=app.id, workspace_id=workspace_id
        )

    def _validate_app_accessible(self, app: App, workspace_id: uuid.UUID | None) -> None:
        """Validate app accessibility (including shared apps, for read-only ops).

        Args:
            app: App object
            workspace_id: Workspace ID

        Raises:
            BusinessException: When the app is not accessible
        """
        if not self._check_app_accessible(app, workspace_id):
            logger.warning(
                "应用访问被拒",
                extra={"app_id": str(app.id), "workspace_id": str(workspace_id)}
            )
            raise BusinessException("应用不可访问", BizCode.WORKSPACE_NO_ACCESS)

    def _unique_app_name(self, name: str, workspace_id: uuid.UUID, app_type: AppType) -> str:
        """Generate a unique app name, checking own and shared-to-workspace apps."""
        existing = {r[0] for r in self.db.query(App.name).filter(
            App.workspace_id == workspace_id,
            App.type == app_type,
            App.is_active.is_(True)
        ).all()}
        shared_names = {r[0] for r in self.db.query(App.name).join(
            AppShare, AppShare.source_app_id == App.id
        ).filter(
            AppShare.target_workspace_id == workspace_id,
            App.type == app_type,
            App.is_active.is_(True)
        ).all()}
        existing |= shared_names

        # 资源中心只读引用没有 app_shares 行，但它们同样占着本空间可见的名字，
        # 不并进来就会允许创建同名应用，列表里出现两行同名。注意取的是**钉住版本
        # 的名字**（`_convert_to_schema` 就是这么展示的），不是 `App.name`：源应用
        # 改名后列表里显示的还是快照名。
        # `App.is_active` 必须保留：源应用被软删除后列表里看不到它（`list_apps`
        # 的 base filter），再算它占名只会给新应用加无谓的后缀。
        ref_names = {r[0] for r in self.db.query(AppRelease.name).join(
            AppAsset, AppAsset.published_release_id == AppRelease.id
        ).join(
            AppAssetInstallation, AppAssetInstallation.asset_id == AppAsset.id
        ).join(
            App, App.id == AppAsset.source_app_id
        ).filter(
            AppAssetInstallation.target_workspace_id == workspace_id,
            AppAssetInstallation.status == InstallStatus.ACTIVE,
            AppAssetInstallation.link_mode == LinkMode.REFERENCE,
            AppAsset.status == AssetStatus.PUBLISHED,
            AppAsset.is_active.is_(True),
            # 按 `App.type` 而不是 `AppRelease.type`：占名要算的是「这一行会不会在
            # type=T 的列表里跟新应用撞名」，而列表过滤的就是 `App.type`
            # （`list_apps` 的 `App.type == type_list[0]`），展示的 type 也取
            # `app.type`。上面两个分支（自有应用、legacy 共享）同样按 `App.type`。
            # 改用快照 type 会让三个分支口径分裂：快照 type 与 App.type 一旦不一致，
            # 这里就漏掉一个列表里真实存在的同名行，生成出重复名字。
            App.type == app_type,
            # 已下线的钉住版本读不出来（resolver 抛 RELEASE_NOT_FOUND），不占名
            AppRelease.is_active.is_(True),
            App.is_active.is_(True),
        ).all()}
        existing |= ref_names

        if name not in existing:
            return name
        counter = 1
        while f"{name}({counter})" in existing:
            counter += 1
        return f"{name}({counter})"

    def _get_share_permission(self, app: App, workspace_id: uuid.UUID | None) -> str | None:
        """Get the share permission of a shared app.

        Returns:
            None: Not a shared app (it belongs to this workspace)
            'readonly': Read-only share
            'editable': Editable share
        """
        if workspace_id is None or app.workspace_id == workspace_id:
            return None  # Own-workspace app, not a share

        stmt = select(AppShare).where(
            AppShare.source_app_id == app.id,
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active.is_(True)
        )
        share = self.db.scalars(stmt).first()
        return share.permission if share else None

    def _validate_app_writable(self, app: App, workspace_id: uuid.UUID | None) -> None:
        """Validate that the app config is writable.

        Only an app owned by the request workspace may be changed. Legacy
        ``AppShare.permission=editable`` records used to bypass this guard and
        mutate the source app; editable resource-center sharing now creates a
        local copy instead, so legacy shares are read-only during migration.
        """
        if workspace_id is None or app.workspace_id == workspace_id:
            return

        logger.warning(
            "应用写操作被拒",
            extra={"app_id": str(app.id), "workspace_id": str(workspace_id)},
        )
        raise BusinessException("共享应用不可修改配置", BizCode.WORKSPACE_NO_ACCESS)

    def _get_app_or_404(self, app_id: uuid.UUID) -> App:
        """Get the app or raise a not-found exception.

        Args:
            app_id: App ID

        Returns:
            App: App object

        Raises:
            ResourceNotFoundException: When the app does not exist
        """
        app = get_apps_by_id(self.db, app_id)
        if not app:
            logger.warning("应用不存在", extra={"app_id": str(app_id)})
            raise ResourceNotFoundException(
                "应用", str(app_id), code=BizCode.APP_NOT_FOUND
            )
        return app

    def _create_agent_config(
            self,
            app_id: uuid.UUID,
            config_data: app_schema.AgentConfigCreate,
            now: datetime.datetime,
            *,
            workspace_id: uuid.UUID | None = None,
            tenant_id: uuid.UUID | None = None
    ) -> None:
        """Create an Agent config (internal method).

        Args:
            app_id: App ID
            config_data: Agent config data
            now: Current time
            workspace_id: Workspace ID (tenant fallback lookup)
            tenant_id: Tenant ID (skips the workspace lookup)
        """
        storage_data = AgentConfigConverter.to_storage_format(config_data)
        tools = storage_data.get("tools") or []
        if tools:
            resolved = tenant_id or (
                cross_domain_read.get_workspace_tenant_id(self.db, workspace_id)
                if workspace_id is not None
                else None
            )
            if resolved is None:
                raise BusinessException("应用所属工作空间不存在", BizCode.NOT_FOUND)
            storage_data["tools"] = ToolService(self.db).validate_tool_selection(
                resolved, tools
            )

        agent_cfg = AgentConfig(
            id=uuid.uuid4(),
            app_id=app_id,
            system_prompt=config_data.system_prompt,
            default_model_config_id=config_data.default_model_config_id,
            model_parameters=storage_data.get("model_parameters"),
            knowledge_retrieval=storage_data.get("knowledge_retrieval"),
            memory=storage_data.get("memory"),
            variables=storage_data.get("variables", []),
            tools=storage_data.get("tools", []),
            skills=storage_data.get("skills", {}),
            features=storage_data.get("features", {}),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(agent_cfg)
        logger.debug("Agent 配置已创建", extra={"app_id": str(app_id)})

    def _create_workflow_config(
            self,
            app_id: uuid.UUID,
            data,
            now: datetime.datetime
    ):
        workflow_service = WorkflowService(self.db)
        config_dict = workflow_service._prepare_workflow_config_dict(
            nodes=[node.model_dump() for node in data.nodes] if data.nodes else [],
            edges=[edge.model_dump() for edge in data.edges] if data.edges else [],
            variables=[var.model_dump() for var in data.variables] if data.variables else [],
            environment_variables=[
                var.model_dump() for var in data.environment_variables
            ] if getattr(data, "environment_variables", None) else [],
            execution_config=data.execution_config.model_dump() if data.execution_config else {},
            features=data.features if data.features else {},
            triggers=[trigger.model_dump() for trigger in data.triggers] if data.triggers else [],
            workflow_type=getattr(data, "workflow_type", None),
        )
        workflow_cfg = WorkflowConfig(
            id=uuid.uuid4(),
            app_id=app_id,
            nodes=config_dict["nodes"],
            edges=config_dict["edges"],
            variables=config_dict["variables"],
            environment_variables=config_dict["environment_variables"],
            execution_config=config_dict["execution_config"],
            features=config_dict["features"],
            triggers=config_dict["triggers"],
            is_active=True,
            created_at=now,
            updated_at=now
        )
        self.db.add(workflow_cfg)

    def _create_multi_agent_config(
            self,
            app_id: uuid.UUID,
            config_data: dict[str, Any],
            now: datetime.datetime
    ) -> None:
        """Create a multi-agent config (internal method).

        Args:
            app_id: App ID
            config_data: Multi-agent config data (dict)
            now: Current time
        """
        # Convert the dict into MultiAgentConfigCreate
        from ..schemas.multi_agent_schema import (
            ExecutionConfig,
            MultiAgentConfigCreate,
            RoutingRule,
            SubAgentConfig,
        )

        # Convert sub_agents
        sub_agents = [SubAgentConfig(**sa) for sa in config_data.get('sub_agents', [])]

        # Convert routing_rules (if any)
        routing_rules = None
        if config_data.get('routing_rules'):
            routing_rules = [RoutingRule(**rr) for rr in config_data['routing_rules']]

        # Convert execution_config
        execution_config = ExecutionConfig(**config_data.get('execution_config', {}))

        # Build the MultiAgentConfigCreate object
        config = MultiAgentConfigCreate(
            master_agent_id=config_data['master_agent_id'],
            orchestration_mode=config_data['orchestration_mode'],
            sub_agents=sub_agents,
            routing_rules=routing_rules,
            execution_config=execution_config,
            aggregation_strategy=config_data.get('aggregation_strategy', 'merge')
        )

        # Verify the master agent exists
        master_agent = self.db.get(AgentConfig, config.master_agent_id)
        if not master_agent:
            raise ResourceNotFoundException("主 Agent", str(config.master_agent_id))

        # Verify each sub-agent exists
        for sub_agent in config.sub_agents:
            agent = self.db.get(AgentConfig, sub_agent.agent_id)
            if not agent:
                raise ResourceNotFoundException("子 Agent", str(sub_agent.agent_id))

        # Create the multi-agent config
        # Convert UUIDs to strings for JSON serialization
        sub_agents_data = []
        for sub_agent in config.sub_agents:
            sa_dict = sub_agent.model_dump()
            sa_dict['agent_id'] = str(sa_dict['agent_id'])  # UUID -> str
            sub_agents_data.append(sa_dict)

        routing_rules_data = None
        if config.routing_rules:
            routing_rules_data = []
            for rule in config.routing_rules:
                rule_dict = rule.model_dump()
                rule_dict['target_agent_id'] = str(rule_dict['target_agent_id'])  # UUID -> str
                routing_rules_data.append(rule_dict)

        multi_agent_cfg = MultiAgentConfig(
            id=uuid.uuid4(),
            app_id=app_id,
            master_agent_id=config.master_agent_id,
            orchestration_mode=config.orchestration_mode,
            sub_agents=sub_agents_data,
            routing_rules=routing_rules_data,
            execution_config=config.execution_config.model_dump(),
            aggregation_strategy=config.aggregation_strategy,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(multi_agent_cfg)
        logger.debug(
            "多 Agent 配置已创建",
            extra={"app_id": str(app_id), "mode": config.orchestration_mode},
        )

    def _load_ontology_map(
            self,
            app_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict] | None:
        """Batch-load the effective ontology (ontology sub-object) per app.

        Returns:
            - Community edition (no ontology module): None; caller sets ontology
              to null
            - Enterprise edition: {app_id: ontology dict}; unbound apps fall back
              to the system default ontology block
        On aggregation failure, log an error and degrade to None without
        affecting the main list flow.
        """
        binder = get_plugin(ONTOLOGY_BINDING_PLUGIN)
        if binder is None:
            return None
        if not app_ids:
            return {}
        try:
            if getattr(binder, "uses_local_db", True):
                # Wrap in a SAVEPOINT: if the aggregation SQL fails, only the
                # savepoint rolls back and the outer transaction stays usable;
                # otherwise the PG transaction enters aborted state and later
                # serialization queries fail.
                with self.db.begin_nested():
                    return binder.load_app_ontology_batch(self.db, app_ids)
            # Remote binder (memory-domain callback): never hold a transaction
            # across the HTTP call. Callers serialize rows before reaching here.
            self.db.rollback()
            return binder.load_app_ontology_batch(self.db, app_ids)
        except Exception as e:
            logger.error("应用本体聚合失败", extra={"error": str(e)}, exc_info=True)
            return None

    # ==================== Serialization ====================

    def _build_serialization_context(
            self,
            apps: list[App],
            current_workspace_id: uuid.UUID
    ) -> _AppSerializationContext:
        """Preload the share/workspace/username/release lookups for one pass."""
        shared_app_ids = [a.id for a in apps if a.workspace_id != current_workspace_id]
        context = _AppSerializationContext()

        if shared_app_ids:
            stmt = select(AppShare).where(
                AppShare.source_app_id.in_(shared_app_ids),
                AppShare.target_workspace_id == current_workspace_id,
                AppShare.is_active.is_(True)
            )
            for share in self.db.scalars(stmt).all():
                context.shares_by_app.setdefault(share.source_app_id, share)

            # 从资源中心拉来的应用没有 app_shares 行（只读引用甚至没有本地 app），
            # 来源空间与权限只能从安装记录取，否则列表里「来源」一列是空的。
            # 同时取条目钉住版本的展示字段：只读引用读的就是这份快照
            # （`AssetReleaseResolver` 对 reference 安装返回 `published_release_id`），
            # 名称/图标/描述必须与资源中心一致，否则源应用改名后两边对不上。
            missing = [
                app_id for app_id in shared_app_ids
                if app_id not in context.shares_by_app
            ]
            if missing:
                rows = self.db.execute(
                    select(
                        AppAsset.source_app_id,
                        AppAsset.source_workspace_id,
                        AppAssetInstallation.permission,
                        AppAssetInstallation.created_at,
                        AppAsset.description.label("asset_description"),
                        AppAsset.display_payload.label("asset_display_payload"),
                        AppRelease,
                    )
                    .join(AppAssetInstallation, AppAssetInstallation.asset_id == AppAsset.id)
                    # 已下线的钉住版本读不出来（`AssetReleaseResolver.resolve_release`
                    # 直接抛 `RELEASE_NOT_FOUND`），所以不能拿它的名字/图标当展示值——
                    # 否则会出现「列表显示 A、按 A 搜索却搜不到（`_pinned_name_app_ids`
                    # 有 is_active 过滤）、A 也不占名」的三处口径分裂。
                    # 用 join 条件而不是 where：`install_sources`（来源/权限）仍需要这
                    # 一行，只是不写 `install_display`，展示回退到 `app.name`。
                    .outerjoin(
                        AppRelease,
                        and_(
                            AppRelease.id == AppAsset.published_release_id,
                            AppRelease.is_active.is_(True),
                        ),
                    )
                    .where(
                        AppAsset.source_app_id.in_(missing),
                        AppAssetInstallation.target_workspace_id == current_workspace_id,
                        AppAssetInstallation.status == InstallStatus.ACTIVE,
                        # 与列表作用域同口径：只有 reference 安装会让「别人的应用」
                        # 出现在本空间的列表里，copy 的那条应用是本空间自有行。
                        AppAssetInstallation.link_mode == LinkMode.REFERENCE,
                        AppAsset.status == AssetStatus.PUBLISHED,
                        AppAsset.is_active.is_(True),
                    )
                    .order_by(AppAssetInstallation.created_at.desc())
                ).all()
                for row in rows:
                    context.install_sources.setdefault(
                        row.source_app_id,
                        (row.source_workspace_id, row.permission, row.created_at),
                    )
                    if row.AppRelease is not None:
                        context.install_display.setdefault(
                            row.source_app_id,
                            (
                                row.AppRelease.name,
                                # 条目冻结的描述与图标，与资源中心详情返回的同一份。
                                row.asset_description,
                                (row.asset_display_payload or {}).get("icon") or None,
                                (row.asset_display_payload or {}).get("icon_type") or None,
                            ),
                        )

            source_workspace_ids = {
                share.source_workspace_id for share in context.shares_by_app.values()
            } | {ws_id for ws_id, _permission, _at in context.install_sources.values()}
            context.workspaces = cross_domain_read.get_workspaces_by_ids(
                self.db, source_workspace_ids
            )
            context.usernames = cross_domain_read.get_usernames_by_ids(
                self.db,
                {share.shared_by for share in context.shares_by_app.values() if share.shared_by}
            )

        release_ids = {a.current_release_id for a in apps if a.current_release_id}
        if release_ids:
            rows = self.db.execute(
                select(AppRelease.id, AppRelease.version_name).where(
                    AppRelease.id.in_(release_ids)
                )
            ).all()
            context.release_versions = {row.id: row.version_name for row in rows}

        return context

    def to_schemas_with_ontology(
            self,
            apps: list[App],
            current_workspace_id: uuid.UUID
    ) -> list[app_schema.App]:
        """Batch-convert apps to schemas and fill the effective ontology.

        Aggregates once per batch to avoid N+1. ontology is None in the
        community edition or when ontology aggregation fails. Rows are
        serialized before the aggregation so a remote binder may end the
        transaction without expiring the ORM objects mid-serialization.
        """
        app_ids = [a.id for a in apps]
        context = self._build_serialization_context(apps, current_workspace_id)
        schemas = [
            self._convert_to_schema(a, current_workspace_id, None, context)
            for a in apps
        ]
        ontology_map = self._load_ontology_map(app_ids)
        if ontology_map is not None:
            # A remote binder ends the transaction, which expires the ORM rows;
            # read the pre-captured ids so the fill loop issues no refresh queries.
            for app_id, schema in zip(app_ids, schemas, strict=True):
                ontology = ontology_map.get(app_id)
                if ontology is not None:
                    schema.ontology = app_schema.AppOntologyInfo(**ontology)
        return schemas

    def to_schema_with_ontology(
            self,
            app: App,
            current_workspace_id: uuid.UUID
    ) -> app_schema.App:
        """Convert a single app to a schema and fill the effective ontology."""
        return self.to_schemas_with_ontology([app], current_workspace_id)[0]

    def _convert_to_schema(
            self,
            app: App,
            current_workspace_id: uuid.UUID,
            ontology: dict | None = None,
            context: _AppSerializationContext | None = None
    ) -> app_schema.App:
        """Convert an App model to a schema and set the is_shared field.

        Args:
            app: App model instance
            current_workspace_id: Current workspace ID
            ontology: Effective ontology (batch-loaded by _load_ontology_map);
                None in the community edition
            context: Batch-preloaded share/release context; built on demand for
                single-row scenarios when omitted

        Returns:
            app_schema.App: App schema
        """
        if context is None:
            context = self._build_serialization_context([app], current_workspace_id)

        is_shared = app.workspace_id != current_workspace_id
        share_permission = None
        source_workspace_name = None
        source_workspace_icon = None
        source_app_version = None
        source_app_is_active = None
        share_id = None
        shared_by = None
        shared_by_name = None
        shared_at = None

        if is_shared:
            share = context.shares_by_app.get(app.id)
            if share:
                share_id = share.id
                share_permission = share.permission
                shared_by = share.shared_by
                shared_at = share.created_at
                shared_by_name = context.usernames.get(share.shared_by)
                source_workspace = context.workspaces.get(share.source_workspace_id)
                if source_workspace:
                    source_workspace_name = source_workspace.name
                    source_workspace_icon = source_workspace.icon
            elif app.id in context.install_sources:
                # 资源中心只读引用：没有 share 行，来源与权限取安装记录
                source_workspace_id, share_permission, shared_at = (
                    context.install_sources[app.id]
                )
                source_workspace = context.workspaces.get(source_workspace_id)
                if source_workspace:
                    source_workspace_name = source_workspace.name
                    source_workspace_icon = source_workspace.icon

        # 只读引用展示的是条目钉住的那一版快照（`AssetReleaseResolver` 对
        # reference 安装返回 `published_release_id`），所以展示字段取快照值，
        # 与资源中心详情（`asset_service.get_asset` 用 release.name）保持一致。
        # 源应用改过名但条目还钉在旧版本时，不覆盖就会出现「资源中心叫 A、
        # 列表叫 B」，用户按 A 搜不到。
        # description 取的是条目上冻结的 `app_assets.description`（资源中心详情
        # 原样返回该字段），不要再回退到 `app.description`——那是源应用当前的草稿
        # 值，跨空间消费它既与详情不一致，也等于读了本不该暴露的实时状态。
        pinned = context.install_display.get(app.id)
        if pinned is not None:
            display_name, display_description, display_icon, display_icon_type = pinned
        else:
            display_name = app.name
            display_description = app.description
            display_icon = app.icon
            display_icon_type = app.icon_type

        # Release version and active status
        source_app_version = context.release_versions.get(app.current_release_id)
        source_app_is_active = app.is_active

        app_dict = {
            "id": app.id,
            "workspace_id": app.workspace_id,
            "created_by": app.created_by,
            "name": display_name,
            "description": display_description,
            "icon": display_icon,
            "icon_type": display_icon_type,
            "type": app.type,
            "visibility": app.visibility,
            "status": app.status,
            "tags": app.tags or [],
            "current_release_id": app.current_release_id,
            "is_active": app.is_active,
            "is_shared": is_shared,
            "share_permission": share_permission,
            "source_workspace_name": source_workspace_name,
            "source_workspace_icon": source_workspace_icon,
            "source_app_version": source_app_version,
            "source_app_is_active": source_app_is_active,
            "share_id": share_id,
            "shared_by": shared_by,
            "shared_by_name": shared_by_name,
            "shared_at": shared_at,
            "ontology": ontology,
            # 来源（§11.2）：只有本地副本记来源，市场状态按需回填
            "origin": str(getattr(app, "origin", None) or "local"),
            "external": (
                {
                    "source": getattr(app, "external_source", None) or "community_market",
                    "resource_id": getattr(app, "external_resource_id", None),
                    "version": getattr(app, "external_version", None),
                    "status": None,
                }
                if str(getattr(app, "origin", None) or "local") == "external"
                else None
            ),
            "created_at": app.created_at,
            "updated_at": app.updated_at
        }
        return app_schema.App(**app_dict)

    # ==================== App management ====================

    def get_app(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None
    ) -> App:
        """Get app details.

        Args:
            app_id: App ID
            workspace_id: Workspace ID (for permission checks, supports shared apps)

        Returns:
            App: App object

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app is not accessible
        """
        app = self._get_app_or_404(app_id)
        self._validate_app_accessible(app, workspace_id)
        return app

    def create_app(
            self,
            *,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID,
            data: app_schema.AppCreate,
            tenant_id: uuid.UUID | None = None
    ) -> App:
        """Create an app.

        Args:
            user_id: Creator user ID
            workspace_id: Workspace ID
            data: App creation data
            tenant_id: Current user's tenant ID (for cross-tenant checks when
                binding an ontology)

        Returns:
            App: The created app object

        Raises:
            BusinessException: When creation fails
        """
        logger.info(
            "创建应用",
            extra={"app_name": data.name, "type": data.type, "workspace_id": str(workspace_id)}
        )
        apps = self.app_repo.get_apps_by_name(data.name, data.type, workspace_id)
        if apps:
            raise BusinessException(message="已存在同名应用", code=BizCode.RESOURCE_ALREADY_EXISTS)

        try:
            now = utcnow_naive()

            app = App(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                created_by=user_id,
                name=data.name,
                description=data.description,
                icon=data.icon,
                icon_type=data.icon_type,
                type=data.type,
                visibility=data.visibility,
                status=data.status,
                tags=data.tags or [],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            self.db.add(app)
            self.db.flush()  # Fetch app.id

            # For agent-type apps with a config payload, create the AgentConfig
            if app.type == "agent" and data.agent_config:
                self._create_agent_config(
                    app.id,
                    data.agent_config,
                    now,
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                )

            # For multi_agent-type apps with a config payload, create MultiAgentConfig
            if app.type == "multi_agent" and data.multi_agent_config:
                self._create_multi_agent_config(app.id, data.multi_agent_config, now)

            if app.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW) and data.workflow_config:
                from ..schemas.workflow_schema import WorkflowConfigCreate
                wf_data = (
                    WorkflowConfigCreate(**data.workflow_config)
                    if isinstance(data.workflow_config, dict)
                    else data.workflow_config
                )
                self._create_workflow_config(app.id, wf_data, now)

            # Optional ontology binding: the in-process binder writes it in the
            # same transaction as the app and its config; the remote binder can
            # only run after the local commit (no remote call inside a
            # transaction) and is compensated when it fails.
            binder = get_plugin(ONTOLOGY_BINDING_PLUGIN)
            remote_bind = bool(data.ontology_id) and binder is not None and not getattr(
                binder, "uses_local_db", True
            )
            if remote_bind and tenant_id is None:
                raise BusinessException("缺少租户信息，无法绑定本体", BizCode.INVALID_PARAMETER)
            if data.ontology_id and not remote_bind:
                self._bind_ontology_in_session(app.id, tenant_id, data.ontology_id)

            self.db.commit()
            self.db.refresh(app)

            if remote_bind:
                self._bind_ontology_remote(binder, app, tenant_id, data.ontology_id)

            logger.info("应用创建成功", extra={"app_id": str(app.id), "app_name": app.name})
            return app

        except BusinessException:
            # Keep original business codes for validation errors (e.g. ontology
            # not found 4000 / cross-tenant 3001); do not wrap them as 10001
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            logger.error("应用创建失败", extra={"app_name": data.name, "error": str(e)})
            raise BusinessException(
                f"应用创建失败: {str(e)}", BizCode.INTERNAL_ERROR, cause=e
            ) from e

    def _bind_ontology_in_session(
            self,
            app_id: uuid.UUID,
            tenant_id: uuid.UUID | None,
            ontology_id: uuid.UUID
    ) -> None:
        """Write the app-ontology binding within the current transaction (no commit).

        Only the in-process binder writes here; the remote (callback) binder is
        invoked after the local commit by ``_bind_ontology_remote``. In the
        community edition without any binder the input is ignored with a
        warning (ontology module unavailable).
        """
        binder = get_plugin(ONTOLOGY_BINDING_PLUGIN)
        if binder is None or not getattr(binder, "uses_local_db", True):
            logger.warning(
                "未加载本地本体模块，忽略 ontology_id",
                extra={"app_id": str(app_id), "ontology_id": str(ontology_id)}
            )
            return
        if tenant_id is None:
            raise BusinessException("缺少租户信息，无法绑定本体", BizCode.INVALID_PARAMETER)
        binder.bind_in_session(self.db, app_id, tenant_id, ontology_id)

    def _bind_ontology_remote(
            self,
            binder: Any,
            app: App,
            tenant_id: uuid.UUID | None,
            ontology_id: uuid.UUID,
    ) -> None:
        """Bind after the local commit; soft-delete the app when the callback fails.

        The monolith business code is passed through to the client (validation
        codes like 4000/3001); transport failures surface as 10003.
        """
        try:
            binder.bind_app(app.id, tenant_id, ontology_id)
        except OntologyUpstreamError as exc:
            self._compensate_failed_create(app)
            raise BusinessException(
                str(exc),
                exc.code if exc.code is not None else BizCode.SERVICE_UNAVAILABLE,
                cause=exc,
            ) from exc

    def _compensate_failed_create(self, app: App) -> None:
        try:
            app.is_active = False
            app.updated_at = utcnow_naive()
            self.db.commit()
            logger.warning(
                "本体绑定失败，已软删除刚创建的应用",
                extra={"app_id": str(app.id), "app_name": app.name},
            )
        except Exception as e:
            self.db.rollback()
            logger.error(
                "本体绑定失败后的补偿删除也失败",
                extra={"app_id": str(app.id), "error": str(e)},
            )

    def update_app(
            self,
            *,
            app_id: uuid.UUID,
            data: app_schema.AppUpdate,
            workspace_id: uuid.UUID | None = None
    ) -> App:
        """Update basic app information.

        Args:
            app_id: App ID
            data: Update payload
            workspace_id: Workspace ID (for permission checks)

        Returns:
            App: The updated app object

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app is not in the given workspace
        """
        logger.info("更新应用", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)
        self._validate_app_writable(app, workspace_id)

        changed = False
        for field_name in [
            "name", "description", "icon", "icon_type", "visibility", "status", "tags",
        ]:
            val = getattr(data, field_name, None)
            if val is not None:
                setattr(app, field_name, val)
                changed = True

        if changed:
            app.updated_at = utcnow_naive()
            self.db.commit()
            self.db.refresh(app)
            logger.info("应用更新成功", extra={"app_id": str(app_id)})
        else:
            logger.debug("应用无变更", extra={"app_id": str(app_id)})

        return app

    def delete_app(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None
    ) -> None:
        """Delete an app.

        Args:
            app_id: App ID
            workspace_id: Workspace ID (for permission checks)

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app is not in the given workspace, or the
                ontology unbind callback fails (the app and its shares are
                restored before the error is raised, so the delete can be retried)
        """
        logger.info("删除应用", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)
        self._validate_workspace_access(app, workspace_id)

        # Soft-delete the app
        app.is_active = False

        # Deactivate all share rows of this app in app_shares and bump updated_at;
        # keep their ids so a failed ontology unbind can restore them.
        share_ids = list(self.db.scalars(
            select(AppShare.id).where(
                AppShare.source_app_id == app_id,
                AppShare.is_active.is_(True)
            )
        ).all())
        stmt = sa_update(AppShare).where(
            AppShare.source_app_id == app_id,
            AppShare.is_active.is_(True)
        ).values(
            is_active=False,
            updated_at=utcnow_naive()
        )
        self.db.execute(stmt)

        # A copy installation points at this app through `local_app_id`; leaving it
        # active makes the catalog report a deleted app (`reused=true` + 4007 on
        # read). Retire it here so the next install rebuilds a live copy. The ids
        # come back from the same statement (no separate SELECT, no unbounded
        # `IN (...)`) so a failed ontology unbind can restore exactly these rows.
        installation_ids = list(self.db.scalars(
            sa_update(AppAssetInstallation)
            .where(
                AppAssetInstallation.local_app_id == app_id,
                AppAssetInstallation.status == InstallStatus.ACTIVE,
            )
            .values(
                status=InstallStatus.REMOVED,
                updated_at=utcnow_naive(),
            )
            .returning(AppAssetInstallation.id)
        ).all())

        # Bindings belong to the memory domain: only the remote callback binder
        # can unbind them, after the local commit (no remote call inside a
        # transaction). The in-process binder lives in the monolith and runs its
        # own delete path, so this service never sees one.
        binder = get_plugin(ONTOLOGY_BINDING_PLUGIN)
        remote_unbind = binder is not None and not getattr(binder, "uses_local_db", True)

        self.db.commit()

        if remote_unbind:
            self._unbind_ontology_remote(binder, app, share_ids, installation_ids)

        logger.info(
            "应用删除成功",
            extra={
                "app_id": str(app_id),
                "app_name": app.name,
                "app_type": app.type
            }
        )

    def _unbind_ontology_remote(
            self,
            binder: Any,
            app: App,
            share_ids: list[uuid.UUID],
            installation_ids: list[uuid.UUID],
    ) -> None:
        """Unbind after the local commit; restore the app when the callback fails.

        A soft-deleted app can no longer be looked up (the repository filters on
        is_active), so the delete cannot be retried once the local commit lands:
        the compensation restores the app, its shares and the copy installations
        first, then the failure is surfaced to the caller as a whole-delete
        failure it can retry.
        """
        try:
            binder.unbind_app(app.id)
        except OntologyUpstreamError as exc:
            self._compensate_failed_delete(app, share_ids, installation_ids)
            raise BusinessException(
                str(exc),
                exc.code if exc.code is not None else BizCode.SERVICE_UNAVAILABLE,
                cause=exc,
            ) from exc

    def _compensate_failed_delete(
            self,
            app: App,
            share_ids: list[uuid.UUID],
            installation_ids: list[uuid.UUID],
    ) -> None:
        try:
            app.is_active = True
            app.updated_at = utcnow_naive()
            if share_ids:
                self.db.execute(
                    sa_update(AppShare)
                    .where(AppShare.id.in_(share_ids))
                    .values(is_active=True, updated_at=utcnow_naive())
                )
            # The copy installations were retired for this delete; leaving them
            # removed would make the catalog rebuild a duplicate copy later.
            # Stay on the same predicate (still pointing at this app, still
            # removed) so a row another flow re-pointed in the meantime is not
            # flipped back to active.
            restored_ids = list(self.db.scalars(
                sa_update(AppAssetInstallation)
                .where(
                    AppAssetInstallation.id.in_(installation_ids),
                    AppAssetInstallation.local_app_id == app.id,
                    AppAssetInstallation.status == InstallStatus.REMOVED,
                )
                .values(status=InstallStatus.ACTIVE, updated_at=utcnow_naive())
                .returning(AppAssetInstallation.id)
            ).all()) if installation_ids else []
            self.db.commit()
            logger.warning(
                "本体解绑失败，已恢复应用、共享记录与副本安装",
                extra={
                    "app_id": str(app.id),
                    "share_count": len(share_ids),
                    "installation_count": len(restored_ids),
                },
            )
        except Exception as e:
            self.db.rollback()
            logger.error(
                "本体解绑失败后的补偿恢复也失败",
                extra={"app_id": str(app.id), "error": str(e)},
            )

    def copy_app(
            self,
            *,
            app_id: uuid.UUID,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None,
            new_name: str | None = None
    ) -> App:
        """Copy an app (basic info plus config).

        Args:
            app_id: Source app ID
            user_id: Creator user ID
            workspace_id: Target workspace ID (copies into the source app's
                workspace when None)
            new_name: New app name (defaults to "source name - copy" when None)

        Returns:
            App: The newly copied app object

        Raises:
            ResourceNotFoundException: When the source app does not exist
            BusinessException: When the copy fails
        """
        logger.info("复制应用", extra={"source_app_id": str(app_id)})

        try:
            # Load the source app
            source_app = self._get_app_or_404(app_id)
            self._validate_app_accessible(source_app, workspace_id)

            # Resolve the target workspace
            target_workspace_id = workspace_id or source_app.workspace_id

            # Resolve the new app name
            if not new_name:
                new_name = f"{source_app.name} - 副本"
            new_name = self._unique_app_name(new_name, target_workspace_id, source_app.type)

            now = utcnow_naive()

            # Create the new app (copy basic info)
            new_app = App(
                id=uuid.uuid4(),
                workspace_id=target_workspace_id,
                created_by=user_id,
                name=new_name,
                description=source_app.description,
                icon=source_app.icon,
                icon_type=source_app.icon_type,
                type=source_app.type,
                visibility=source_app.visibility,
                status="draft",  # Copied apps default to draft status
                tags=source_app.tags.copy() if source_app.tags else [],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            self.db.add(new_app)
            self.db.flush()

            # Detect a cross-workspace copy (shared app copied into own workspace)
            is_cross_workspace = target_workspace_id != source_app.workspace_id

            # For cross-workspace copies, load the target workspace tenant_id to
            # determine model config availability
            target_tenant_id = None
            if is_cross_workspace:
                target_ws = cross_domain_read.get_workspace(self.db, target_workspace_id)
                if not target_ws:
                    raise ResourceNotFoundException("工作空间", str(target_workspace_id))
                target_tenant_id = target_ws.tenant_id

            # For agent-type apps, copy the AgentConfig
            if source_app.type == AppType.AGENT:
                source_config = self.db.query(AgentConfig).filter(
                    AgentConfig.app_id == source_app.id
                ).first()

                if source_config:
                    if is_cross_workspace:
                        # Cross-workspace: keep tenant-level model/tools/skills as is;
                        # filter workspace-level knowledge_bases and clear memory_config
                        _, kb_ids = self._collect_resource_ids_from_config(
                            None, source_config.knowledge_retrieval
                        )
                        _, available_kb_ids = self._preload_cross_workspace_resources(
                            target_tenant_id, target_workspace_id, set(), kb_ids
                        )
                        new_model_config_id = source_config.default_model_config_id
                        new_knowledge_retrieval = self._clean_knowledge_retrieval(
                            source_config.knowledge_retrieval, available_kb_ids
                        )
                        new_tools = (
                            copy.deepcopy(source_config.tools)
                            if source_config.tools else []
                        )
                        new_memory = self._clean_memory_cross_workspace(
                            source_config.memory, target_workspace_id
                        )
                        new_skills = (
                            copy.deepcopy(source_config.skills)
                            if source_config.skills else {}
                        )
                    else:
                        new_model_config_id = source_config.default_model_config_id
                        new_knowledge_retrieval = (
                            copy.deepcopy(source_config.knowledge_retrieval)
                            if source_config.knowledge_retrieval else None
                        )
                        new_tools = (
                            copy.deepcopy(source_config.tools)
                            if source_config.tools else []
                        )
                        new_memory = (
                            copy.deepcopy(source_config.memory)
                            if source_config.memory else None
                        )
                        new_skills = (
                            copy.deepcopy(source_config.skills)
                            if source_config.skills else {}
                        )

                    new_config = AgentConfig(
                        id=uuid.uuid4(),
                        app_id=new_app.id,
                        system_prompt=source_config.system_prompt,
                        default_model_config_id=new_model_config_id,
                        model_parameters=(
                            copy.deepcopy(source_config.model_parameters)
                            if source_config.model_parameters else None
                        ),
                        knowledge_retrieval=new_knowledge_retrieval,
                        memory=new_memory,
                        variables=(
                            copy.deepcopy(source_config.variables)
                            if source_config.variables else []
                        ),
                        tools=new_tools,
                        skills=new_skills,
                        features=(
                            copy.deepcopy(source_config.features)
                            if source_config.features else {}
                        ),
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(new_config)

            elif source_app.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
                source_config = self.db.query(WorkflowConfig).filter(
                    WorkflowConfig.app_id == source_app.id
                ).first()

                if source_config:
                    workflow_missing: list[dict[str, Any]] = []
                    nodes = (
                        self._remap_workflow_nodes_cross_workspace(
                            source_config.nodes,
                            target_tenant_id=target_tenant_id,
                            target_workspace_id=target_workspace_id,
                        )
                        if is_cross_workspace
                        else (copy.deepcopy(source_config.nodes or []), workflow_missing)
                    )
                    copied_nodes, workflow_missing = nodes
                    if workflow_missing:
                        logger.warning(
                            "跨空间复制工作流时移除了不可用资源引用",
                            extra={
                                "source_app_id": str(source_app.id),
                                "target_workspace_id": str(target_workspace_id),
                                "missing": workflow_missing,
                            },
                        )
                    new_config = WorkflowConfig(
                        id=uuid.uuid4(),
                        app_id=new_app.id,
                        nodes=copied_nodes,
                        edges=copy.deepcopy(source_config.edges) if source_config.edges else [],
                        variables=(
                            copy.deepcopy(source_config.variables)
                            if source_config.variables else []
                        ),
                        environment_variables=copy.deepcopy(source_config.environment_variables)
                        if source_config.environment_variables else [],
                        execution_config=(
                            copy.deepcopy(source_config.execution_config)
                            if source_config.execution_config else {}
                        ),
                        features=(
                            copy.deepcopy(source_config.features)
                            if source_config.features else {}
                        ),
                        triggers=(
                            copy.deepcopy(source_config.triggers)
                            if source_config.triggers else []
                        ),
                        workflow_type=source_config.workflow_type,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(new_config)

            elif source_app.type == AppType.MULTI_AGENT:
                source_config = self.db.query(MultiAgentConfig).filter(
                    MultiAgentConfig.app_id == source_app.id
                ).first()

                if source_config:
                    # multi_agent: tenant-level model_config_id/sub_agents/routing_rules
                    # are kept as is; master_agent_id (an AppRelease) belongs to the
                    # source workspace, so clear it on cross-workspace copies
                    # supervisor_config carries the master agent's own capability
                    # surface (features/prompt/memory/knowledge/skills/variables/
                    # tools); omitting it degrades the copy to a bare supervisor.
                    new_supervisor_config = self._clone_supervisor_config(
                        source_config.supervisor_config,
                        is_cross_workspace=is_cross_workspace,
                        target_tenant_id=target_tenant_id,
                        target_workspace_id=target_workspace_id,
                    )
                    new_config = MultiAgentConfig(
                        id=uuid.uuid4(),
                        app_id=new_app.id,
                        master_agent_id=(
                            source_config.master_agent_id
                            if not is_cross_workspace else None
                        ),
                        master_agent_name=source_config.master_agent_name,
                        default_model_config_id=source_config.default_model_config_id,
                        model_parameters=(
                            copy.deepcopy(source_config.model_parameters)
                            if source_config.model_parameters else None
                        ),
                        orchestration_mode=source_config.orchestration_mode,
                        sub_agents=(
                            copy.deepcopy(source_config.sub_agents)
                            if source_config.sub_agents else []
                        ),
                        routing_rules=(
                            copy.deepcopy(source_config.routing_rules)
                            if source_config.routing_rules else None
                        ),
                        execution_config=(
                            copy.deepcopy(source_config.execution_config)
                            if source_config.execution_config else {}
                        ),
                        supervisor_config=new_supervisor_config,
                        aggregation_strategy=source_config.aggregation_strategy,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(new_config)

            self.db.commit()
            self.db.refresh(new_app)

            logger.info(
                "应用复制成功",
                extra={
                    "source_app_id": str(app_id),
                    "new_app_id": str(new_app.id),
                    "new_app_name": new_app.name
                }
            )

            return new_app

        except Exception as e:
            self.db.rollback()
            logger.error(
                "应用复制失败",
                extra={"source_app_id": str(app_id), "error": str(e)}
            )
            raise BusinessException(
                f"应用复制失败: {str(e)}", BizCode.INTERNAL_ERROR, cause=e
            ) from e

    def _preload_cross_workspace_resources(
            self,
            target_tenant_id: uuid.UUID | None,
            target_workspace_id: uuid.UUID,
            model_config_ids: set,
            kb_ids: set
    ) -> tuple:
        """Batch-load model configs and knowledge bases to avoid N+1 queries.

        Returns:
            (available_model_ids, available_kb_ids): sets of IDs available in target workspace
        """
        # Batch check model configs by tenant
        available_model_ids = cross_domain_read.get_available_model_ids(
            self.db, model_config_ids, target_tenant_id
        )

        # Batch check knowledge bases
        available_kb_ids: set = set()
        if kb_ids:
            kb_uuids = set()
            for kid in kb_ids:
                try:
                    kb_uuids.add(uuid.UUID(str(kid)))
                except (ValueError, AttributeError):
                    pass

            available_kb_ids = cross_domain_read.get_available_kb_ids(
                self.db, kb_uuids, target_workspace_id
            )

        return available_model_ids, available_kb_ids

    @staticmethod
    def _collect_resource_ids_from_config(
            model_config_id: uuid.UUID | None,
            knowledge_retrieval: dict | None
    ) -> tuple:
        """Extract all model config IDs and knowledge base IDs from an app config."""
        model_ids: set = set()
        kb_ids: set = set()

        if model_config_id:
            model_ids.add(model_config_id)

        if knowledge_retrieval and isinstance(knowledge_retrieval, dict):
            if "knowledge_bases" in knowledge_retrieval:
                for kid in knowledge_retrieval.get("knowledge_bases", []):
                    kb_ids.add(str(kid.get("kb_id")))

        return model_ids, kb_ids

    @staticmethod
    def _is_kb_available(kb_id: str | None, available_kb_ids: set) -> str | None:
        if not kb_id:
            return None
        try:
            return kb_id if uuid.UUID(str(kb_id)) in available_kb_ids else None
        except (ValueError, AttributeError):
            return None

    def _clean_knowledge_retrieval(
            self,
            knowledge_retrieval: dict | None,
            available_kb_ids: set
    ) -> dict | None:
        """Clean knowledge retrieval config, keeping only available KBs."""
        if not knowledge_retrieval:
            return None

        cleaned = copy.deepcopy(knowledge_retrieval)

        if "knowledge_bases" in cleaned and isinstance(cleaned["knowledge_bases"], list):
            cleaned["knowledge_bases"] = [
                kb for kb in cleaned["knowledge_bases"]
                if self._is_kb_available(kb.get("kb_id"), available_kb_ids)
            ]

        return cleaned

    def _clean_memory_cross_workspace(
            self,
            memory: dict | None,
            target_workspace_id: uuid.UUID
    ) -> dict | None:
        """Clear memory_config_id/memory_content if it doesn't belong to target workspace."""
        if not memory:
            return None

        cleaned = copy.deepcopy(memory)
        # Support both the legacy memory_content field and new memory_config_id
        mid = cleaned.get("memory_config_id") or cleaned.get("memory_content")
        if mid:
            try:
                mid_uuid = uuid.UUID(str(mid))
            except (ValueError, AttributeError, TypeError):
                try:
                    legacy_memory_id = int(mid)
                except (ValueError, TypeError):
                    exists = False
                else:
                    exists = cross_domain_read.memory_config_exists_by_legacy_id(
                        self.db, legacy_memory_id, target_workspace_id
                    )
                if not exists:
                    cleaned["memory_config_id"] = None
                    cleaned.pop("memory_content", None)
                return cleaned

            exists = cross_domain_read.memory_config_exists_by_uuid(
                self.db, mid_uuid, target_workspace_id
            )
            if not exists:
                cleaned["memory_config_id"] = None
                cleaned.pop("memory_content", None)

        return cleaned

    def _remap_workflow_nodes_cross_workspace(
            self,
            nodes: list[dict[str, Any]] | None,
            *,
            target_tenant_id: uuid.UUID | None,
            target_workspace_id: uuid.UUID,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Remove unavailable workspace-scoped references from copied workflow nodes.

        Node configuration has no common schema, so this intentionally walks nested
        dicts. Only knowledge bases and memory configuration belong to a workspace;
        tenant-level model, tool and skill references are left untouched.
        """
        copied = copy.deepcopy(nodes or [])
        missing: list[dict[str, Any]] = []
        knowledge_entries: list[dict[str, Any]] = []

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "knowledge_bases" and isinstance(child, list):
                        knowledge_entries.extend(
                            item for item in child if isinstance(item, dict)
                        )
                    else:
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(copied)
        kb_ids = {
            str(entry.get("kb_id"))
            for entry in knowledge_entries
            if entry.get("kb_id") is not None
        }
        _, available_kb_ids = self._preload_cross_workspace_resources(
            target_tenant_id, target_workspace_id, set(), kb_ids
        )

        def node_config_of(node: dict[str, Any]) -> dict[str, Any] | None:
            config = node.get("config")
            if isinstance(config, dict):
                return config
            data = node.get("data")
            if isinstance(data, dict) and isinstance(data.get("config"), dict):
                return data["config"]
            return None

        # Agent 节点的「引用已发布 Agent 应用」只能指向本空间应用（发布时会校验
        # workspace），复制到目标空间后源空间的 app_id 必然失效，所以整段清掉，
        # 节点退回 inline 交给用户重新配置，否则副本连发布都过不了。
        agent_reference_ids: set[uuid.UUID] = set()

        def collect_agent_references(value: Any) -> None:
            if isinstance(value, list):
                for child in value:
                    collect_agent_references(child)
                return
            if not isinstance(value, dict):
                return
            node_config = node_config_of(value)
            reference = (
                node_config.get("reference")
                if isinstance(node_config, dict)
                and node_config.get("mode") == "reference"
                else None
            )
            if isinstance(reference, dict):
                try:
                    agent_reference_ids.add(uuid.UUID(str(reference.get("app_id"))))
                except (TypeError, ValueError, AttributeError):
                    pass
            for child in value.values():
                collect_agent_references(child)

        collect_agent_references(copied)
        available_agent_app_ids: set[uuid.UUID] = set()
        if agent_reference_ids:
            available_agent_app_ids = set(
                self.db.scalars(
                    select(App.id).where(
                        App.id.in_(agent_reference_ids),
                        App.workspace_id == target_workspace_id,
                        App.is_active.is_(True),
                    )
                ).all()
            )

        def clean(value: Any, path: str = "nodes") -> Any:
            if isinstance(value, list):
                return [clean(child, f"{path}[{index}]") for index, child in enumerate(value)]
            if not isinstance(value, dict):
                return value

            result = copy.deepcopy(value)
            if "knowledge_bases" in result and isinstance(result["knowledge_bases"], list):
                retained = []
                for entry in result["knowledge_bases"]:
                    if not isinstance(entry, dict):
                        missing.append({
                            "kind": "knowledge_base",
                            "name": None,
                            "path": path,
                            "reason": "知识库引用格式无效",
                        })
                        continue
                    kb_id = entry.get("kb_id")
                    if self._is_kb_available(kb_id, available_kb_ids):
                        retained.append(clean(entry, f"{path}.knowledge_bases"))
                    else:
                        missing.append({
                            "kind": "knowledge_base",
                            "name": str(kb_id) if kb_id is not None else None,
                            "path": path,
                            "reason": "知识库不属于目标工作空间",
                        })
                result["knowledge_bases"] = retained

            memory_id = result.get("memory_config_id") or result.get("memory_content")
            if memory_id:
                cleaned_memory = self._clean_memory_cross_workspace(
                    result, target_workspace_id
                )
                if (
                    cleaned_memory.get("memory_config_id") is None
                    and "memory_content" not in cleaned_memory
                ):
                    missing.append({
                        "kind": "memory_config",
                        "name": str(memory_id),
                        "path": path,
                        "reason": "记忆配置不属于目标工作空间",
                    })
                result = cleaned_memory

            node_config = node_config_of(result)
            if isinstance(node_config, dict) and node_config.get("mode") == "reference":
                reference = node_config.get("reference")
                try:
                    referenced_app_id = uuid.UUID(
                        str(reference.get("app_id"))
                    ) if isinstance(reference, dict) else None
                except (TypeError, ValueError, AttributeError):
                    referenced_app_id = None
                if referenced_app_id not in available_agent_app_ids:
                    missing.append({
                        "kind": "agent",
                        "name": str(referenced_app_id) if referenced_app_id else None,
                        "path": path,
                        "reason": "引用的 Agent 不属于目标工作空间",
                    })
                    # 引用模式缺了 reference 会校验失败，必须一并退回 inline。
                    node_config.pop("reference", None)
                    node_config["mode"] = "inline"

            for key, child in list(result.items()):
                if key != "knowledge_bases":
                    result[key] = clean(child, f"{path}.{key}")
            return result

        return clean(copied), missing

    def _clone_supervisor_config(
            self,
            supervisor_config: dict | None,
            *,
            is_cross_workspace: bool,
            target_tenant_id: uuid.UUID | None,
            target_workspace_id: uuid.UUID,
    ) -> dict | None:
        """Copy the cluster supervisor_config (the master agent's capability surface).

        supervisor_config holds the whole master-agent configuration: features,
        system_prompt, memory, knowledge_retrieval, skills, variables and tools.
        Omitting the field entirely drops the copy back to a bare supervisor
        (hardcoded prompt + SubAgentTool only).

        Cross-workspace cleaning follows the AgentConfig branch:
        - knowledge_retrieval is workspace-level: keep only KBs available in the
          target workspace;
        - memory is workspace-level: clear memory_config_id when it does not
          belong to the target workspace;
        - model/tools/skills are tenant-level and kept as is.
        """
        if not supervisor_config:
            return None

        cleaned = copy.deepcopy(supervisor_config)
        if not isinstance(cleaned, dict):
            return None

        if is_cross_workspace:
            knowledge_retrieval = cleaned.get("knowledge_retrieval")
            if knowledge_retrieval:
                _, kb_ids = self._collect_resource_ids_from_config(None, knowledge_retrieval)
                _, available_kb_ids = self._preload_cross_workspace_resources(
                    target_tenant_id, target_workspace_id, set(), kb_ids
                )
                cleaned["knowledge_retrieval"] = self._clean_knowledge_retrieval(
                    knowledge_retrieval, available_kb_ids
                )
            if cleaned.get("memory"):
                cleaned["memory"] = self._clean_memory_cross_workspace(
                    cleaned["memory"], target_workspace_id
                )

        return cleaned

    def list_apps(
            self,
            *,
            workspace_id: uuid.UUID,
            type: str | None = None,
            visibility: str | None = None,
            status: str | None = None,
            search: str | None = None,
            tag_search: str | None = None,
            include_shared: bool = True,
            shared_only: bool = False,
            page: int = 1,
            pagesize: int = 10,
            ontology_status: str = "all",
            field_search: str | None = None,
            orchestration_mode: str | None = None,
    ) -> tuple[list[App], int]:
        """List apps in a workspace (paginated).

        Includes:
        1. Apps created in this workspace
        2. Apps shared to this workspace by other workspaces (if include_shared=True)

        Args:
            workspace_id: Workspace ID
            type: App type filter (comma-separated multi-value, e.g. "agent,multi_agent")
            visibility: Visibility filter
            status: Status filter
            search: Search keyword (fuzzy match on app name)
            tag_search: Tag search keyword (fuzzy match on app tags)
            include_shared: Whether to include shared apps
            page: Page number (1-based)
            pagesize: Page size
            ontology_status: Ontology filter: all | bound (ontology linked) |
                default (default ontology only)
            field_search: Tag-mode search; fuzzy-matches display names of custom
                fields of the bound ontology's scene
            orchestration_mode: Orchestration mode filter (clusters only; filters by
                the current release snapshot's config.orchestration_mode)

        Returns:
            Tuple[List[App], int]: (app list, total count)
        """
        logger.debug(
            "查询应用列表",
            extra={
                "workspace_id": str(workspace_id),
                "include_shared": include_shared,
                "page": page,
                "pagesize": pagesize
            }
        )

        # Build query filters
        filters = [App.is_active.is_(True)]
        # type accepts comma-separated multi-value (e.g. "agent,multi_agent"); single
        # value stays backward compatible
        type_list = [t.strip() for t in type.split(",") if t.strip()] if type else []
        if orchestration_mode:
            # Orchestration mode filter (clusters only): matched against the *current
            # release snapshot* — workflow Agent nodes reference the published cluster's
            # runtime artifact, so the predicate must agree with what the runtime reads
            # (a draft whose mode changed but was never published must not be picked up).
            # Cluster branch OR the remaining types:
            #   type=agent,multi_agent&orchestration_mode=supervisor_loop
            #   -> all agents + clusters published with that snapshot mode; with no type
            #   given, only clusters of that mode.
            mode_apps = (
                select(AppRelease.app_id)
                .where(
                    AppRelease.is_active.is_(True),
                    AppRelease.config["orchestration_mode"].astext == orchestration_mode,
                )
                .where(AppRelease.id == App.current_release_id)
            )
            other_types = [t for t in type_list if t != AppType.MULTI_AGENT]
            cluster_branch = and_(App.type == AppType.MULTI_AGENT, App.id.in_(mode_apps))
            if other_types:
                filters.append(or_(App.type.in_(other_types), cluster_branch))
            else:
                filters.append(cluster_branch)
        elif type_list:
            if len(type_list) == 1:
                filters.append(App.type == type_list[0])
            else:
                filters.append(App.type.in_(type_list))
        if visibility:
            filters.append(App.visibility == visibility)
        if status:
            filters.append(App.status == status)
        if search:
            search_pattern = f"%{search.lower()}%"
            filters.append(
                or_(
                    func.lower(App.name).like(search_pattern),
                    # 只读引用在列表里显示的是条目钉住版本的名字（见
                    # `_convert_to_schema` 的 `install_display`），所以按名字搜索
                    # 也必须命中快照名，否则用户照资源中心的名字搜会得到空列表。
                    # 必须带 workspace_id：不加空间隔离会跨所有空间的安装行扫描。
                    App.id.in_(_pinned_name_app_ids(search_pattern, workspace_id)),
                )
            )
        if tag_search:
            tag_pattern = f"%{tag_search.lower()}%"
            filters.append(
                exists(
                    select(1).select_from(
                        func.jsonb_array_elements_text(App.tags.cast(JSONB))
                    ).where(
                        func.lower(column('value')).like(tag_pattern)
                    )
                )
            )
        # Ontology filter: the local binder supplies SQL predicates (premium
        # tables); the remote binder filters candidate ids over the monolith
        # callback channel after the scope query. Without a plugin the
        # ontology parameters have no effect (community behavior).
        field_search = field_search.strip() if field_search else None
        ontology_filter = (ontology_status and ontology_status != "all") or field_search
        ontology_binder = None
        if ontology_filter:
            ontology_binder = get_plugin(ONTOLOGY_BINDING_PLUGIN)
            if ontology_binder is None:
                logger.warning(
                    "未加载本体模块，忽略本体筛选参数",
                    extra={"ontology_status": ontology_status, "field_search": field_search}
                )
                ontology_filter = None
            elif getattr(ontology_binder, "uses_local_db", True):
                filters.extend(ontology_binder.build_list_filters(ontology_status, field_search))
                ontology_filter = None
        # shared_only implies include_shared; enforce to avoid confusing API usage
        if shared_only:
            include_shared = True

        # Base scope: apps of this workspace (plus apps shared to it)
        if shared_only:
            # Return only apps shared to this workspace, excluding own apps.
            # 「共享来的」= 旧 app_shares 行 ∪ 资源中心只读引用安装（见
            # `_reference_install_app_ids`）：只补 legacy 表会漏掉所有从资源中心
            # 拉取的应用，它们的 app 属于源空间、又不在 app_shares 里。
            shared_app_ids_stmt = (
                select(AppShare.source_app_id)
                .where(AppShare.target_workspace_id == workspace_id, AppShare.is_active.is_(True))
                .union(_reference_install_app_ids(workspace_id))
            )
            scope = and_(
                App.id.in_(shared_app_ids_stmt),
                # 「共享给我」不含自有应用：上面的 union 里可能混入指向本空间应用
                # 的安装行（租户把自己的应用拉到自己的空间），必须显式排除。
                App.workspace_id != workspace_id,
            )
        elif include_shared:
            # Query apps of this workspace plus those shared to it
            shared_app_ids_stmt = (
                select(AppShare.source_app_id)
                .where(AppShare.target_workspace_id == workspace_id, AppShare.is_active.is_(True))
                .union(_reference_install_app_ids(workspace_id))
            )
            scope = or_(
                App.workspace_id == workspace_id,
                App.id.in_(shared_app_ids_stmt)
            )
        else:
            # Query only apps of this workspace
            scope = App.workspace_id == workspace_id

        if filters:
            scope = and_(scope, *filters)

        if ontology_filter:
            # Candidate ids under the non-ontology scope; the monolith returns
            # the subset matching the ontology filter. End the transaction
            # before the HTTP call (no remote calls inside a transaction).
            candidates = list(
                self.db.scalars(select(App.id).where(scope)).all()
            )
            self.db.rollback()
            try:
                matched = ontology_binder.filter_app_ids(
                    candidates, ontology_status, field_search
                )
            except Exception as e:
                logger.error("本体筛选回调失败", extra={"error": str(e)}, exc_info=True)
                raise BusinessException(
                    "本体筛选服务不可用", BizCode.SERVICE_UNAVAILABLE, cause=e
                ) from e
            if matched is None:
                logger.warning(
                    "本体模块未启用，忽略本体筛选参数",
                    extra={"ontology_status": ontology_status, "field_search": field_search}
                )
            else:
                scope = and_(scope, App.id.in_(matched))

        stmt = select(App).where(scope)

        # Count total
        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = self.db.execute(total_stmt).scalar() or 0

        # Pagination
        offset = (page - 1) * pagesize
        stmt = stmt.order_by(App.created_at.desc()).offset(offset).limit(pagesize)

        items = list(self.db.scalars(stmt).all())

        logger.debug(
            "应用列表查询完成",
            extra={"total": total, "returned": len(items), "include_shared": include_shared}
        )
        return items, int(total)

    def get_apps_by_ids(
            self,
            app_ids: list[str],
            workspace_id: uuid.UUID
    ) -> list[App]:
        """Get apps by ID list.

        Args:
            app_ids: List of app IDs
            workspace_id: Workspace ID (for permission checks)

        Returns:
            List[App]: List of apps
        """
        if not app_ids:
            return []

        # Convert string IDs to UUIDs
        try:
            uuid_ids = [uuid.UUID(app_id) for app_id in app_ids]
        except ValueError:
            return []

        # Query own-workspace apps plus apps shared to it (same scope as list_apps
        # with include_shared; the union keeps ids from the catalog in sync too)
        shared_app_ids_stmt = (
            select(AppShare.source_app_id)
            .where(AppShare.target_workspace_id == workspace_id, AppShare.is_active.is_(True))
            .union(_reference_install_app_ids(workspace_id))
        )
        stmt = select(App).where(
            App.id.in_(uuid_ids),
            or_(
                App.workspace_id == workspace_id,
                App.id.in_(shared_app_ids_stmt)
            )
        )

        return list(self.db.scalars(stmt).all())

    # ==================== Agent config management ====================

    def update_agent_config(
            self,
            *,
            app_id: uuid.UUID,
            data: app_schema.AgentConfigUpdate,
            workspace_id: uuid.UUID | None = None
    ) -> AgentConfig:
        """Update the Agent config.

        Args:
            app_id: App ID
            data: Config update payload
            workspace_id: Workspace ID (for permission checks)

        Returns:
            AgentConfig: The updated config object

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app type is unsupported or the app is not
                in the given workspace
        """
        logger.info("更新 Agent 配置", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)

        # Access check precedes the type branch: an invisible app must not reveal
        # its type through a different error.
        self._validate_app_writable(app, workspace_id)

        if app.type != "agent":
            raise BusinessException(
                "只有 Agent 类型应用支持 Agent 配置",
                BizCode.APP_TYPE_NOT_SUPPORTED,
            )

        # Validate the model reference before the row is touched; a bare FK
        # violation would surface as a 500 carrying the SQL statement.
        if data.default_model_config_id is not None:
            tenant_id = cross_domain_read.get_workspace_tenant_id(
                self.db, app.workspace_id
            )
            if resolve_invoke_ref(
                self.db, data.default_model_config_id, tenant_id=tenant_id
            ) is None:
                raise_model_unavailable(
                    self.db, data.default_model_config_id, tenant_id=tenant_id
                )

        stmt = (
            select(AgentConfig)
            .where(AgentConfig.app_id == app_id, AgentConfig.is_active.is_(True))
            .order_by(AgentConfig.updated_at.desc())
        )
        agent_cfg: AgentConfig | None = self.db.scalars(stmt).first()
        now = utcnow_naive()

        if not agent_cfg:
            agent_cfg = AgentConfig(
                id=uuid.uuid4(),
                app_id=app_id,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            self.db.add(agent_cfg)
            logger.debug("创建新的 Agent 配置", extra={"app_id": str(app_id)})

        # Convert to storage format
        storage_data = AgentConfigConverter.to_storage_format(data)

        tools = storage_data.get("tools") or []
        if tools:
            tenant_id = cross_domain_read.get_workspace_tenant_id(
                self.db, app.workspace_id
            )
            if tenant_id is None:
                raise BusinessException("应用所属工作空间不存在", BizCode.NOT_FOUND)
            storage_data["tools"] = ToolService(self.db).validate_tool_selection(
                tenant_id, tools, agent_cfg.tools
            )

        # Update fields (unconditional full overwrite: absent fields are cleared too)
        agent_cfg.system_prompt = data.system_prompt
        agent_cfg.default_model_config_id = data.default_model_config_id
        agent_cfg.model_parameters = storage_data.get("model_parameters")
        agent_cfg.knowledge_retrieval = storage_data.get("knowledge_retrieval")
        agent_cfg.memory = storage_data.get("memory")
        agent_cfg.variables = storage_data.get("variables", [])
        agent_cfg.tools = storage_data.get("tools", [])
        agent_cfg.skills = storage_data.get("skills", {})
        agent_cfg.features = storage_data.get("features", {})

        agent_cfg.updated_at = now

        self.db.commit()
        self.db.refresh(agent_cfg)

        logger.info("Agent 配置更新成功", extra={"app_id": str(app_id)})
        return agent_cfg

    def _agent_config_from_release(self, release: AppRelease) -> AgentConfig:
        """Rebuild an AgentConfig object from a release snapshot (not persisted)."""
        cfg = release.config or {}
        now = release.created_at or utcnow_naive()
        # Look up the source app's real AgentConfig id for agent_executions FKs
        real_config = self.db.scalars(
            select(AgentConfig).where(AgentConfig.app_id == release.app_id)
        ).first()
        real_id = real_config.id if real_config else uuid.uuid4()
        agent_cfg = AgentConfig(
            id=real_id,
            app_id=release.app_id,
            system_prompt=cfg.get("system_prompt", ""),
            default_model_config_id=release.default_model_config_id,
            model_parameters=cfg.get("model_parameters"),
            knowledge_retrieval=cfg.get("knowledge_retrieval"),
            memory=cfg.get("memory", {}),
            variables=cfg.get("variables", []),
            tools=cfg.get("tools", []),
            skills=cfg.get("skills", {}),
            features=cfg.get("features", {}),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        return agent_cfg

    def get_agent_config(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None
    ) -> AgentConfig:
        """Get the Agent config.

        Returns a default config template (not saved to the DB) if none exists.

        Args:
            app_id: App ID
            workspace_id: Workspace ID (for permission checks)

        Returns:
            AgentConfig: Existing config or the default template

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app type is unsupported or inaccessible
        """
        logger.debug("获取 Agent 配置", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)

        # Access check precedes the type branch: an invisible app must not reveal
        # its type through a different error.
        self._validate_app_accessible(app, workspace_id)

        if app.type != "agent":
            raise BusinessException(
                "只有 Agent 类型应用支持 Agent 配置",
                BizCode.APP_TYPE_NOT_SUPPORTED,
            )

        # Shared app: return the pinned release snapshot instead of the draft.
        # The pin comes from the catalog entry this workspace installed, not the
        # source's current release (§5.3).
        if workspace_id and app.workspace_id != workspace_id:
            release = AssetReleaseResolver(self.db).resolve_release(
                app_id=app.id, workspace_id=workspace_id
            )
            if not release:
                raise BusinessException("该应用尚未发布，无法使用", BizCode.APP_NOT_PUBLISHED)
            return self._agent_config_from_release(release)

        stmt = select(AgentConfig).where(
            AgentConfig.app_id == app_id,
            AgentConfig.is_active.is_(True)
        ).order_by(
            AgentConfig.updated_at.desc()
        )

        config = self.db.scalars(stmt).first()

        try:
            config_memory = config.memory
            if 'memory_content' in config_memory:
                config.memory['memory_config_id'] = config.memory.pop('memory_content')
        except Exception:
            logger.debug("记忆配置不存在")
        if config:
            return config

        # Return a default config template (not saved to the DB)
        logger.debug("配置不存在，返回默认模板", extra={"app_id": str(app_id)})
        return self._create_default_agent_config(app_id)

    def get_default_model_parameters(
            self,
            *,
            app_id: uuid.UUID,
    ) -> app_schema.ModelParameters:
        """Get the Agent's default model parameters (no DB writes).

        Args:
            app_id: App ID

        Returns:
            ModelParameters: Default model parameters
        """
        logger.info("获取 Agent 默认模型参数", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)

        if app.type != "agent":
            raise BusinessException(
                "只有 Agent 类型应用支持 Agent 配置",
                BizCode.APP_TYPE_NOT_SUPPORTED,
            )

        default_model_parameters = app_schema.ModelParameters()

        logger.info("获取 Agent 默认模型参数成功", extra={"app_id": str(app_id)})
        return default_model_parameters

    def _create_default_agent_config(self, app_id: uuid.UUID) -> AgentConfig:
        """Create a default Agent config template (not saved to the DB).

        Args:
            app_id: App ID

        Returns:
            AgentConfig: Default config object
        """
        now = utcnow_naive()

        # Build a transient config object; do not add it to the DB
        default_config = AgentConfig(
            id=uuid.uuid4(),  # Temporary ID
            app_id=app_id,
            system_prompt="你是一个专业的AI助手，你的职责是帮助用户解决问题。",
            default_model_config_id=None,
            model_parameters={
                "temperature": 0.7,
                "max_tokens": 2000,
                "top_p": 1.0,
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
                "n": 1,
                "stop": None
            },
            knowledge_retrieval={
                "knowledge_bases": [],
                "merge_strategy": "weighted"
            },
            memory={
                "enabled": True,
                "memory_config_id": None,
            },
            variables=[],
            tools=[],
            skills=[],
            features={},
            is_active=True,
            created_at=now,
            updated_at=now,
        )

        return default_config

    # ==================== Workflow config reads ====================

    def _normalize_workflow_type(
            self,
            workflow_type: Any,
            default: str = AppType.WORKFLOW.value
    ) -> str:
        """Normalize workflow_type to a string; accepts None/enum/plain string."""
        if workflow_type is None:
            return default
        if hasattr(workflow_type, "value"):
            return str(workflow_type.value)
        return str(workflow_type)

    def get_workflow_config(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None
    ) -> WorkflowConfig:
        """Get the workflow config.

        Returns a default config template (not saved to the DB) if none exists.

        Args:
            app_id: App ID
            workspace_id: Workspace ID (for permission checks)

        Returns:
            WorkflowConfig: Existing config or the default template

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app type is unsupported or inaccessible
        """
        logger.debug("获取 Workflow 配置", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)

        if app.type not in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            raise BusinessException(
                "只有 Workflow 或 Pure_Workflow 类型应用支持 Workflow 配置",
                BizCode.APP_TYPE_NOT_SUPPORTED,
            )

        # Read-only operation; shared apps are allowed
        self._validate_app_accessible(app, workspace_id)

        # Shared app: return the pinned release snapshot instead of the draft.
        # Same rule as the Agent config read: follow this workspace's
        # installation (§5.3), not the source's current release.
        if workspace_id and app.workspace_id != workspace_id:
            release = AssetReleaseResolver(self.db).resolve_release(
                app_id=app.id, workspace_id=workspace_id
            )
            if not release:
                raise BusinessException("该应用尚未发布，无法使用", BizCode.APP_NOT_PUBLISHED)
            return self._workflow_config_from_release(release)

        repo = WorkflowConfigRepository(self.db)
        config = repo.get_by_app_id(app_id)
        if config:
            return config

        # Return a default config template (not saved to the DB)
        logger.debug("配置不存在，返回默认模板", extra={"app_id": str(app_id)})
        return self._create_default_workflow_config(app_id, app.type)

    def update_workflow_config(
            self,
            *,
            app_id: uuid.UUID,
            data: workflow_schema.WorkflowConfigUpdate,
            workspace_id: uuid.UUID | None = None,
            redis_client: Any,
    ) -> WorkflowConfig:
        """Update the Workflow config (full replacement).

        Args:
            app_id: App ID
            data: Full config update payload
            workspace_id: Workspace ID (for permission checks)
            redis_client: Shared-cache client for the cross-process invalidation.

        Returns:
            WorkflowConfig: The updated config object

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app type is unsupported or the app is not
                in the given workspace
        """
        logger.info("更新 Workflow 配置", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)

        if app.type not in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            raise BusinessException(
                "只有 Workflow 或 Pure_Workflow 类型应用支持 Workflow 配置",
                BizCode.APP_TYPE_NOT_SUPPORTED,
            )

        self._validate_app_writable(app, workspace_id)

        # Load the existing config
        repo = WorkflowConfigRepository(self.db)
        workflow_cfg = repo.get_by_app_id(app_id)
        now = utcnow_naive()
        workflow_service = WorkflowService(self.db)
        features = data.features or {}
        workflow_type = self._normalize_workflow_type(
            data.workflow_type,
            self._normalize_workflow_type(
                workflow_cfg.workflow_type if workflow_cfg else app.type,
                self._normalize_workflow_type(app.type),
            ),
        )
        config_dict = workflow_service._prepare_workflow_config_dict(
            nodes=[node.model_dump() for node in data.nodes] if data.nodes else [],
            edges=[edge.model_dump() for edge in data.edges] if data.edges else [],
            variables=[var.model_dump() for var in data.variables] if data.variables else [],
            environment_variables=[
                var.model_dump() for var in data.environment_variables
            ] if data.environment_variables else [],
            execution_config=data.execution_config.model_dump() if data.execution_config else {},
            features=features,
            triggers=[trigger.model_dump() for trigger in data.triggers] if data.triggers else [],
            workflow_type=workflow_type,
        )
        is_valid, errors = WorkflowValidator.validate(config_dict)
        if not is_valid:
            raise BusinessException(
                f"工作流配置无效: {'; '.join(errors)}",
                BizCode.INVALID_PARAMETER,
            )

        if not workflow_cfg:
            # Create a new config if none exists
            workflow_cfg = WorkflowConfig(
                id=uuid.uuid4(),
                app_id=app_id,
                nodes=config_dict["nodes"],
                edges=config_dict["edges"],
                variables=config_dict["variables"],
                environment_variables=config_dict["environment_variables"],
                execution_config=config_dict["execution_config"],
                triggers=config_dict["triggers"],
                features=config_dict["features"],
                workflow_type=config_dict["workflow_type"],
                is_active=True,
                created_at=now,
                updated_at=now
            )
            self.db.add(workflow_cfg)
            logger.debug("创建新的 Workflow 配置", extra={"app_id": str(app_id)})
        else:
            # Full update of the existing config
            workflow_cfg.nodes = config_dict["nodes"]
            workflow_cfg.edges = config_dict["edges"]
            workflow_cfg.variables = config_dict["variables"]
            workflow_cfg.environment_variables = config_dict["environment_variables"]
            workflow_cfg.execution_config = config_dict["execution_config"]
            workflow_cfg.triggers = config_dict["triggers"]
            workflow_cfg.features = config_dict["features"]
            workflow_cfg.workflow_type = config_dict["workflow_type"]
            workflow_cfg.updated_at = now

        self.db.commit()
        self.db.refresh(workflow_cfg)
        delete_workflow_config(redis_client, app_id)

        logger.info("Workflow 配置更新成功", extra={"app_id": str(app_id)})
        return workflow_cfg

    def _workflow_config_from_release(self, release: AppRelease) -> WorkflowConfig:
        """Rebuild a WorkflowConfig object from a release snapshot (not persisted)."""
        cfg = release.config or {}
        now = release.created_at or utcnow_naive()
        # Look up the source app's real WorkflowConfig id for workflow_executions FKs
        real_config = WorkflowConfigRepository(self.db).get_by_app_id(release.app_id)
        real_id = real_config.id if real_config else uuid.uuid4()
        wf_cfg = WorkflowConfig(
            id=real_id,
            app_id=release.app_id,
            nodes=cfg.get("nodes", []),
            edges=cfg.get("edges", []),
            variables=cfg.get("variables", []),
            environment_variables=cfg.get("environment_variables") or [],
            execution_config=cfg.get("execution_config", {}),
            triggers=cfg.get("triggers", []),
            features=cfg.get("features", {}),
            workflow_type=self._normalize_workflow_type(cfg.get("workflow_type")),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        return wf_cfg

    def _create_default_workflow_config(
            self,
            app_id: uuid.UUID,
            workflow_type: Any = AppType.WORKFLOW.value
    ) -> WorkflowConfig:
        """Create a default workflow config template (not saved to the DB).

        Loads the simple_qa template via template_loader as the default config.

        Args:
            app_id: App ID

        Returns:
            WorkflowConfig: Default config object
        """
        now = utcnow_naive()
        normalized_workflow_type = self._normalize_workflow_type(workflow_type)

        template_id = "pure_workflow_simple_qa" \
            if normalized_workflow_type == AppType.PURE_WORKFLOW.value \
            else "simple_qa"
        template_data = load_workflow_template(template_id)

        if not template_data:
            # Fall back to a minimal config if the template fails to load
            logger.warning(
                "无法加载默认工作流模板，使用最小化配置",
                extra={"app_id": str(app_id)}
            )
            if normalized_workflow_type == AppType.PURE_WORKFLOW.value:
                template_data = {
                    "nodes": [
                        {"id": "start", "type": "start", "name": "开始"},
                        {
                            "id": "output_node",
                            "type": "output",
                            "name": "输出",
                            "config": {"outputs": []},
                        },
                    ],
                    "edges": [
                        {"source": "start", "target": "output_node"}
                    ],
                    "variables": [],
                    "environment_variables": [],
                    "execution_config": {
                        "max_execution_time": 300,
                        "max_iterations": 10,
                    },
                    "triggers": [],
                }
            else:
                template_data = {
                    'nodes': [
                        {'id': 'start', 'type': 'start', 'name': '开始'},
                        {'id': 'end', 'type': 'end', 'name': '回复'}
                    ],
                    'edges': [
                        {'source': 'start', 'target': 'end'}
                    ],
                    'variables': [],
                    'environment_variables': [],
                    'execution_config': {
                        'max_execution_time': 300,
                        'max_iterations': 10
                    },
                    'triggers': []
                }

        # Convert to WorkflowConfig shape
        default_config = WorkflowConfig(
            id=uuid.uuid4(),
            app_id=app_id,
            nodes=template_data.get('nodes', []),
            edges=template_data.get('edges', []),
            variables=template_data.get('variables', []),
            environment_variables=template_data.get('environment_variables', []),
            execution_config=template_data.get('execution_config', {}),
            triggers=template_data.get('triggers', []),
            workflow_type=normalized_workflow_type,
            is_active=True,
            created_at=now,
            updated_at=now
        )

        return default_config

    # ==================== Publish / rollback ====================

    def release_schema(self, release: AppRelease) -> app_schema.AppRelease:
        """Serialize one release (publisher name batch-loaded)."""

        return self.release_schemas([release])[0]

    def release_schemas(self, releases: list[AppRelease]) -> list[app_schema.AppRelease]:
        """Serialize releases with the host publisher-name fallback chain.

        The host reads ``release.publisher.username or email or "未知用户"``
        through the lazy relationship; the service-side model omits that
        relationship, so the publishers are batch-loaded up front and the same
        fallback applies for missing rows (deleted users).
        """

        names = cross_domain_read.get_user_display_names_by_ids(
            self.db, {r.published_by for r in releases if r.published_by}
        )
        return [
            self._release_to_schema(r, names.get(r.published_by) or "未知用户")
            for r in releases
        ]

    def _release_to_schema(
            self, release: AppRelease, publisher_name: str
    ) -> app_schema.AppRelease:
        return app_schema.AppRelease(
            id=release.id,
            app_id=release.app_id,
            version=release.version,
            release_notes=release.release_notes,
            version_name=release.version_name,
            description=release.description,
            icon=release.icon,
            icon_type=release.icon_type,
            name=release.name,
            type=release.type,
            visibility=release.visibility,
            config=release.config,
            usage_examples=_expand_usage_examples(release.usage_examples),
            setup_steps=release.setup_steps,
            categories=list(release.categories or []),
            default_model_config_id=release.default_model_config_id,
            published_by=release.published_by,
            publisher_name=publisher_name,
            published_at=release.published_at,
            is_active=release.is_active,
            created_at=release.created_at,
            updated_at=release.updated_at,
        )

    def publish(
            self,
            *,
            app_id: uuid.UUID,
            publisher_id: uuid.UUID,
            version_name: str,
            workspace_id: uuid.UUID | None = None,
            release_notes: str | None = None,
            name: str | None = None,
            icon: str | None = None,
            usage_examples: list[str] | None = None,
            setup_steps: str | None = None,
            categories: list[str] | None = None,
    ) -> AppRelease:
        """Publish an app (creates an immutable snapshot).

        Args:
            app_id: App ID
            publisher_id: Publisher user ID
            workspace_id: Workspace ID (for permission checks)
            release_notes: Release notes
            name: Title
            icon: Icon

        Returns:
            AppRelease: The release object

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app lacks config or is not in the given
                workspace
        """
        logger.info("发布应用", extra={"app_id": str(app_id), "publisher_id": str(publisher_id)})

        app = self._get_app_or_404(app_id)
        # Check app ownership
        self._validate_workspace_access(app, workspace_id)

        # 图标与前端保持一致，都是必填：服务层再挡一次，避免内部调用绕过 schema。
        icon = (icon or "").strip()
        if not icon:
            raise BusinessException("发布必须选择自定义图标", BizCode.INVALID_PARAMETER)

        # 按应用类型校验并归一化发布元数据，避免开放 API 绕过前端规则。
        workflow_types = (AppType.WORKFLOW, AppType.PURE_WORKFLOW)
        if app.type in workflow_types:
            setup_steps = (setup_steps or "").strip()
            if not setup_steps:
                raise BusinessException("工作流发布必须填写设置步骤", BizCode.INVALID_PARAMETER)
            if len(setup_steps) > app_schema.SETUP_STEPS_MAX_CHARS:
                raise BusinessException(
                    f"设置步骤最多 {app_schema.SETUP_STEPS_MAX_CHARS} 字",
                    BizCode.INVALID_PARAMETER,
                )
            usage_examples = []
        else:
            usage_examples = [
                item.strip()
                for item in (usage_examples or [])
                if isinstance(item, str) and item.strip()
            ]
            if not (
                app_schema.USAGE_EXAMPLES_MIN_ITEMS
                <= len(usage_examples)
                <= app_schema.USAGE_EXAMPLES_MAX_ITEMS
            ):
                raise BusinessException(
                    "Agent 或集群发布必须填写 "
                    f"{app_schema.USAGE_EXAMPLES_MIN_ITEMS}～"
                    f"{app_schema.USAGE_EXAMPLES_MAX_ITEMS} 条使用示例",
                    BizCode.INVALID_PARAMETER,
                )
            if any(
                len(item) > app_schema.USAGE_EXAMPLE_MAX_CHARS
                for item in usage_examples
            ):
                raise BusinessException(
                    f"每条使用示例最多 {app_schema.USAGE_EXAMPLE_MAX_CHARS} 字",
                    BizCode.INVALID_PARAMETER,
                )
            setup_steps = None

        # Build the snapshot config
        config: dict[str, Any] = {}
        default_model_config_id = None

        if app.type == AppType.AGENT:
            stmt = (
                select(AgentConfig)
                .where(AgentConfig.app_id == app_id, AgentConfig.is_active.is_(True))
                .order_by(AgentConfig.updated_at.desc())
            )
            agent_cfg = self.db.scalars(stmt).first()
            if not agent_cfg:
                raise BusinessException(
                    "Agent 应用缺少配置，无法发布", BizCode.AGENT_CONFIG_MISSING
                )

            miss_params = []
            if agent_cfg.default_model_config_id is None:
                miss_params.append("模型配置")

            if miss_params:
                raise BusinessException(
                    f"应用发布失败：检测到以下必要配置尚未完成：{', '.join(miss_params)}。"
                    f"请返回应用编辑页面完成相关配置后再尝试发布。",
                    BizCode.CONFIG_MISSING,
                    context={"missing_params": miss_params},
                )

            config = {
                "system_prompt": agent_cfg.system_prompt,
                "model_parameters": model_parameters_to_dict(agent_cfg.model_parameters),
                "knowledge_retrieval": agent_cfg.knowledge_retrieval,
                "memory": agent_cfg.memory,
                "variables": agent_cfg.variables or [],
                "tools": agent_cfg.tools or [],
                "skills": agent_cfg.skills or {},
                "features": agent_cfg.features or {}
            }
            default_model_config_id = agent_cfg.default_model_config_id
        elif app.type == AppType.MULTI_AGENT:
            # 1. Load the multi-agent config
            multi_agent_cfg = MultiAgentConfigRepository(self.db).get_by_app_id(app_id)
            if not multi_agent_cfg:
                raise BusinessException(
                    "多 Agent 应用缺少有效配置，无法发布", BizCode.AGENT_CONFIG_MISSING
                )

            # 2. Check config completeness
            self._check_multi_agent_config(app_id)

            # 3. Get the master agent's model config ID
            default_model_config_id = multi_agent_cfg.default_model_config_id

            # 4. Build the config snapshot
            # Sub-agent versions are pinned at publish time: "current" resolves to
            # the release at this moment; snapshots always store pinned, so published
            # clusters do not change silently when a sub-agent is republished.
            pinned_sub_agents = self._pin_multi_agent_sub_agents(multi_agent_cfg.sub_agents)

            config = {
                "model_parameters": model_parameters_to_dict(multi_agent_cfg.model_parameters),
                "master_agent_id": str(multi_agent_cfg.master_agent_id),
                "orchestration_mode": multi_agent_cfg.orchestration_mode,
                "sub_agents": pinned_sub_agents,
                "routing_rules": multi_agent_cfg.routing_rules,
                "execution_config": multi_agent_cfg.execution_config,
                # The supervisor config (prompt/tools/memory/KBs/cluster vars) must be
                # published with the snapshot, or readers of published clusters
                # (multi_agent_config_4_app_release etc.) would always get None
                "supervisor_config": multi_agent_cfg.supervisor_config,
                "aggregation_strategy": multi_agent_cfg.aggregation_strategy,
            }

            logger.info(
                "多智能体应用发布配置准备完成",
                extra={
                    "app_id": str(app_id),
                    "default_model_config_id": str(default_model_config_id),
                    "sub_agent_count": (
                        len(multi_agent_cfg.sub_agents) if multi_agent_cfg.sub_agents else 0
                    ),
                    "orchestration_mode": multi_agent_cfg.orchestration_mode
                }
            )
        elif app.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            # Host path is WorkflowService.get_workflow_config ->
            # config_repo.get_by_app_id (no default-template fallback)
            workflow_cfg = WorkflowConfigRepository(self.db).get_by_app_id(app_id)
            if not workflow_cfg:
                raise BusinessException("应用缺少有效配置，无法发布", BizCode.CONFIG_MISSING)

            config = {
                "id": str(workflow_cfg.id),
                "nodes": self._pin_workflow_agent_references(
                    workflow_cfg.nodes,
                    app.workspace_id,
                ),
                "edges": workflow_cfg.edges,
                "variables": workflow_cfg.variables,
                "environment_variables": workflow_cfg.environment_variables or [],
                "execution_config": workflow_cfg.execution_config,
                "triggers": workflow_cfg.triggers,
                "workflow_type": workflow_cfg.workflow_type,
                "features": workflow_cfg.features or {}
            }

            is_valid, errors = WorkflowValidator.validate_for_publish(config)
            if not is_valid:
                raise BusinessException(
                    f"应用缺少有效配置，无法发布, errors:{','.join(errors)}",
                    BizCode.CONFIG_MISSING,
                )
            logger.info(
                "应用发布配置准备完成"
            )

        # Draft publish gate: reject publishing when a referenced model is
        # disabled or sunset
        self._assert_publishable_models(config, default_model_config_id)

        # 功能分类必须存在且启用：枚举已删除，只能查 `app_categories`（§4.5）。
        # 放在这里而不是 pydantic validator 里，因为 validator 拿不到 session。
        CategoryService(self.db).assert_codes_exist(categories or [])

        now = utcnow_naive()
        version = self._get_next_version(app_id)

        release = AppRelease(
            id=uuid.uuid4(),
            app_id=app_id,
            version=version,
            version_name=version_name,
            release_notes=release_notes,
            name=name or app.name,
            description=app.description,
            icon=icon or app.icon,
            icon_type=app.icon_type,
            type=app.type,
            visibility=app.visibility,
            config=config,
            setup_steps=setup_steps,
            usage_examples=_store_usage_examples(usage_examples),
            categories=list(categories or []),
            default_model_config_id=default_model_config_id,
            published_by=publisher_id,
            published_at=now,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(release)
        self.db.flush()  # Flush first so the release row exists in the DB

        # Point the app at the new current release
        app.current_release_id = release.id
        app.status = AppStatus.ACTIVE
        app.updated_at = now

        self.db.commit()
        self.db.refresh(release)

        logger.info(
            "应用发布成功",
            extra={"app_id": str(app_id), "version": version, "release_id": str(release.id)}
        )
        return release

    def _check_multi_agent_config(self, app_id: uuid.UUID):
        """Check the completeness of a multi-agent config.

        Verifies:
        1. The multi-agent config exists
        2. The master agent config exists
        3. Sub-agent configs exist
        4. Model configs exist for all agents

        Args:
            app_id: App ID

        Raises:
            BusinessException: When config is incomplete or missing
        """

        # 1. Check the multi-agent config exists
        multi_agent_config = MultiAgentConfigRepository(self.db).get_by_app_id(app_id)

        if not multi_agent_config:
            raise BusinessException(
                "多智能体配置不存在，无法运行",
                BizCode.AGENT_CONFIG_MISSING
            )

        if not multi_agent_config.is_active:
            raise BusinessException(
                "多智能体配置未激活，无法运行",
                BizCode.AGENT_CONFIG_MISSING
            )
        # Both "supervisor-style" modes require a usable master model or master agent:
        # supervisor routes and aggregates; supervisor_loop drives the supervisor
        # ReAct engine
        if multi_agent_config.orchestration_mode in ("supervisor", "supervisor_loop"):
            if not multi_agent_config.default_model_config_id:
                # 2. Check the master agent config
                if not multi_agent_config.master_agent_id:
                    raise BusinessException(
                        "未配置主 Agent，无法运行",
                        BizCode.AGENT_CONFIG_MISSING
                    )

                master_agent_release = self.db.get(AppRelease, multi_agent_config.master_agent_id)
                if not master_agent_release:
                    raise BusinessException(
                        f"主 Agent 配置不存在: {multi_agent_config.master_agent_id}",
                        BizCode.AGENT_CONFIG_MISSING
                    )

                # Check the master agent's model config
                multi_agent_config.default_model_config_id = (
                    master_agent_release.default_model_config_id
                )

            # This method only receives app_id; load the App here to read its
            # workspace_id (served from the session identity map, no extra query)
            app = self.db.get(App, app_id)
            if not app:
                raise BusinessException("应用不存在", BizCode.NOT_FOUND)

            db_app = self.db.get(App, app_id)
            if not db_app:
                raise BusinessException("应用不存在", BizCode.NOT_FOUND)

            tenant_id = cross_domain_read.get_workspace_tenant_id(self.db, db_app.workspace_id)
            invoke_ref = resolve_invoke_ref(
                self.db,
                multi_agent_config.default_model_config_id,
                tenant_id=tenant_id,
            )
            if invoke_ref is None:
                raise_model_unavailable(
                    self.db,
                    multi_agent_config.default_model_config_id,
                    tenant_id=tenant_id,
                )

        # 3. Check sub-agent configs
        # supervisor_loop accepts zero sub-agents (the supervisor degrades to a bare
        # agent answering directly); every other mode requires sub-agents to run.
        if (
            multi_agent_config.orchestration_mode != "supervisor_loop"
            and (not multi_agent_config.sub_agents or len(multi_agent_config.sub_agents) == 0)
        ):
            raise BusinessException(
                "未配置子 Agent，无法运行",
                BizCode.AGENT_CONFIG_MISSING
            )

        # 4. Validate each sub-agent and its model config
        for idx, sub_agent_data in enumerate(multi_agent_config.sub_agents):
            agent_id = sub_agent_data.get('agent_id')
            if not agent_id:
                raise BusinessException(
                    f"子 Agent #{idx + 1} 缺少 agent_id",
                    BizCode.AGENT_CONFIG_MISSING
                )

            # Resolve the effective release per the version policy
            # (new agent_id form = app ID, legacy = release ID; the resolver decides)
            effective_release_id = resolve_effective_release_id(
                self.db, sub_agent_data, strict=False
            )

            # Check the sub-agent exists
            sub_agent_release = self.db.get(AppRelease, effective_release_id)
            if not sub_agent_release:
                raise BusinessException(
                    f"子 Agent 配置不存在: {agent_id} ({sub_agent_data.get('name', '未命名')})",
                    BizCode.AGENT_CONFIG_MISSING
                )

            # Check the sub-agent's model config
            if sub_agent_release.default_model_config_id:
                sub_model = cross_domain_read.get_model_config_row(
                    self.db, sub_agent_release.default_model_config_id
                )
                if not sub_model:
                    raise BusinessException(
                        f"子 Agent '{sub_agent_data.get('name', '未命名')}' 的模型配置不存在: "
                        f"{sub_agent_release.default_model_config_id}",
                        BizCode.MODEL_NOT_FOUND
                    )
            else:
                raise BusinessException(
                    f"子 Agent '{sub_agent_data.get('name', '未命名')}' 未配置模型，无法运行",
                    BizCode.MODEL_NOT_CONFIGURED
                )

        logger.info(
            "多智能体配置检查通过"
        )

    def _pin_workflow_agent_references(
            self,
            nodes: list[dict[str, Any]],
            workspace_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """Validate agent references and pin them in the workflow release snapshot."""
        pinned_nodes = copy.deepcopy(nodes)
        for node in pinned_nodes:
            if node.get("type") != "agent":
                continue
            node_config = node.get("config") or {}
            if node_config.get("mode", "inline") != "reference":
                continue

            reference = node_config.get("reference") or {}
            try:
                referenced_app_id = uuid.UUID(str(reference.get("app_id")))
            except (TypeError, ValueError, AttributeError) as exc:
                raise BusinessException(
                    f"Agent 节点 {node.get('name') or node.get('id')} 缺少有效的引用应用",
                    BizCode.CONFIG_MISSING,
                    cause=exc,
                ) from exc

            referenced_app = self.db.get(App, referenced_app_id)
            if not referenced_app or not referenced_app.is_active:
                raise BusinessException("引用的 Agent 应用不存在或已停用", BizCode.APP_NOT_FOUND)
            if referenced_app.workspace_id != workspace_id:
                raise BusinessException(
                    "第一阶段仅支持引用当前工作空间的 Agent 应用",
                    BizCode.WORKSPACE_NO_ACCESS,
                )
            if referenced_app.type != AppType.AGENT:
                raise BusinessException("引用的应用不是 Agent 类型", BizCode.APP_TYPE_NOT_SUPPORTED)
            if referenced_app.status != AppStatus.ACTIVE:
                raise BusinessException(
                    "引用的 Agent 应用尚未发布或已归档", BizCode.APP_NOT_PUBLISHED
                )

            release_policy = reference.get("release_policy", "pinned")
            effective_release_id = (
                referenced_app.current_release_id
                if release_policy == "current"
                else reference.get("release_id")
            )
            try:
                effective_release_uuid = uuid.UUID(str(effective_release_id))
            except (TypeError, ValueError, AttributeError) as exc:
                raise BusinessException(
                    "Agent 引用缺少有效发布版本", BizCode.RELEASE_NOT_FOUND, cause=exc
                ) from exc

            referenced_release = self.db.get(AppRelease, effective_release_uuid)
            if (
                    not referenced_release
                    or not referenced_release.is_active
                    or referenced_release.app_id != referenced_app.id
                    or referenced_release.type != AppType.AGENT
            ):
                raise BusinessException(
                    "Agent 发布版本不存在、已下线或归属错误", BizCode.RELEASE_NOT_FOUND
                )

            release_config = (
                referenced_release.config if isinstance(referenced_release.config, dict) else {}
            )
            release_features = release_config.get("features")
            file_upload = (
                release_features.get("file_upload")
                if isinstance(release_features, dict)
                else None
            )
            if not isinstance(file_upload, dict) or file_upload.get("enabled") is not True:
                node_config.pop("files", None)

            reference["app_id"] = str(referenced_app.id)
            reference["release_policy"] = "pinned"
            reference["release_id"] = str(referenced_release.id)
            node_config["reference"] = reference
            node["config"] = node_config

        return pinned_nodes

    def _pin_multi_agent_sub_agents(
            self,
            sub_agents: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Pin sub-agents to concrete AppReleases when publishing a cluster.

        - current (follow latest): resolves to the sub-agent app's release at this
          moment and writes release_id;
        - pinned / legacy entries without release_policy: keep the pinned version;
        - snapshots are normalized to the new shape: agent_id = sub-agent app ID,
          release_policy = pinned, release_id = concrete version, so published
          clusters do not change when a sub-agent is republished.
        Resolution failure (no usable release for a sub-agent) raises and blocks
        publishing.
        """

        pinned: list[dict[str, Any]] = []
        for entry in sub_agents or []:
            item = dict(entry)
            effective_id = resolve_effective_release_id(self.db, item, strict=True)
            release = self.db.get(AppRelease, effective_id)
            if release is None:
                raise BusinessException(
                    f"子 Agent「{item.get('name') or item.get('agent_id')}」的发布版本不存在",
                    BizCode.RELEASE_NOT_FOUND,
                )
            item["agent_id"] = str(release.app_id)
            item["release_policy"] = POLICY_PINNED
            item["release_id"] = str(effective_id)
            pinned.append(item)
        return pinned

    def _assert_publishable_models(
            self,
            config: dict[str, Any] | None,
            default_model_config_id: uuid.UUID | None,
    ) -> None:
        """Publish/rollback gate: snapshot-referenced models must not be disabled or
        sunset; any hit rejects the operation.

        Reference surface = the default model column plus model_id/reranker_id in
        the snapshot JSON (invalid UUID references are skipped).
        """
        assert_refs_publishable(self.db, config_ref_ids(config, default_model_config_id))

    def _get_next_version(self, app_id: uuid.UUID) -> int:
        """Get the next version number.

        Args:
            app_id: App ID

        Returns:
            int: Next version number
        """
        stmt = select(func.max(AppRelease.version)).where(AppRelease.app_id == app_id)
        max_ver = self.db.execute(stmt).scalar()
        return 1 if max_ver is None else int(max_ver) + 1

    def get_current_release(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None
    ) -> AppRelease | None:
        """Get the current release.

        Args:
            app_id: App ID
            workspace_id: Workspace ID (for permission checks)

        Returns:
            Optional[AppRelease]: Current release, or None if not published

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app is not accessible
        """
        logger.debug("获取当前发布版本", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)
        # Read-only operation; shared apps are allowed
        self._validate_app_accessible(app, workspace_id)

        if not app.current_release_id:
            return None

        return self.db.get(AppRelease, app.current_release_id)

    def get_release_by_id(
            self,
            *,
            app_id: uuid.UUID,
            release_id: uuid.UUID
    ) -> AppRelease:
        """Get one active release snapshot by its id.

        Args:
            app_id: App ID
            release_id: Release ID

        Returns:
            AppRelease: The active release snapshot

        Raises:
            BusinessException: When the release does not exist or is inactive
        """
        release = self.db.execute(
            select(AppRelease)
            .where(
                AppRelease.app_id == app_id,
                AppRelease.id == release_id,
                AppRelease.is_active.is_(True),
            )
            .limit(1)
        ).scalars().first()

        if not release:
            raise BusinessException(
                f"版本 {release_id} 不存在或已下线", BizCode.RELEASE_NOT_FOUND
            )
        return release

    def list_releases(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None
    ) -> list[AppRelease]:
        """List all releases of an app (newest first).

        Args:
            app_id: App ID
            workspace_id: Workspace ID (for permission checks)

        Returns:
            List[AppRelease]: List of releases

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app is not accessible
        """
        logger.debug("列出发布版本", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)
        # Read-only operation; shared apps are allowed
        self._validate_app_accessible(app, workspace_id)

        stmt = (
            select(AppRelease)
            .where(AppRelease.app_id == app_id, AppRelease.is_active.is_(True))
            .order_by(AppRelease.version.desc())
        )
        return list(self.db.scalars(stmt).all())

    def rollback(
            self,
            *,
            app_id: uuid.UUID,
            version: int,
            workspace_id: uuid.UUID | None = None
    ) -> AppRelease:
        """Roll back to the given version.

        Args:
            app_id: App ID
            version: Target version number
            workspace_id: Workspace ID (for permission checks)

        Returns:
            AppRelease: The release rolled back to

        Raises:
            ResourceNotFoundException: When the app or version does not exist
            BusinessException: When the app is not in the given workspace
        """
        logger.info("回滚应用", extra={"app_id": str(app_id), "version": version})

        app = self._get_app_or_404(app_id)
        self._validate_app_accessible(app, workspace_id)

        stmt = select(AppRelease).where(
            AppRelease.app_id == app_id,
            AppRelease.version == version
        )
        release = self.db.scalars(stmt).first()

        if not release:
            logger.warning(
                "发布版本不存在",
                extra={"app_id": str(app_id), "version": version}
            )
            raise ResourceNotFoundException("发布版本", f"app_id={app_id}, version={version}")

        # Rollback gate: reject when the target snapshot references a disabled/sunset model
        self._assert_publishable_models(release.config, release.default_model_config_id)

        app.current_release_id = release.id
        app.updated_at = utcnow_naive()

        self.db.commit()
        self.db.refresh(release)

        logger.info(
            "应用回滚成功",
            extra={"app_id": str(app_id), "version": version, "release_id": str(release.id)}
        )
        return release


__all__ = ["AppService", "_AppSerializationContext"]
