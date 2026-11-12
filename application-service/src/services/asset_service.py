"""Tenant resource center (design doc §4.1 / §5 / §6.1).

Scope boundaries this module must keep (they are the whole point of the section):

- **No publish / unpublish endpoint.** Visibility follows the share switches: an
  entry exists because someone shared it, and disappears from the catalog when
  the sharer cancels. There is no state-only entry point. Operating the
  *community market* is a different chain (§6.3, `/sys/market/.../unlist`).
- **No version-forward endpoint.** Advancing a pinned release = re-opening the
  share dialog and sharing again with a newer `release_id`.
- **No sync / my-installs / preview.** A copy diverges from the source the moment
  it is created; "what did I pull" is visible in the space's own app list.

Everything shown in the detail page comes from the pinned release snapshot
(`app_releases`), never from the app draft, so a source edit cannot silently
change what consumers see.
"""
from __future__ import annotations

import copy
import logging
import uuid
from contextlib import nullcontext
from typing import Any

from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from ..errors import (
    BizCode,
    BusinessException,
    ResourceNotFoundException,
    ShareDependencyUnavailableError,
)
from ..models import (
    AgentConfig,
    App,
    AppAsset,
    AppAssetInstallation,
    AppRelease,
    MultiAgentConfig,
    Skill,
    SkillPackage,
    SkillPlugin,
    ToolConfig,
    ToolStatus,
    WorkflowConfig,
)
from ..models.app_asset_model import (
    AssetStatus,
    InstallSource,
    InstallStatus,
    LinkMode,
    ShareMode,
)
from ..models.app_model import AppType
from ..repositories import cross_domain_read
from ..schemas import app_schema
from ..utils.datetime_utils import to_timestamp_ms, utcnow_naive
from ..utils.redis_cache import delete_json
from .app_service import AppService, _expand_usage_examples
from .asset_release_resolver import app_cache_key
from .detail_view import build_display_payload

logger = logging.getLogger(__name__)


def _share_scope_error(reason: str, message: str) -> BusinessException:
    """One code (`1003`) with `data.reason`, so the code segment never grows."""

    return BusinessException(
        message,
        BizCode.INVALID_PARAMETER,
        data={"reason": reason},
    )


def _describe_unavailable_dependencies(issues: list[dict[str, Any]]) -> str:
    """把逐项不可用原因压成一句用户能照着改的话。

    只保留「谁 + 为什么」：`kind` 转成中文名，优先用 `name`（用户认识它），没有
    名字才退回 id；`path`（`app.tools[0]` 这类内部坐标）只在前两者都缺失时使用。
    超过 5 项截断并给出剩余数量，避免一条 `msg` 被几十项撑爆。
    """

    kind_labels = {
        "tool": "工具",
        "tool_method": "工具方法",
        "skill": "技能",
        "agent_release": "Agent 版本",
    }
    rendered: list[str] = []
    for issue in issues:
        label = kind_labels.get(str(issue.get("kind")), "依赖")
        subject = issue.get("name") or issue.get("id") or issue.get("path")
        reason = str(issue.get("reason") or "").strip()
        if subject and reason:
            rendered.append(f"{label}「{subject}」（{reason}）")
        elif subject:
            rendered.append(f"{label}「{subject}」")
        elif reason:
            rendered.append(f"{label}：{reason}")

    if not rendered:
        return "存在不可用的依赖"
    shown = rendered[:5]
    if len(rendered) > len(shown):
        shown.append(f"等 {len(rendered)} 项")
    return "；".join(shown)


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class AssetService:
    """Catalog entries (switches 1/2) for the tenant resource center."""

    def __init__(self, db: Session, app_service: AppService | None = None):
        self.db = db
        self.app_service = app_service or AppService(db)

    # ==================== 共享（发布弹窗） ====================

    def share_to_catalog(
            self,
            *,
            app_id: uuid.UUID,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
            tenant_id: uuid.UUID,
            release_id: uuid.UUID | None,
            enabled: bool,
            allow_editable: bool,
    ) -> AppAsset | None:
        """开关 1/2：把应用（指定版本）放进租户资源中心。

        Returns the entry, or None when switch 1 is off (the caller still has to
        handle switch 3, which is independent).
        """

        app = self.app_service._get_app_or_404(app_id)
        self.app_service._validate_workspace_access(app, workspace_id)
        self._assert_can_share(app, user_id, workspace_id)

        if not enabled:
            # 开关 1 关闭时开关 2 无意义（400 1003 + data.reason，§6.0）
            if allow_editable:
                raise _share_scope_error(
                    "INVALID_SHARE_SCOPE", "开关 1 关闭时开关 2 无意义"
                )
            # Switch 1 off: keep whatever the previous submission decided; the
            # sharer cancels explicitly through DELETE /assets/{id} (§5.4).
            return None

        release = self._resolve_release(app, release_id)
        self._assert_share_metadata_complete(release)
        self._assert_share_dependencies_available(release, tenant_id)
        return self._upsert_asset(
            app=app,
            release=release,
            user_id=user_id,
            tenant_id=tenant_id,
            allow_editable=allow_editable,
            visible=True,
        )

    def ensure_market_carrier(
            self,
            *,
            app_id: uuid.UUID,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
            tenant_id: uuid.UUID,
            release_id: uuid.UUID | None,
            allow_editable: bool = False,
    ) -> tuple[AppAsset, AppRelease]:
        """开关 3 单独打开时的载体行。

        `market_sync.asset_id` 是指向 `app_assets` 的外键，而开关 3 可以与开关 1
        完全独立（§6.0「可以只开它一个」）。因此这里确保条目存在，但用
        `status=unpublished` 让它在资源中心不展示——行只承载市场指针，不改变
        「共享到资源中心」这个语义。已存在的条目**不降级**：开关 1 关闭时保留
        上一次的决定，取消共享走 `DELETE /assets/{id}`。
        """

        app = self.app_service._get_app_or_404(app_id)
        self.app_service._validate_workspace_access(app, workspace_id)
        self._assert_can_share(app, user_id, workspace_id)

        release = self._resolve_release(app, release_id)
        self._assert_share_metadata_complete(release)
        self._assert_share_dependencies_available(release, tenant_id)
        return self._upsert_asset(
            app=app,
            release=release,
            user_id=user_id,
            tenant_id=tenant_id,
            allow_editable=allow_editable,
            visible=False,
        ), release

    def _upsert_asset(
            self,
            *,
            app: App,
            release: AppRelease,
            user_id: uuid.UUID,
            tenant_id: uuid.UUID,
            allow_editable: bool,
            visible: bool,
    ) -> AppAsset:
        """建/更新条目。一个应用在一个租户内只有一个条目（唯一约束）。"""

        now = utcnow_naive()
        asset = self.db.scalars(
            select(AppAsset).where(
                AppAsset.tenant_id == tenant_id,
                AppAsset.source_app_id == app.id,
            )
        ).first()

        if asset is None:
            asset = AppAsset(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                source_app_id=app.id,
                source_workspace_id=app.workspace_id,
                published_release_id=release.id,
                published_version=release.version,
                share_mode=ShareMode.EDITABLE if allow_editable else ShareMode.READONLY,
                # 仅作市场指针载体时不可见；开关 1 打开时立刻可见
                status=AssetStatus.PUBLISHED if visible else AssetStatus.UNPUBLISHED,
                view_count=0,
                # 与迁移回填口径一致（`COALESCE(r.description, a.description)`）：
                # 版本快照是不可变的，改应用描述不会回写已发版本；用户在创建后
                # 才补的描述只存在于 `apps.description`，这里不回退就会漏掉。
                description=release.description or app.description,
                display_payload=self._display_payload(release),
                created_by=user_id,
                created_at=now,
                updated_at=now,
                is_active=True,
            )
            self.db.add(asset)
        else:
            # Re-sharing is idempotent on the same release and forwards the
            # version otherwise (§5.4). Re-sharing after an unshare restores
            # visibility on the release that was already pinned.
            if asset.published_release_id != release.id:
                asset.published_release_id = release.id
                asset.published_version = release.version
            asset.share_mode = (
                ShareMode.EDITABLE if allow_editable else ShareMode.READONLY
            )
            if visible and asset.status == AssetStatus.UNPUBLISHED:
                asset.status = AssetStatus.PUBLISHED
            asset.description = release.description or app.description
            asset.display_payload = self._display_payload(release)
            asset.updated_at = now
            asset.is_active = True

        self.db.flush()
        logger.info(
            "共享到资源中心",
            extra={
                "app_id": str(app.id),
                "asset_id": str(asset.id),
                "release_id": str(release.id),
                "share_mode": asset.share_mode,
                "visible": asset.status == AssetStatus.PUBLISHED,
            },
        )
        return asset

    def invalidate_reference_runtime_caches(
            self,
            asset: AppAsset,
            *,
            extra_workspace_ids: set[uuid.UUID] | None = None,
    ) -> int:
        """Clear runtime caches for active references and just-removed references.

        ``extra_workspace_ids`` covers a reference-to-copy upgrade: its installation
        is already marked removed before commit, so it no longer appears in the
        active-reference query but its source-app cache must still be deleted.
        Redis failures are handled by ``delete_json`` and only fall back to TTL.
        """

        workspace_ids = set(self.db.scalars(
            select(AppAssetInstallation.target_workspace_id).where(
                AppAssetInstallation.asset_id == asset.id,
                AppAssetInstallation.status == InstallStatus.ACTIVE,
                AppAssetInstallation.link_mode == LinkMode.REFERENCE,
            )
        ).all())
        workspace_ids.update(extra_workspace_ids or set())
        for target_workspace_id in workspace_ids:
            delete_json(app_cache_key(asset.source_app_id, target_workspace_id))
        return len(workspace_ids)

    def _assert_can_share(
            self, app: App, user_id: uuid.UUID, workspace_id: uuid.UUID | None
    ) -> None:
        """空间级决定：管理员、应用创建者或同租户超管才能共享该应用。"""

        if workspace_id is None:
            raise BusinessException(
                "缺少工作空间上下文", BizCode.WORKSPACE_NO_ACCESS
            )
        if app.created_by == user_id:
            return
        if cross_domain_read.is_workspace_manager(self.db, user_id, workspace_id):
            return
        if self._is_tenant_superuser(user_id, workspace_id):
            return
        raise BusinessException(
            "只有空间管理员、应用创建者或租户超级管理员可以共享该应用",
            BizCode.PERMISSION_DENIED,
        )

    def _is_tenant_superuser(
            self, user_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> bool:
        """Whether the user is a superuser of the workspace's own tenant.

        Keeps this gate on the same rule as the workspace guard
        (`guards/workspace_access.py`): a superuser may enter any workspace of
        their tenant, so refusing them a space-level action there would be
        self-contradictory. Cross-tenant superusers stay denied.
        """

        user = cross_domain_read.get_user(self.db, user_id)
        if user is None or not user.is_active or not user.is_superuser:
            return False
        workspace = cross_domain_read.get_workspace(self.db, workspace_id)
        return workspace is not None and workspace.tenant_id == user.tenant_id

    def _resolve_release(self, app: App, release_id: uuid.UUID | None) -> AppRelease:
        target_id = release_id or app.current_release_id
        if not target_id:
            raise BusinessException(
                "应用尚未发布，无法共享", BizCode.RELEASE_NOT_FOUND
            )
        release = self.db.scalars(
            select(AppRelease).where(
                AppRelease.id == target_id,
                AppRelease.app_id == app.id,
                AppRelease.is_active.is_(True),
            )
        ).first()
        if release is None:
            raise BusinessException(
                f"版本 {target_id} 不存在或已下线", BizCode.RELEASE_NOT_FOUND
            )
        return release

    def _assert_share_metadata_complete(self, release: AppRelease) -> None:
        """共享前确认快照填齐「实际必填」的展示字段。

        图标、功能分类、使用示例、设置步骤的必填是随发布弹窗逐步落地的，
        SSO 发布入口至今仍允许缺省（预告期，4 周缓冲），因此库里会存在缺字段
        的版本快照。这类版本进资源中心后展示为空，消费方无从判断用途与用法，
        所以统一在共享入口拦住，要求重新发布补齐——旧版本与 SSO 发布的版本
        都走这一道，不需要回改历史数据。
        """

        missing: list[dict[str, Any]] = []
        if not (release.icon or "").strip():
            missing.append({"field": "icon", "reason": "未填写图标"})
        if not (release.categories or []):
            missing.append({"field": "categories", "reason": "未选择功能分类"})

        if release.type in (AppType.AGENT, AppType.MULTI_AGENT):
            examples = _expand_usage_examples(release.usage_examples)
            if len(examples) < app_schema.USAGE_EXAMPLES_MIN_ITEMS:
                missing.append({
                    "field": "usage_examples",
                    "reason": (
                        "使用示例少于 "
                        f"{app_schema.USAGE_EXAMPLES_MIN_ITEMS} 条"
                    ),
                })
        elif release.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            if not (release.setup_steps or "").strip():
                missing.append({"field": "setup_steps", "reason": "未填写设置步骤"})

        if missing:
            # 字段名必须写进 message：错误信封不向前端透出 `data`（见
            # error_core/fastapi_handlers.py 的 business_error_handler），
            # 只说「缺少展示信息」会让用户不知道该补哪一项。
            fields = "、".join(item["reason"] for item in missing)
            raise BusinessException(
                f"该版本{fields}，请重新发布补齐后再共享",
                BizCode.INVALID_PARAMETER,
                data={"reason": "INCOMPLETE_METADATA", "missing": missing},
            )

    def _assert_share_dependencies_available(
            self, release: AppRelease, tenant_id: uuid.UUID
    ) -> None:
        """Reject sharing a snapshot whose executable tools or skills are unavailable."""

        tool_refs: list[dict[str, Any]] = []
        skill_refs: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        visited_releases: set[uuid.UUID] = set()
        seen_refs: set[tuple[str, str, str]] = set()
        seen_issues: set[tuple[str, str, str, str]] = set()

        def add_ref(
                bucket: list[dict[str, Any]],
                kind: str,
                raw_id: Any,
                path: str,
                operation: Any = None,
        ) -> None:
            if isinstance(raw_id, dict):
                raw_id = raw_id.get("id") or raw_id.get(f"{kind}_id")
            text = str(raw_id).strip() if raw_id is not None else ""
            method = str(operation).strip() if operation is not None else ""
            # `method` is part of the key: the same tool can be referenced twice
            # under one path with different (or no) operation, and dropping the
            # method-bearing entry would silently skip method-level validation.
            key = (kind, text, path, method)
            if key not in seen_refs:
                seen_refs.add(key)
                bucket.append({
                    "id": _coerce_uuid(text),
                    "raw_id": text or None,
                    "path": path,
                    # 选中具体方法时的 operation_key；整工具选择为 None
                    "operation": method or None,
                })

        def add_issue(
                kind: str,
                raw_id: Any,
                path: str,
                reason: str,
                name: str | None = None,
        ) -> None:
            text = str(raw_id).strip() if raw_id is not None else ""
            key = (kind, text, path, reason)
            if key in seen_issues:
                return
            seen_issues.add(key)
            unavailable.append({
                "kind": kind,
                "id": text or None,
                "name": name,
                "path": path,
                "reason": reason,
            })

        def inspect_agent_surface(
                config: dict[str, Any], path: str, *, enabled_by_default: bool
        ) -> None:
            tools = config.get("tools")
            if isinstance(tools, list):
                for index, selector in enumerate(tools):
                    if not isinstance(selector, dict):
                        add_ref(tool_refs, "tool", None, f"{path}.tools[{index}]")
                        continue
                    if bool(selector.get("enabled", enabled_by_default)):
                        add_ref(
                            tool_refs,
                            "tool",
                            selector.get("tool_id"),
                            f"{path}.tools[{index}]",
                            selector.get("operation"),
                        )

            skills = config.get("skills")
            if isinstance(skills, dict):
                skill_ids = skills.get("skill_ids") or []
                if not isinstance(skill_ids, list):
                    add_ref(skill_refs, "skill", None, f"{path}.skills.skill_ids")
                else:
                    for index, skill_id in enumerate(skill_ids):
                        add_ref(
                            skill_refs,
                            "skill",
                            skill_id,
                            f"{path}.skills.skill_ids[{index}]",
                        )

        def inspect_nested_release(
                raw_id: Any, path: str, expected_app_id: Any = None
        ) -> None:
            release_id = _coerce_uuid(raw_id)
            expected_id = _coerce_uuid(expected_app_id)
            if release_id is None:
                add_issue("agent_release", raw_id, path, "引用的 Agent 版本无效")
                return
            if expected_app_id is not None and expected_id is None:
                add_issue("agent_release", raw_id, path, "引用的 Agent 应用无效")
                return
            nested = self.db.get(AppRelease, release_id)
            if (
                    nested is None
                    or not nested.is_active
                    or nested.type != AppType.AGENT
                    or (expected_id is not None and nested.app_id != expected_id)
            ):
                add_issue(
                    "agent_release", release_id, path, "引用的 Agent 版本不存在、已下线或归属错误"
                )
                return
            nested_app = self.db.get(App, nested.app_id)
            nested_tenant_id = (
                cross_domain_read.get_workspace_tenant_id(self.db, nested_app.workspace_id)
                if nested_app is not None and nested_app.is_active
                else None
            )
            if (
                    nested_app is None
                    or not nested_app.is_active
                    or nested_app.type != AppType.AGENT
                    or nested_tenant_id != tenant_id
            ):
                add_issue(
                    "agent_release", release_id, path, "引用的 Agent 应用不存在、已停用或跨租户"
                )
                return
            inspect_release(nested, path)

        def inspect_workflow_nodes(value: Any, path: str) -> None:
            if isinstance(value, list):
                for index, child in enumerate(value):
                    inspect_workflow_nodes(child, f"{path}[{index}]")
                return
            if not isinstance(value, dict):
                return

            node_type = value.get("type")
            node_config = value.get("config")
            config_path = f"{path}.config"
            if not isinstance(node_config, dict):
                data = value.get("data")
                node_config = data.get("config") if isinstance(data, dict) else None
                config_path = f"{path}.data.config"

            if node_type == "tool":
                node_parameters = (
                    node_config.get("tool_parameters")
                    if isinstance(node_config, dict)
                    else None
                )
                add_ref(
                    tool_refs,
                    "tool",
                    node_config.get("tool_id") if isinstance(node_config, dict) else None,
                    config_path,
                    node_parameters.get("operation")
                    if isinstance(node_parameters, dict)
                    else None,
                )
            elif node_type == "agent" and isinstance(node_config, dict):
                if node_config.get("mode", "inline") == "reference":
                    reference = node_config.get("reference")
                    reference = reference if isinstance(reference, dict) else {}
                    inspect_nested_release(
                        reference.get("release_id"),
                        f"{config_path}.reference",
                        reference.get("app_id"),
                    )
                else:
                    inspect_agent_surface(
                        node_config, config_path, enabled_by_default=True
                    )

            for key, child in value.items():
                inspect_workflow_nodes(child, f"{path}.{key}")

        def inspect_release(current: AppRelease, path: str) -> None:
            if current.id in visited_releases:
                return
            visited_releases.add(current.id)
            config = current.config if isinstance(current.config, dict) else {}

            if current.type == AppType.AGENT:
                inspect_agent_surface(config, path, enabled_by_default=False)
            elif current.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
                inspect_workflow_nodes(config.get("nodes") or [], f"{path}.nodes")
            elif current.type == AppType.MULTI_AGENT:
                supervisor = config.get("supervisor_config")
                if isinstance(supervisor, dict):
                    inspect_agent_surface(
                        supervisor,
                        f"{path}.supervisor_config",
                        enabled_by_default=False,
                    )
                for index, entry in enumerate(config.get("sub_agents") or []):
                    if not isinstance(entry, dict):
                        add_issue(
                            "agent_release",
                            None,
                            f"{path}.sub_agents[{index}]",
                            "子 Agent 引用格式无效",
                        )
                        continue
                    inspect_nested_release(
                        entry.get("release_id"),
                        f"{path}.sub_agents[{index}]",
                        entry.get("agent_id"),
                    )

        inspect_release(release, "app")

        valid_skill_refs: list[tuple[dict[str, Any], Skill]] = []
        skill_ids = {ref["id"] for ref in skill_refs if ref["id"] is not None}
        skill_rows = (
            self.db.scalars(select(Skill).where(Skill.id.in_(skill_ids))).all()
            if skill_ids
            else []
        )
        skills_by_id = {row.id: row for row in skill_rows}
        for ref in skill_refs:
            skill = skills_by_id.get(ref["id"])
            if ref["id"] is None or skill is None:
                add_issue("skill", ref["raw_id"], ref["path"], "技能不存在")
            elif skill.tenant_id != tenant_id and not skill.is_public:
                add_issue("skill", ref["raw_id"], ref["path"], "无权访问该技能")
            elif (
                    skill.tenant_id != tenant_id
                    and (skill.skill_format or "legacy") == "standard"
            ):
                add_issue(
                    "skill",
                    ref["raw_id"],
                    ref["path"],
                    "跨租户标准技能运行时不可用",
                    skill.name,
                )
            elif not skill.is_active:
                add_issue(
                    "skill", ref["raw_id"], ref["path"], "技能已删除或停用", skill.name
                )
            else:
                valid_skill_refs.append((ref, skill))

        package_ids = {
            skill.package_id
            for _, skill in valid_skill_refs
            if (skill.skill_format or "legacy") == "standard" and skill.package_id
        }
        package_rows = (
            self.db.scalars(
                select(SkillPackage).where(SkillPackage.id.in_(package_ids))
            ).all()
            if package_ids
            else []
        )
        packages_by_id = {row.id: row for row in package_rows}
        plugin_ids = {row.plugin_id for row in package_rows if row.plugin_id}
        plugin_rows = (
            self.db.scalars(
                select(SkillPlugin).where(SkillPlugin.id.in_(plugin_ids))
            ).all()
            if plugin_ids
            else []
        )
        plugins_by_id = {row.id: row for row in plugin_rows}

        for ref, skill in valid_skill_refs:
            if (skill.skill_format or "legacy") == "standard":
                package = packages_by_id.get(skill.package_id)
                if (
                        skill.package_id is None
                        or package is None
                        or not package.is_active
                        or package.tenant_id != skill.tenant_id
                ):
                    add_issue(
                        "skill",
                        ref["raw_id"],
                        ref["path"],
                        "标准技能包不存在或已停用",
                        skill.name,
                    )
                    continue
                if package.plugin_id:
                    plugin = plugins_by_id.get(package.plugin_id)
                    if (
                            plugin is None
                            or not plugin.is_active
                            or plugin.tenant_id != package.tenant_id
                    ):
                        add_issue(
                            "skill",
                            ref["raw_id"],
                            ref["path"],
                            "技能所属插件不存在或已停用",
                            skill.name,
                        )
                continue

            for index, selector in enumerate(skill.tools or []):
                if isinstance(selector, dict):
                    add_ref(
                        tool_refs,
                        "tool",
                        selector.get("tool_id"),
                        f"{ref['path']}.tools[{index}]",
                        selector.get("operation"),
                    )
                else:
                    add_ref(
                        tool_refs,
                        "tool",
                        None,
                        f"{ref['path']}.tools[{index}]",
                    )

        tool_ids = {ref["id"] for ref in tool_refs if ref["id"] is not None}
        tool_rows = (
            self.db.scalars(select(ToolConfig).where(ToolConfig.id.in_(tool_ids))).all()
            if tool_ids
            else []
        )
        tools_by_id = {row.id: row for row in tool_rows}
        # 方法级校验：工具整行还在、但选中的 operation 从 schema 里删掉时，
        # 快照里的 (tool_id, operation) 已经无法执行，必须在共享前拦下。
        methods_checked: set[uuid.UUID] = set()
        methods_by_tool: dict[str, set[str]] = {}
        operation_refs = {
            ref["id"] for ref in tool_refs if ref["id"] is not None and ref["operation"]
        }
        if operation_refs:
            from .bundle_install_service import _run_async
            from .tool_service import ToolService

            items, missing_tools = _run_async(
                lambda: ToolService(self.db).get_tool_methods_batch(
                    [str(tool_id) for tool_id in operation_refs], tenant_id
                )
            )
            if missing_tools:
                # A tool that cannot be looked up has no method list, so its
                # operations pass the check below. Record it to keep「为何放行」
                # diagnosable instead of failing the share silently.
                logger.warning(
                    "共享校验：部分工具查不到，方法级校验被跳过",
                    extra={
                        "tool_ids": missing_tools,
                        "tenant_id": str(tenant_id),
                    },
                )
            methods_by_tool = {
                item["tool_id"]: {
                    str(method.get("operation_key"))
                    for method in item.get("methods") or []
                    if method.get("operation_key") is not None
                }
                for item in items
            }
            methods_checked = set(operation_refs)

        for ref in tool_refs:
            tool = tools_by_id.get(ref["id"])
            if ref["id"] is None or tool is None:
                add_issue("tool", ref["raw_id"], ref["path"], "工具不存在")
            elif tool.tenant_id != tenant_id:
                add_issue("tool", ref["raw_id"], ref["path"], "工具不属于当前租户")
            elif not tool.is_active:
                add_issue(
                    "tool", ref["raw_id"], ref["path"], "工具已删除或停用", tool.name
                )
            elif tool.status != ToolStatus.AVAILABLE.value:
                add_issue(
                    "tool",
                    ref["raw_id"],
                    ref["path"],
                    f"工具状态不可用：{tool.status}",
                    tool.name,
                )
            elif (
                    ref["operation"]
                    and ref["id"] in methods_checked
                    # 方法列表为空（如 MCP 缓存尚未同步）无法判定，按不可校验放行，
                    # 避免把「查不到方法」误报成「方法被删」
                    and methods_by_tool.get(str(ref["id"]))
                    and ref["operation"] not in methods_by_tool[str(ref["id"])]
            ):
                add_issue(
                    "tool_method",
                    ref["raw_id"],
                    ref["path"],
                    f"工具方法不存在：{ref['operation']}",
                    tool.name,
                )

        if unavailable:
            # `data` 恒为 `{}`（统一错误契约 §4.1）——逐项原因只能进 `msg`，
            # 否则前端拿到的是通用「请求参数无效」，用户不知道该改哪个工具。
            raise ShareDependencyUnavailableError(
                details=_describe_unavailable_dependencies(unavailable)
            )

    def _display_payload(self, release: AppRelease) -> dict[str, Any]:
        """同租户内无需脱敏（§7.0）：真实配置 + 全部展示字段。"""

        return {
            "name": release.name,
            "description": release.description,
            "icon": release.icon,
            "icon_type": release.icon_type,
            "app_type": release.type,
            "version_name": release.version_name,
            "changelog": release.release_notes,
            "categories": list(release.categories or []),
            "usage_examples": _expand_usage_examples(release.usage_examples),
            "setup_steps": release.setup_steps,
            "config": release.config or {},
        }

    # ==================== 取消共享 ====================

    def unshare_asset(
            self,
            *,
            asset_id: uuid.UUID,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
            tenant_id: uuid.UUID,
    ) -> AppAsset:
        """条目置 `unpublished`：不再展示，引用型消费方立即失效，副本不受影响。"""

        asset = self._get_asset_or_404(asset_id, tenant_id)
        app = self.app_service._get_app_or_404(asset.source_app_id)
        self._assert_can_share(app, user_id, workspace_id)

        asset.status = AssetStatus.UNPUBLISHED
        asset.updated_at = utcnow_naive()
        self.db.commit()
        invalidated = self.invalidate_reference_runtime_caches(asset)

        logger.info(
            "取消共享资源中心条目",
            extra={
                "asset_id": str(asset_id),
                "tenant_id": str(tenant_id),
                "invalidated_reference_caches": invalidated,
            },
        )
        return asset

    def remove_reference_installation(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            tenant_id: uuid.UUID,
            commit: bool = True,
    ) -> AppAsset | None:
        """Soft-delete this workspace's active catalog reference, if present.

        ``None`` is a normal result so the shared-app endpoint can remove a
        legacy share and a reference together in one transaction. Copies remain
        local applications and are deliberately excluded.
        """

        row = self.db.execute(
            select(AppAssetInstallation, AppAsset)
            .join(AppAsset, AppAssetInstallation.asset_id == AppAsset.id)
            .where(
                AppAsset.source_app_id == app_id,
                AppAsset.tenant_id == tenant_id,
                AppAssetInstallation.tenant_id == tenant_id,
                AppAssetInstallation.target_workspace_id == workspace_id,
                AppAssetInstallation.source == InstallSource.CATALOG,
                AppAssetInstallation.link_mode == LinkMode.REFERENCE,
                AppAssetInstallation.status == InstallStatus.ACTIVE,
            )
        ).one_or_none()
        if row is None:
            return None

        installation, asset = row
        installation.status = InstallStatus.REMOVED
        installation.updated_at = utcnow_naive()
        if commit:
            self.db.commit()
            delete_json(app_cache_key(asset.source_app_id, workspace_id))

        logger.info(
            "移除资源中心仅使用应用",
            extra={
                "app_id": str(app_id),
                "asset_id": str(asset.id),
                "workspace_id": str(workspace_id),
            },
        )
        return asset

    # ==================== 改共享权限 ====================

    def update_share_mode(
            self,
            *,
            asset_id: uuid.UUID,
            tenant_id: uuid.UUID,
            share_mode: str,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
    ) -> AppAsset:
        """切换条目的 `share_mode`（`readonly` ↔ `editable`）。

        「我的共享」那一列的权限开关就是它：之前的实现只有共享时一次性设定
        （`_upsert_asset` 里的 `allow_editable`），之后没有任何更新入口，前端
        开关点了不落库。

        只影响**之后**的 `install_asset`：形态由条目实时读取（`install_asset`
        里 `asset.share_mode == ShareMode.EDITABLE`），已产生的安装记录
        `app_asset_installations.permission` 是安装当时的快照，不追溯改写。
        """

        if share_mode not in (ShareMode.READONLY, ShareMode.EDITABLE):
            raise BusinessException(
                "权限值无效，只允许 readonly 或 editable",
                BizCode.INVALID_PARAMETER,
                data={"reason": "INVALID_SHARE_MODE"},
            )

        # `_get_asset_or_404` 已经挡掉不存在 / 已取消共享（unpublished）的条目：
        # 取消共享后列表里没有这一行，也没有可改的开关。
        asset = self._get_asset_or_404(asset_id, tenant_id)
        app = self.app_service._get_app_or_404(asset.source_app_id)
        self._assert_can_share(app, user_id, workspace_id)

        if asset.share_mode != share_mode:
            asset.share_mode = share_mode
            asset.updated_at = utcnow_naive()
            self.db.commit()
            logger.info(
                "更新资源中心条目共享权限",
                extra={
                    "asset_id": str(asset_id),
                    "tenant_id": str(tenant_id),
                    "share_mode": share_mode,
                },
            )
        return asset

    # ==================== 拉取到空间 ====================

    def install_asset(
            self,
            *,
            asset_id: uuid.UUID,
            tenant_id: uuid.UUID,
            user_id: uuid.UUID,
            target_workspace_id: uuid.UUID,
    ) -> dict[str, Any]:
        """把条目拉到目标空间。形态只由条目的 `share_mode` 决定（§6.1）。

        - `readonly` → 只读引用：不产生新 app，运行时读条目钉住的 release 快照
        - `editable` → 可编辑副本：以 release 快照为源在当前空间重建 app，
          集群连同全部子 Agent 一起落地（§7.0.1）

        已有安装时是幂等返回，**只有一个方向会换形态**：已装的是只读引用、条目现在
        放开成 `editable`，则旧安装行转 `removed`、新落一条 `copy`。反方向
        （`copy` → 条目改回 `readonly`）不动：副本是本空间的自有应用，条目改权限
        不能把它收回或降级。

        注意形态变更**只由消费者主动再次安装触发**：发布方改 `share_mode` 只写
        条目（`update_share_mode`），不会往任何消费方的空间里写东西。
        """

        asset = self._get_asset_or_404(asset_id, tenant_id)

        target_tenant = cross_domain_read.get_workspace_tenant_id(self.db, target_workspace_id)
        if target_tenant is None:
            raise ResourceNotFoundException("工作空间", str(target_workspace_id))
        if target_tenant != tenant_id:
            # 资源中心只在租户内可见，跨租户只能走社区市场
            raise BusinessException("只能拉取到同一租户下的空间", BizCode.WORKSPACE_ACCESS_DENIED)

        existing = self.db.scalars(
            select(AppAssetInstallation).where(
                AppAssetInstallation.asset_id == asset.id,
                AppAssetInstallation.target_workspace_id == target_workspace_id,
                AppAssetInstallation.status == InstallStatus.ACTIVE,
            )
        ).first()

        replaced_link_mode: str | None = None
        replaced_reference_workspace_ids: set[uuid.UUID] = set()
        # 「只读引用 → 可编辑副本」是唯一会换形态的方向；其余情况一律幂等返回。
        upgrade = (
            existing is not None
            and existing.link_mode == LinkMode.REFERENCE
            and asset.share_mode == ShareMode.EDITABLE
        )
        # 副本被删除后安装行仍停在 active：副本是独立应用，删除只软删 app，
        # 不碰安装行。再次添加若直接复用，会返回一个已 4007 的 local_app_id，
        # 所以这里把「目标应用已不在」的副本行当作失效行重建。
        stale_copy = (
            existing is not None
            and existing.link_mode == LinkMode.COPY
            and not self._local_copy_alive(existing)
        )
        if existing is not None and not upgrade and not stale_copy:
            # 副本已是目标空间的独立应用，源版本下线不影响它；但 reference
            # 仍依赖条目钉住版本，失效时不能误报为「已就绪」。
            if existing.link_mode == LinkMode.REFERENCE:
                pinned = self.db.get(AppRelease, asset.published_release_id)
                if pinned is None or not pinned.is_active:
                    raise BusinessException("条目钉住的版本已下线", BizCode.RELEASE_NOT_FOUND)
            return self._install_payload(existing, reused=True)

        # 先校验钉住版本，避免升级时把仍可用的旧引用置 removed 后才发现新版本失效。
        release = self.db.get(AppRelease, asset.published_release_id)
        if release is None or not release.is_active:
            raise BusinessException("条目钉住的版本已下线", BizCode.RELEASE_NOT_FOUND)

        # 升级或重建失效副本时，旧安装行与新副本必须在同一个 savepoint：复制集群
        # 或子 Agent 失败时只回滚这次操作，保留原有安装行。
        rebuild = upgrade or stale_copy
        scope = self.db.begin_nested() if rebuild else nullcontext()
        with scope:
            if existing is not None and rebuild:
                # 先让旧行失效再建副本：既避免部分唯一索引冲突，也让副本保留原名称。
                replaced_link_mode = existing.link_mode
                if existing.link_mode == LinkMode.REFERENCE:
                    replaced_reference_workspace_ids.add(existing.target_workspace_id)
                existing.status = InstallStatus.REMOVED
                existing.updated_at = utcnow_naive()
                self.db.flush()

            now = utcnow_naive()
            missing: list[dict[str, Any]] = []
            local_app_id: uuid.UUID | None = None
            link_mode = LinkMode.REFERENCE

            if asset.share_mode == ShareMode.EDITABLE:
                link_mode = LinkMode.COPY
                local_app_id = self._copy_from_release(
                    release=release,
                    source_app_id=asset.source_app_id,
                    target_workspace_id=target_workspace_id,
                    user_id=user_id,
                    missing=missing,
                )

            installation = AppAssetInstallation(
                id=uuid.uuid4(),
                asset_id=asset.id,
                source=InstallSource.CATALOG,
                tenant_id=tenant_id,
                target_workspace_id=target_workspace_id,
                permission=asset.share_mode,
                link_mode=link_mode,
                installed_release_id=release.id,
                local_app_id=local_app_id,
                # 只读引用恒跟随条目版本；副本天生脱钩（§5.3）
                follow_version=link_mode == LinkMode.REFERENCE,
                status=InstallStatus.ACTIVE,
                result="partial" if missing else "success",
                missing_report=missing or None,
                created_by=user_id,
                created_at=now,
                updated_at=now,
            )
            self.db.add(installation)
        self.db.commit()
        invalidated = self.invalidate_reference_runtime_caches(
            asset, extra_workspace_ids=replaced_reference_workspace_ids
        )

        logger.info(
            "从资源中心拉取",
            extra={
                "asset_id": str(asset.id),
                "target_workspace_id": str(target_workspace_id),
                "link_mode": link_mode,
                "result": installation.result,
                "replaced_link_mode": replaced_link_mode,
                "invalidated_reference_caches": invalidated,
            },
        )
        return self._install_payload(
            installation, reused=False, replaced_link_mode=replaced_link_mode
        )

    def _install_payload(
            self,
            installation: Any,
            *,
            reused: bool,
            replaced_link_mode: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": str(installation.id),
            "asset_id": str(installation.asset_id) if installation.asset_id else None,
            "source": installation.source,
            "link_mode": installation.link_mode,
            "permission": installation.permission,
            "local_app_id": (
                str(installation.local_app_id) if installation.local_app_id else None
            ),
            "installed_release_id": (
                str(installation.installed_release_id)
                if installation.installed_release_id else None
            ),
            "result": installation.result,
            # 副本恒可编辑；partial 表示有依赖需要人工补齐
            "needs_config": installation.result == "partial",
            "missing_dependencies": installation.missing_report or [],
            "reused": reused,
            # 本次安装顶掉的旧形态：`reference` → `copy` 升级、或副本被删后重建时非空。
            # 前端据此提示「已重新添加」，避免用户以为点错了没反应。
            "replaced_link_mode": replaced_link_mode,
        }

    # ---------- 以 release 快照为源重建副本 ----------

    def _local_copy_alive(self, installation: AppAssetInstallation) -> bool:
        """副本 installation 是否仍指向一个存活的本地应用。

        副本是目标空间自己的独立应用，删除走普通应用删除（软删 `App`），
        安装行不会被联动改写；所以安装行 active 不等于副本可用，必须回查
        `local_app_id` 对应的应用还在不在。
        """

        if installation.local_app_id is None:
            return False
        local_app = self.db.get(App, installation.local_app_id)
        return bool(
            local_app is not None
            and local_app.is_active
            and local_app.workspace_id == installation.target_workspace_id
        )

    def _copy_from_release(
            self,
            *,
            release: AppRelease,
            source_app_id: uuid.UUID,
            target_workspace_id: uuid.UUID,
            user_id: uuid.UUID,
            missing: list[dict[str, Any]],
    ) -> uuid.UUID:
        """以**版本快照**为源建一个 app（与 `copy_app` 读草稿行的关键差异）。

        副本要脱离源独立存活：集群的全部子 Agent 必须落到同一空间，否则
        副本一发布就掉光子 Agent（`_locate_agent_app` 按 workspace 过滤）。
        """

        source_app = self.app_service._get_app_or_404(source_app_id)
        config = copy.deepcopy(release.config or {})
        now = utcnow_naive()
        is_cross_workspace = target_workspace_id != source_app.workspace_id

        new_app = App(
            id=uuid.uuid4(),
            workspace_id=target_workspace_id,
            created_by=user_id,
            name=self.app_service._unique_app_name(
                release.name, target_workspace_id, source_app.type
            ),
            description=release.description,
            icon=release.icon,
            icon_type=release.icon_type,
            type=release.type,
            visibility="private",
            status="draft",
            tags=(
                list(source_app.tags or [])
                if source_app.workspace_id == target_workspace_id
                else []
            ),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(new_app)
        self.db.flush()

        if new_app.type == AppType.AGENT:
            knowledge_retrieval, memory = self._remap_ws_scoped(
                config,
                target_workspace_id,
                is_cross_workspace,
                missing=missing,
                missing_scope="agent",
            )
            self.db.add(
                AgentConfig(
                    id=uuid.uuid4(),
                    app_id=new_app.id,
                    system_prompt=config.get("system_prompt"),
                    default_model_config_id=release.default_model_config_id,
                    model_parameters=config.get("model_parameters"),
                    knowledge_retrieval=knowledge_retrieval,
                    memory=memory,
                    variables=config.get("variables") or [],
                    tools=config.get("tools") or [],
                    skills=config.get("skills") or {},
                    features=config.get("features") or {},
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        elif new_app.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            nodes = self._remap_workflow_nodes(
                config.get("nodes") or [],
                target_workspace_id=target_workspace_id,
                is_cross_workspace=is_cross_workspace,
                missing=missing,
            )
            self.db.add(
                WorkflowConfig(
                    id=uuid.uuid4(),
                    app_id=new_app.id,
                    workflow_type=config.get("workflow_type") or "workflow",
                    nodes=nodes,
                    edges=config.get("edges") or [],
                    variables=config.get("variables") or [],
                    environment_variables=config.get("environment_variables") or [],
                    execution_config=config.get("execution_config") or {},
                    features=config.get("features") or {},
                    triggers=config.get("triggers") or [],
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        elif new_app.type == AppType.MULTI_AGENT:
            self._copy_cluster_config(
                new_app=new_app,
                release=release,
                config=config,
                target_workspace_id=target_workspace_id,
                user_id=user_id,
                is_cross_workspace=is_cross_workspace,
                missing=missing,
                now=now,
            )

        return new_app.id

    def _remap_ws_scoped(
            self,
            config: dict[str, Any],
            target_workspace_id: uuid.UUID,
            is_cross_workspace: bool,
            *,
            missing: list[dict[str, Any]] | None = None,
            missing_scope: str = "app",
    ) -> tuple[Any, Any]:
        """Rebind workspace-scoped knowledge and memory without leaking source IDs."""

        knowledge_retrieval = config.get("knowledge_retrieval")
        memory = config.get("memory")
        if not is_cross_workspace:
            return copy.deepcopy(knowledge_retrieval), copy.deepcopy(memory)

        target_tenant_id = cross_domain_read.get_workspace_tenant_id(
            self.db, target_workspace_id
        )
        _, kb_ids = self.app_service._collect_resource_ids_from_config(
            None, knowledge_retrieval
        )
        _, available_kb_ids = self.app_service._preload_cross_workspace_resources(
            target_tenant_id, target_workspace_id, set(), kb_ids
        )
        cleaned_knowledge = self.app_service._clean_knowledge_retrieval(
            knowledge_retrieval, available_kb_ids
        )
        cleaned_memory = self.app_service._clean_memory_cross_workspace(
            memory, target_workspace_id
        )

        if missing is not None:
            for entry in (knowledge_retrieval or {}).get("knowledge_bases", []):
                if not isinstance(entry, dict):
                    missing.append({
                        "kind": "knowledge_base",
                        "name": None,
                        "path": missing_scope,
                        "reason": "知识库引用格式无效",
                    })
                    continue
                kb_id = entry.get("kb_id")
                if _coerce_uuid(kb_id) is None:
                    missing.append({
                        "kind": "knowledge_base",
                        "name": str(kb_id) if kb_id is not None else None,
                        "path": missing_scope,
                        "reason": "知识库引用格式无效",
                    })
                elif not self.app_service._is_kb_available(kb_id, available_kb_ids):
                    missing.append({
                        "kind": "knowledge_base",
                        "name": str(kb_id),
                        "path": missing_scope,
                        "reason": "知识库不属于目标工作空间",
                    })
            memory_id = (
                (memory or {}).get("memory_config_id")
                or (memory or {}).get("memory_content")
            )
            if memory_id and cleaned_memory != memory:
                missing.append({
                    "kind": "memory_config",
                    "name": str(memory_id),
                    "path": missing_scope,
                    "reason": "记忆配置不属于目标工作空间",
                })

        return cleaned_knowledge, cleaned_memory

    def _remap_supervisor_config(
            self,
            supervisor_config: dict[str, Any] | None,
            *,
            target_workspace_id: uuid.UUID,
            is_cross_workspace: bool,
            missing: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Apply the same workspace boundary to a cluster supervisor's config."""

        if supervisor_config is None:
            return None
        cleaned = copy.deepcopy(supervisor_config)
        if not isinstance(cleaned, dict):
            missing.append({
                "kind": "supervisor_config",
                "name": None,
                "path": "supervisor_config",
                "reason": "主管配置格式无效，已忽略",
            })
            return None
        knowledge_retrieval, memory = self._remap_ws_scoped(
            cleaned,
            target_workspace_id,
            is_cross_workspace,
            missing=missing,
            missing_scope="supervisor_config",
        )
        cleaned["knowledge_retrieval"] = knowledge_retrieval
        cleaned["memory"] = memory
        return cleaned

    def _remap_workflow_nodes(
            self,
            nodes: list[dict[str, Any]],
            *,
            target_workspace_id: uuid.UUID,
            is_cross_workspace: bool,
            missing: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Remove inaccessible workspace-scoped references from workflow nodes.

        Workflow node JSON has several node-specific schemas, so this walks nested
        dictionaries rather than coupling the catalog copy path to every node type.
        Only knowledge-base and memory-config identifiers are workspace scoped;
        models, tools and skills remain untouched.
        """

        copied = copy.deepcopy(nodes)
        if not is_cross_workspace:
            return copied

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
        target_tenant_id = cross_domain_read.get_workspace_tenant_id(
            self.db, target_workspace_id
        )
        _, available_kb_ids = self.app_service._preload_cross_workspace_resources(
            target_tenant_id, target_workspace_id, set(), kb_ids
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
                    if _coerce_uuid(kb_id) is None:
                        missing.append({
                            "kind": "knowledge_base",
                            "name": str(kb_id) if kb_id is not None else None,
                            "path": path,
                            "reason": "知识库引用格式无效",
                        })
                    elif self.app_service._is_kb_available(kb_id, available_kb_ids):
                        retained.append(clean(entry, f"{path}.knowledge_bases"))
                    else:
                        missing.append({
                            "kind": "knowledge_base",
                            "name": str(kb_id),
                            "path": path,
                            "reason": "知识库不属于目标工作空间",
                        })
                result["knowledge_bases"] = retained

            memory_id = result.get("memory_config_id") or result.get("memory_content")
            if memory_id:
                cleaned_memory = self.app_service._clean_memory_cross_workspace(
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

            for key, child in list(result.items()):
                if key != "knowledge_bases":
                    result[key] = clean(child, f"{path}.{key}")
            return result

        return clean(copied)

    def _copy_cluster_config(
            self,
            *,
            new_app: App,
            release: AppRelease,
            config: dict[str, Any],
            target_workspace_id: uuid.UUID,
            user_id: uuid.UUID,
            is_cross_workspace: bool,
            missing: list[dict[str, Any]],
            now: Any,
    ) -> None:
        """集群副本：遍历一遍即止（不递归），子 Agent 与主 Agent 落在同一空间。"""

        id_map: dict[str, str] = {}
        kept_sub_agents: list[dict[str, Any]] = []

        for entry in config.get("sub_agents") or []:
            sub_release_id = _coerce_uuid(entry.get("release_id"))
            sub_release = (
                self.db.get(AppRelease, sub_release_id) if sub_release_id else None
            )
            if sub_release is None or not sub_release.is_active:
                missing.append(
                    {
                        "kind": "sub_agent",
                        "name": entry.get("name"),
                        "reason": "子 Agent 的钉住版本不存在或已下线",
                    }
                )
                continue
            new_sub_app_id = self._copy_from_release(
                release=sub_release,
                source_app_id=sub_release.app_id,
                target_workspace_id=target_workspace_id,
                user_id=user_id,
                missing=missing,
            )
            id_map[str(sub_release.app_id)] = str(new_sub_app_id)
            item = {k: v for k, v in entry.items() if k != "_ref"}
            item["agent_id"] = str(new_sub_app_id)
            # 副本里的子 Agent 是本地草稿，只能跟随各自当前版本
            item["release_policy"] = "current"
            item["release_id"] = None
            kept_sub_agents.append(item)

        routing_rules = []
        for rule in config.get("routing_rules") or []:
            item = {k: v for k, v in rule.items() if k != "_ref"}
            mapped = id_map.get(str(item.get("target_agent_id")))
            if mapped is None:
                # 目标子 Agent 没落地，留着就是条悬空路由
                continue
            item["target_agent_id"] = mapped
            routing_rules.append(item)

        master_release_id = _coerce_uuid(config.get("master_agent_id"))
        # 副本是草稿、尚无自己的 release，而 `master_agent_id` 存的是主 Agent 自己的
        # release（`multi_agent_model.py:61`）。留着它会让副本的主管去读**源应用**的
        # 快照——既是错误的配置，也是源方私有内容的泄漏路径。所以两种模式下都清空；
        # 只有跨空间时才需要让用户知道要重新指定（同空间是常态，清空后照常可运行）。
        if master_release_id is not None:
            if is_cross_workspace:
                missing.append(
                    {
                        "kind": "master_agent",
                        "name": None,
                        "reason": "主 Agent 属于源应用，副本发布时会重新指向自身版本",
                    }
                )
            master_release_id = None

        self.db.add(
            MultiAgentConfig(
                id=uuid.uuid4(),
                app_id=new_app.id,
                master_agent_id=master_release_id,
                master_agent_name=config.get("master_agent_name"),
                default_model_config_id=release.default_model_config_id,
                model_parameters=config.get("model_parameters"),
                orchestration_mode=config.get("orchestration_mode") or "supervisor_loop",
                sub_agents=kept_sub_agents,
                routing_rules=routing_rules or None,
                execution_config=config.get("execution_config") or {},
                supervisor_config=self._remap_supervisor_config(
                    config.get("supervisor_config"),
                    target_workspace_id=target_workspace_id,
                    is_cross_workspace=is_cross_workspace,
                    missing=missing,
                ),
                aggregation_strategy=config.get("aggregation_strategy") or "merge",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )

    # ==================== 浏览与详情 ====================

    def list_assets(
            self,
            *,
            tenant_id: uuid.UUID,
            category: str | None = None,
            q: str | None = None,
            app_type: str | None = None,
            page: int = 1,
            pagesize: int = 10,
    ) -> tuple[list[dict[str, Any]], int]:
        """租户内浏览：分类命中数组任一元素即匹配；`q` 命中名称/描述/发布人。"""

        stmt = (
            select(AppAsset, App, AppRelease)
            .join(App, App.id == AppAsset.source_app_id)
            .join(AppRelease, AppRelease.id == AppAsset.published_release_id)
            .where(
                AppAsset.tenant_id == tenant_id,
                AppAsset.status == AssetStatus.PUBLISHED,
                AppAsset.is_active.is_(True),
                App.is_active.is_(True),
            )
        )
        if category:
            # `app_releases.categories` 是 **JSON** 列（不是 JSONB）。SQLAlchemy 的
            # `.contains()` 只在 JSONB 上翻译成包含运算符 `@>`；遇到 JSON 会退化成
            # `col LIKE '%' || :param || '%'`，而 Postgres 没有 `json ~~ text`
            # 这个运算符，于是整个列表接口 500（`UndefinedFunction`）。
            # 显式 cast 到 JSONB 才能拿到「数组含该元素」的语义，且不动列类型、
            # 无需迁移——存量数据照原样读。
            stmt = stmt.where(cast(AppRelease.categories, JSONB).contains([category]))
        if app_type:
            stmt = stmt.where(AppRelease.type == app_type)
        if q:
            like = f"%{q}%"
            # 发布人是跨域 users 表，必须在分页前纳入同一条查询；否则仅命中
            # 发布人时记录已被名称/描述条件过滤，且 total 会与列表不一致。
            publisher_name = func.coalesce(
                cross_domain_read.users.c.username,
                cross_domain_read.users.c.email,
            )
            stmt = (
                stmt.outerjoin(
                    cross_domain_read.users,
                    AppRelease.published_by == cross_domain_read.users.c.id,
                )
                .where(
                    or_(
                        AppRelease.name.ilike(like),
                        AppRelease.description.ilike(like),
                        publisher_name.ilike(like),
                    )
                )
            )

        total = self.db.scalar(
            select(func.count()).select_from(stmt.subquery())
        ) or 0

        rows = self.db.execute(
            stmt.order_by(AppAsset.updated_at.desc())
            .offset((page - 1) * pagesize)
            .limit(pagesize)
        ).all()

        items = [self._serialize_card(asset, app, release) for asset, app, release in rows]
        self._fill_publisher_names(items, rows)
        self._fill_install_counts(items, tenant_id)
        return items, total

    def get_asset(
            self, *, asset_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> dict[str, Any]:
        """详情：展示字段 + 关联技能/工具；打开即 +1 浏览（§6.1.1 口径 1）。"""

        asset = self._get_asset_or_404(asset_id, tenant_id)
        app = self.app_service._get_app_or_404(asset.source_app_id)
        release = self.db.get(AppRelease, asset.published_release_id)

        asset.view_count = (asset.view_count or 0) + 1
        self.db.commit()

        payload = asset.display_payload or {}
        publisher = cross_domain_read.get_user_display_names_by_ids(
            self.db, {release.published_by} if release and release.published_by else set()
        )
        linked_skills, linked_tools = self._linked_resources(release, tenant_id)
        app_type = release.type if release else app.type
        config = payload.get("config") or {}

        return {
            "id": str(asset.id),
            "source_app_id": str(asset.source_app_id),
            "source_app_name": release.name if release else app.name,
            "source_app_type": release.type if release else app.type,
            "description": asset.description,
            "catalog_description": app.description,
            "icon": (payload.get("icon") or None),
            "icon_type": (payload.get("icon_type") or None),
            "published_version": asset.published_version,
            "published_version_name": payload.get("version_name"),
            "source_app_version": self._source_app_version(app),
            "has_newer_version": self._has_newer_version(asset, app),
            "view_count": asset.view_count,
            "publisher_name": next(iter(publisher.values()), None),
            "share_mode": asset.share_mode,
            "categories": payload.get("categories") or [],
            "usage_examples": payload.get("usage_examples") or [],
            # 发布快照优先；nullable 历史快照为空时回退资源条目中已冻结的字段。
            "setup_steps": (release.setup_steps if release else None)
            or payload.get("setup_steps"),
            "changelog": payload.get("changelog"),
            "linked_skills": linked_skills,
            "linked_tools": linked_tools,
            "install_count": self._count_installations(asset.id, tenant_id),
            "my_installations": self._my_installations(asset.id, tenant_id),
            # 详情页图形区（§6.1.3）。不要在这里直接返回 `config`：节点 config 里
            # 有 `url` / `headers` / `model_id` / `knowledge_bases`，跨租户不安全；
            # agent 也不展示配置。图形区只给「画图要画的东西」。
            "display_payload": build_display_payload(app_type, config),
            "updated_at": to_timestamp_ms(asset.updated_at),
        }

    def _serialize_card(
            self, asset: AppAsset, app: App, release: AppRelease
    ) -> dict[str, Any]:
        payload = asset.display_payload or {}
        return {
            "id": str(asset.id),
            "source_app_id": str(asset.source_app_id),
            "source_app_name": release.name,
            "source_app_type": release.type,
            "description": asset.description,
            "icon": payload.get("icon") or None,
            "icon_type": payload.get("icon_type") or None,
            "published_version": asset.published_version,
            "source_app_version": self._source_app_version(app),
            "has_newer_version": self._has_newer_version(asset, app),
            "view_count": asset.view_count or 0,
            "share_mode": asset.share_mode,
            "categories": payload.get("categories") or [],
            "publisher_name": None,
            "updated_at": to_timestamp_ms(asset.updated_at),
        }

    def _source_app_version(self, app: App) -> int | None:
        if not app.current_release_id:
            return None
        return self.db.scalar(
            select(AppRelease.version).where(AppRelease.id == app.current_release_id)
        )

    def _has_newer_version(self, asset: AppAsset, app: App) -> bool:
        latest = self._source_app_version(app)
        return latest is not None and latest > asset.published_version

    def _fill_publisher_names(
            self, items: list[dict[str, Any]], rows: list[Any]
    ) -> None:
        publisher_ids = {
            release.published_by
            for _asset, _app, release in rows
            if release.published_by
        }
        names = cross_domain_read.get_user_display_names_by_ids(self.db, publisher_ids)
        for item, (_asset, _app, release) in zip(items, rows):
            item["publisher_name"] = names.get(release.published_by)

    def _count_installations(self, asset_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
        from ..models import AppAssetInstallation

        return self.db.scalar(
            select(func.count())
            .select_from(AppAssetInstallation)
            .where(
                AppAssetInstallation.asset_id == asset_id,
                AppAssetInstallation.tenant_id == tenant_id,
                AppAssetInstallation.status == InstallStatus.ACTIVE,
            )
        ) or 0

    def _fill_install_counts(
            self, items: list[dict[str, Any]], tenant_id: uuid.UUID
    ) -> None:
        from ..models import AppAssetInstallation

        if not items:
            return
        ids = [uuid.UUID(item["id"]) for item in items]
        counts = dict(
            self.db.execute(
                select(
                    AppAssetInstallation.asset_id,
                    func.count(),
                )
                .where(
                    AppAssetInstallation.asset_id.in_(ids),
                    AppAssetInstallation.tenant_id == tenant_id,
                    AppAssetInstallation.status == InstallStatus.ACTIVE,
                )
                .group_by(AppAssetInstallation.asset_id)
            ).all()
        )
        for item in items:
            item["install_count"] = counts.get(uuid.UUID(item["id"]), 0)

    def _my_installations(
            self, asset_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        from ..models import AppAssetInstallation

        rows = self.db.scalars(
            select(AppAssetInstallation).where(
                AppAssetInstallation.asset_id == asset_id,
                AppAssetInstallation.tenant_id == tenant_id,
                AppAssetInstallation.status == InstallStatus.ACTIVE,
            )
        ).all()
        return [
            {
                "id": str(row.id),
                "target_workspace_id": str(row.target_workspace_id),
                "link_mode": row.link_mode,
                "local_app_id": str(row.local_app_id) if row.local_app_id else None,
                "installed_version": self._installed_version(row),
            }
            for row in rows
        ]

    def _installed_version(self, row: Any) -> int | None:
        if not row.installed_release_id:
            return None
        return self.db.scalar(
            select(AppRelease.version).where(AppRelease.id == row.installed_release_id)
        )

    def _linked_resources(
            self, release: AppRelease | None, tenant_id: uuid.UUID
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        """该版本实际引用到的技能与工具。

        独立技能返回 ``skill``；插件成员按所属插件归并为一个 ``plugin``，不向详情
        页展开成员。工具在既有数组结构上保留 ``tool_type`` 与 ``icon``，供前端按
        工具类型展示。资源中心不以市场的 MCP 准入规则过滤工具。

        工具有两个来源，必须都收：

        - ``config.tools``：agent / 集群的「工具」配置块；
        - ``config.nodes`` 里 ``type == "tool"`` 的节点：**工作流的工具是节点**，
          不在 ``config.tools`` 里。只读前者会让所有工作流的 ``linked_tools``
          恒为空（快照里明明挂了三个工具节点）。

        技能只有一个来源 ``config.skills``：工作流没有技能概念。
        """

        if release is None or not release.config:
            return [], []
        config = release.config or {}
        candidate_configs = [config]
        supervisor = config.get("supervisor_config")
        if isinstance(supervisor, dict):
            candidate_configs.append(supervisor)

        tool_ids: list[uuid.UUID] = []
        skill_ids: list[uuid.UUID] = []
        for cfg in candidate_configs:
            for tool in cfg.get("tools") or []:
                if not isinstance(tool, dict) or not tool.get("enabled"):
                    continue
                tool_id = _coerce_uuid(tool.get("tool_id"))
                if tool_id and tool_id not in tool_ids:
                    tool_ids.append(tool_id)
            skills = cfg.get("skills")
            if isinstance(skills, dict):
                for raw in skills.get("skill_ids") or []:
                    skill_id = _coerce_uuid(raw)
                    if skill_id and skill_id not in skill_ids:
                        skill_ids.append(skill_id)
            # 工作流的工具是节点（`type == "tool"`），工具 id 在节点的
            # `config.tool_id`，与 `config.tools` 是两套结构。agent / 集群的快照
            # 没有 `nodes` 键，这里扫不到东西，所以不必按 release.type 分支。
            # 循环 / 迭代容器里的工具节点同样是平铺在 `nodes` 里的，一并收。
            for node in cfg.get("nodes") or []:
                if not isinstance(node, dict) or node.get("type") != "tool":
                    continue
                node_config = node.get("config")
                if not isinstance(node_config, dict):
                    continue
                tool_id = _coerce_uuid(node_config.get("tool_id"))
                if tool_id and tool_id not in tool_ids:
                    tool_ids.append(tool_id)

        linked_skills: list[dict[str, str]] = []
        if skill_ids:
            skill_rows = self.db.execute(
                select(
                    Skill.id,
                    Skill.name,
                    Skill.package_id,
                    SkillPackage.id,
                    SkillPackage.plugin_id,
                    SkillPlugin.id,
                    SkillPlugin.name,
                )
                .outerjoin(
                    SkillPackage,
                    and_(
                        Skill.package_id == SkillPackage.id,
                        SkillPackage.tenant_id == tenant_id,
                        SkillPackage.is_active.is_(True),
                    ),
                )
                .outerjoin(
                    SkillPlugin,
                    and_(
                        SkillPackage.plugin_id == SkillPlugin.id,
                        SkillPlugin.tenant_id == tenant_id,
                        SkillPlugin.is_active.is_(True),
                    ),
                )
                .where(
                    Skill.id.in_(skill_ids),
                    Skill.tenant_id == tenant_id,
                    Skill.is_active.is_(True),
                )
            ).all()
            skills_by_id = {row[0]: row for row in skill_rows}
            displayed_plugins: set[uuid.UUID] = set()
            for skill_id in skill_ids:
                skill = skills_by_id.get(skill_id)
                if skill is None:
                    continue
                skill_name, declared_package_id = skill[1], skill[2]
                package_id, plugin_id, resolved_plugin_id, plugin_name = skill[3:]
                # 有 package_id 却无法 join 到启用的包，说明它已卸载/跨租户，整项隐藏。
                if declared_package_id and package_id is None:
                    continue
                if plugin_id:
                    # 插件成员必须整体归并。关联插件不可用或异常时不能伪装成普通 skill。
                    if resolved_plugin_id is None or not plugin_name:
                        continue
                    if plugin_id in displayed_plugins:
                        continue
                    displayed_plugins.add(plugin_id)
                    linked_skills.append({
                        "name": str(plugin_name),
                        "skill_type": "plugin",
                    })
                elif skill_name:
                    linked_skills.append({
                        "name": str(skill_name),
                        "skill_type": "skill",
                    })

        linked_tools: list[dict[str, Any]] = []
        if tool_ids:
            tool_rows = self.db.execute(
                select(ToolConfig.id, ToolConfig.name, ToolConfig.tool_type,
                       ToolConfig.icon)
                .where(
                    ToolConfig.id.in_(tool_ids),
                    ToolConfig.tenant_id == tenant_id,
                    ToolConfig.is_active.is_(True),
                )
            ).all()
            tools_by_id = {row[0]: row for row in tool_rows}
            for tool_id in tool_ids:
                tool = tools_by_id.get(tool_id)
                if tool is None or not tool[1]:
                    continue
                icon = tool[3]
                linked_tools.append({
                    "name": str(tool[1]),
                    "tool_type": str(tool[2]),
                    # 空串归一成 None：前端拿空串渲染 <img> 会发一次坏请求
                    "icon": str(icon) if icon and str(icon).strip() else None,
                })
        return linked_skills, linked_tools

    def _get_asset_or_404(self, asset_id: uuid.UUID, tenant_id: uuid.UUID) -> AppAsset:
        asset = self.db.scalars(
            select(AppAsset).where(
                AppAsset.id == asset_id,
                AppAsset.tenant_id == tenant_id,
                AppAsset.is_active.is_(True),
            )
        ).first()
        if asset is None:
            raise ResourceNotFoundException("资源中心条目", str(asset_id))
        if asset.status != AssetStatus.PUBLISHED:
            raise BusinessException(
                "该资源已取消共享", BizCode.WORKSPACE_ACCESS_DENIED
            )
        return asset


__all__ = ["AssetService"]