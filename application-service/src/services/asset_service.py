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

import asyncio
import copy
import logging
import uuid
from contextlib import nullcontext
from typing import Any

from sqlalchemy import and_, cast, func, or_, select, update
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
    BuiltinToolConfig,
    MultiAgentConfig,
    Skill,
    SkillPackage,
    SkillPlugin,
    ToolConfig,
    ToolStatus,
    ToolType,
    WorkflowConfig,
)
from ..models.app_asset_model import (
    AssetStatus,
    InstallSource,
    InstallStatus,
    LinkMode,
    MarketSync,
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


def _node_label(node: dict[str, Any], node_config: Any) -> str | None:
    """工作流节点/内联 Agent 的名称：只在共享预览里当展示用，取不到就不填。"""

    if isinstance(node_config, dict):
        for key in ("name", "label"):
            value = node_config.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("name", "label", "id"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
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

    def _metadata_gaps(self, release: AppRelease) -> list[dict[str, Any]]:
        """快照里「实际必填」的展示字段还缺哪些（不抛错，供预览复用）。

        图标、使用示例、设置步骤的必填是随发布弹窗逐步落地的，SSO 发布入口
        在预告期内仍允许缺省（4 周缓冲），因此库里会存在缺字段的版本快照。
        这类版本进资源中心后展示为空，消费方无从判断用途与用法，所以统一在
        共享入口拦住，要求重新发布补齐——旧版本与 SSO 发布的版本都走这一道，
        不需要回改历史数据。

        功能分类（`categories`）**不在**这道门禁里：它从来不是发布的必填项
        （仅投稿社区市场才要求 ≥1 项），SSO 发布接口至今也没有该字段，
        v0.5.9 补齐后才纳入校验。
        """

        missing: list[dict[str, Any]] = []
        if not (release.icon or "").strip():
            missing.append({"field": "icon", "reason": "未填写图标"})

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

        return missing

    def _assert_share_metadata_complete(self, release: AppRelease) -> None:
        """共享入口的硬拦：缺字段就 400 + 1003（`msg` 里点名要补什么）。"""

        missing = self._metadata_gaps(release)
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

    def _collect_dependency_inventory(
            self, release: AppRelease, tenant_id: uuid.UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """快照引用的工具/技能是否可用（返回清单与问题，不抛错）。

        返回 ``(tool_refs, skill_refs, unavailable)``：前两项是收齐的引用（含集群
        子 Agent 递归过来的），第三项是「这个快照投不出去」的原因。共享入口用第三
        项抛错，共享预览把它翻成 ``blockers``——两处共用同一份遍历，避免「预览说
        能投、提交却报错」。
        """

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
                *,
                source: str | None = None,
                source_name: str | None = None,
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
                    # 谁引用的：self / supervisor / workflow_node / sub_agent。共享
                    # 校验用不到，共享预览要按它分组展示（同一工具被主管和子 Agent
                    # 各引一次是两条，不能去重掉）。
                    "source": source,
                    "source_name": source_name,
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
                config: dict[str, Any],
                path: str,
                *,
                enabled_by_default: bool,
                source: str,
                source_name: str | None = None,
        ) -> None:
            tools = config.get("tools")
            if isinstance(tools, list):
                for index, selector in enumerate(tools):
                    if not isinstance(selector, dict):
                        add_ref(
                            tool_refs, "tool", None, f"{path}.tools[{index}]",
                            source=source, source_name=source_name,
                        )
                        continue
                    if bool(selector.get("enabled", enabled_by_default)):
                        add_ref(
                            tool_refs,
                            "tool",
                            selector.get("tool_id"),
                            f"{path}.tools[{index}]",
                            selector.get("operation"),
                            source=source,
                            source_name=source_name,
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
                raw_id: Any,
                path: str,
                expected_app_id: Any = None,
                source_name: str | None = None,
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
            inspect_release(
                nested, path,
                source="sub_agent", source_name=source_name or nested.name,
            )

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
                    source="workflow_node",
                    source_name=_node_label(value, node_config),
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
                        node_config,
                        config_path,
                        enabled_by_default=True,
                        source="workflow_node",
                        source_name=_node_label(value, node_config),
                    )

            for key, child in value.items():
                inspect_workflow_nodes(child, f"{path}.{key}")

        def inspect_release(
                current: AppRelease,
                path: str,
                *,
                source: str = "self",
                source_name: str | None = None,
        ) -> None:
            if current.id in visited_releases:
                return
            visited_releases.add(current.id)
            config = current.config if isinstance(current.config, dict) else {}

            if current.type == AppType.AGENT:
                inspect_agent_surface(
                    config, path, enabled_by_default=False,
                    source=source, source_name=source_name,
                )
            elif current.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
                inspect_workflow_nodes(config.get("nodes") or [], f"{path}.nodes")
            elif current.type == AppType.MULTI_AGENT:
                supervisor = config.get("supervisor_config")
                if isinstance(supervisor, dict):
                    inspect_agent_surface(
                        supervisor,
                        f"{path}.supervisor_config",
                        enabled_by_default=False,
                        source="supervisor",
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
                        source_name=entry.get("name"),
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

        return tool_refs, skill_refs, unavailable

    def _assert_share_dependencies_available(
            self, release: AppRelease, tenant_id: uuid.UUID
    ) -> None:
        """Reject sharing a snapshot whose executable tools or skills are unavailable."""

        _tools, _skills, unavailable = self._collect_dependency_inventory(
            release, tenant_id
        )
        if unavailable:
            # `data` 恒为 `{}`（统一错误契约 §4.1）——逐项原因只能进 `msg`，
            # 否则前端拿到的是通用「请求参数无效」，用户不知道该改哪个工具。
            raise ShareDependencyUnavailableError(
                details=_describe_unavailable_dependencies(unavailable)
            )

    # ==================== 共享预览（弹窗回显 + 确认页） ====================

    def collect_share_preview(
            self,
            *,
            app_id: uuid.UUID,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
            tenant_id: uuid.UUID,
            release_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """共享弹窗与共享确认页要用的只读快照（契约 `GET /apps/{app_id}/share/preview`）。

        三件事刻意不做，每件都有具体代价：

        - **不调 `MarketSubmissionService.prepare()`**。那条路径会走
          `_export_skill_files` 把技能包文件重新导出到对象存储，放进 GET 意味着每开
          一次弹窗就重传一遍。
        - **不写库**。`ensure_market_carrier` 会建 `unpublished` 载体行，那是提交时的
          副作用；预览只看现状。
        - **不碰市场服务**。预览要的数据全在本库（`app_releases` 快照 +
          `apps.origin` + `app_categories` + `market_sync`），市场侧只回显本地已有的
          投稿记录，审核状态不在这里推导。

        「是否已在资源中心」的判据是 ``status == published``，**不是「有没有条目行」**：
        只开开关 3 时后端会为投稿建一条 `unpublished` 载体行做外键占位，它不在资源
        中心展示（`ensure_market_carrier`）。
        """

        app = self.app_service._get_app_or_404(app_id)
        self.app_service._validate_workspace_access(app, workspace_id)
        self._assert_can_share(app, user_id, workspace_id)

        release = self._resolve_release(app, release_id)
        asset = self.db.scalars(
            select(AppAsset).where(
                AppAsset.tenant_id == tenant_id,
                AppAsset.source_app_id == app.id,
                AppAsset.is_active.is_(True),
            )
        ).first()
        sync = None
        if asset is not None:
            sync = self.db.scalars(
                select(MarketSync)
                .where(MarketSync.asset_id == asset.id)
                .order_by(MarketSync.updated_at.desc())
            ).first()

        tool_refs, skill_refs, unavailable = self._collect_dependency_inventory(
            release, tenant_id
        )
        builtin_tools, tool_dependencies = self._preview_tools(tool_refs, tenant_id)
        skill_dependencies = self._preview_skills(skill_refs, tenant_id)

        blockers = self._preview_blockers(release, tenant_id, unavailable)
        market_blockers = self._preview_market_blockers(app=app, release=release)
        blockers.extend(market_blockers)

        published = asset is not None and asset.status == AssetStatus.PUBLISHED
        publisher_names = cross_domain_read.get_user_display_names_by_ids(
            self.db, {release.published_by} if release.published_by else set()
        )
        block_reason = next(
            (item["reason"] for item in market_blockers), None
        )

        return {
            "app_id": str(app.id),
            "app_type": release.type,
            "release": {
                "id": str(release.id),
                "version": release.version,
                "version_name": release.version_name,
                "published_at": to_timestamp_ms(release.published_at),
                "publisher_name": publisher_names.get(release.published_by) or "未知用户",
                "name": release.name,
                "icon": release.icon,
            },
            "resource_center": {
                "shared": published,
                "allow_editable": bool(
                    published and asset.share_mode == ShareMode.EDITABLE
                ),
                "asset_id": str(asset.id) if asset is not None else None,
                "pinned_release_id": (
                    str(asset.published_release_id)
                    if asset is not None and asset.published_release_id
                    else None
                ),
                "pinned_version": (
                    asset.published_version if asset is not None else None
                ),
                "status": (
                    asset.status if asset is not None else AssetStatus.UNPUBLISHED
                ),
            },
            "community_market": {
                "submitted": sync is not None,
                "sync_status": sync.status if sync is not None else None,
                # 市场审核状态不在这里推导（见方法注释）
                "publication_status": None,
                "market_resource_id": (
                    str(sync.market_resource_id)
                    if sync is not None and sync.market_resource_id
                    else None
                ),
                "can_submit": not market_blockers,
                "block_reason": block_reason,
            },
            "builtin_tools": builtin_tools,
            "carried_dependencies": tool_dependencies + skill_dependencies,
            "bindings": self._preview_knowledge_bindings(release),
            "cluster_agents": self._preview_cluster_agents(app, release),
            "blockers": blockers,
            "can_submit_market": not market_blockers,
        }

    def _preview_tools(
            self, tool_refs: list[dict[str, Any]], tenant_id: uuid.UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """把工具引用拆成「内置（按 tool_class 关联）」与「随包/占位」两桶。

        分桶规则与市场投稿的 `classify_tool` 一致（§7.5.2）：`builtin` → 目标企业
        天然就有；`mcp` → 随包并建条目；`custom` → 只留占位行；`workflow` → 直接
        剔除（它只指向源租户的某个工作流，目标租户没有可填的落点）。这里不 import
        市场包（开源版没有它），所以规则写在两边——但它只是分类，不是准入判定，
        漂移的后果是展示分组不同，不会让提交行为不一致。

        随包/占位的条目额外带 `methods`（「所需方法」）：目标企业拿到占位行后要知道
        该工具**被用到了哪些方法**才配得回来。选中具体方法时只列选中的那几个；整工具
        选择时列全量——否则确认页只写一个工具名，用户装完不知道该补哪些 operation。
        """

        ids = {ref["id"] for ref in tool_refs if ref["id"] is not None}
        if not ids:
            return [], []
        rows = self.db.execute(
            select(ToolConfig.id, ToolConfig.name, ToolConfig.tool_type)
            .where(
                ToolConfig.id.in_(ids),
                ToolConfig.tenant_id == tenant_id,
                ToolConfig.is_active.is_(True),
            )
        ).all()
        by_id = {row[0]: row for row in rows}

        builtin_ids = [row[0] for row in rows if row[2] == ToolType.BUILTIN]
        class_by_id: dict[uuid.UUID, str | None] = {}
        if builtin_ids:
            class_by_id = {
                row.id: row.tool_class
                for row in self.db.scalars(
                    select(BuiltinToolConfig).where(
                        BuiltinToolConfig.id.in_(builtin_ids)
                    )
                ).all()
            }

        # 每个工具「被用到了哪些方法」：选中具体 operation 的只算那几个，整工具选择
        # 的标记为 whole 并展开全量。确认页因此能写出「这个占位行要补哪些方法」。
        picked: dict[uuid.UUID, list[str]] = {}
        whole: set[uuid.UUID] = set()
        for ref in tool_refs:
            if ref["id"] is None:
                continue
            operation = ref["operation"]
            if operation:
                ops = picked.setdefault(ref["id"], [])
                if str(operation) not in ops:
                    ops.append(str(operation))
            else:
                whole.add(ref["id"])
        # 只给 custom / mcp 取方法树。workflow 的方法树要构造
        # `WorkflowAsTool(db=self.db)` 并读一次 `workflow_config`（见
        # `ToolService._get_workflow_tool_methods`），而这里的 Session 属于调用方；
        # `_run_async` 在已有运行中事件循环时会把协程丢到私有线程执行，跨线程复用
        # Session 会抛 `InvalidRequestError`，而且会被外层 except 吞成「退回原始
        # 标识」，症状极隐蔽。workflow 的展示名就是工具名本身，本地就取得到。
        labels_by_tool = self._preview_tool_method_labels(
            {
                row[0] for row in rows
                if row[2] in (ToolType.CUSTOM, ToolType.MCP)
            },
            tenant_id,
        )

        def method_labels(tool_id: uuid.UUID) -> list[str]:
            labels, by_operation = labels_by_tool.get(tool_id, ([], {}))
            if tool_id in whole and labels:
                # 整工具选择 = 需要该工具的全部方法。同一工具若又被按 operation
                # 选中（`add_ref` 的去重键含 method，两条都会保留），全量已经覆盖
                # 那些方法，不再并一次。
                return list(labels)
            # 没有方法树时不能直接返回空：同一工具可能既被整选、又被按 operation
            # 选中，此时 `[]` 会把用户明确选中的方法一起丢掉。`labels` 为空的三种
            # 来源 —— 只有 `schema_url` 的 custom（预览不拉远端）、被排除在外的
            # workflow、以及调用方处在事件循环里时预览主动退化（见
            # `_preview_tool_method_labels`）。前两种退回 operation_key 原文即可。
            resolved: list[str] = []
            for operation in picked.get(tool_id, []):
                label = by_operation.get(operation) or operation
                if label not in resolved:
                    resolved.append(label)
            return resolved

        builtin_tools: list[dict[str, Any]] = []
        dependencies: list[dict[str, Any]] = []
        seen_builtin: set[tuple[Any, Any, Any]] = set()
        seen_dependency: set[tuple[str, str]] = set()
        for ref in tool_refs:
            row = by_id.get(ref["id"])
            if row is None:
                # 查不到行的引用由 blockers 负责说明，这里不返回半条记录
                continue
            name = str(row[1]) if row[1] else None
            tool_type = str(row[2])
            if tool_type == ToolType.BUILTIN:
                key = (row[0], ref["source"], ref["source_name"])
                if key in seen_builtin:
                    continue
                seen_builtin.add(key)
                builtin_tools.append({
                    "name": name,
                    "tool_class": class_by_id.get(row[0]),
                    "source": ref["source"] or "self",
                    "source_name": ref["source_name"],
                })
                continue

            if tool_type == ToolType.MCP:
                kind, disposition = "mcp_tool", "bundled"
            elif tool_type == ToolType.WORKFLOW:
                # 工作流工具只指向源租户的某个纯工作流，落地时既不建条目也不建行
                # （见 `bundle_install_service._land_tools`），预览必须同样标 dropped，
                # 否则会在共享前告诉用户「会随包」，装完却什么都没留下。
                kind, disposition = "workflow_tool", "dropped"
            else:
                kind, disposition = "custom_tool", "placeholder"
            key = (kind, name or str(row[0]))
            if key in seen_dependency:
                continue
            seen_dependency.add(key)
            dependencies.append({
                "kind": kind,
                "name": name,
                "disposition": disposition,
                "methods": method_labels(row[0]),
            })
        return builtin_tools, dependencies

    def _preview_tool_method_labels(
            self, tool_ids: set[uuid.UUID], tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, tuple[list[str], dict[str, str]]]:
        """每个工具的「方法展示名」：``(全部方法, {operation 键: 展示名})``。

        展示名与工具列表里看到的一致：自定义工具用 `METHOD:path`（截图里的
        `GET:/example/t-http`），MCP 用远端方法名，工作流工具只有工具名本身。
        `operation` 存在快照里的是 operation_key（`add_ref` 的第四个参数），所以
        选具体方法时按 operation_key 反查展示名；查不到就退回原值，不让预览因为
        方法树解析失败而崩掉——那只是「少了个更友好的名字」。

        走 `ToolService.get_tool_methods_batch`，与共享校验共用同一份方法树口径。
        但只读展示有三个限制，与共享校验（真要在安装后能执行）不同：

        1. MCP 传 `read_only=True`，不会因为缓存为空去连远端；
        2. `allow_remote_schema=False`，只用已存的 `schema_content`。只有
           `schema_url` 的自定义工具会被跳过——`parse_from_url` 是同步
           requests、超时 30 秒，且 schema_url 往往是第三方主机，预览会直接
           卡住并超时，「预览不做外部请求」的约定也就破了。
        3. 调用方只传 custom / mcp 的 id（workflow 见 `_preview_tools`）；
        4. 只在**没有**运行中事件循环时执行：`_run_async` 遇到已有 loop 会另起
           一个线程跑协程，而 `ToolService` 用的是调用方的 Session——SQLAlchemy
           的 Session 不是线程安全的，跨线程查询抛 `InvalidRequestError`，还会
           被下面的 except 吞成「没有方法树」。预览是 GET 的展示装饰，宁可不给
           展示名，也不换线程。

        跳过之后该工具没有方法树，`method_labels()` 自动退回快照里的
        operation_key；牺牲一个更友好的名字，换预览的确定性时延。
        """

        tool_ids = {tool_id for tool_id in tool_ids if tool_id is not None}
        if not tool_ids:
            return {}

        # 见上面第 4 条：有事件循环就直接退化。抛异常而不是继续，是因为继续的
        # 代价是一个可能永远查不出来的跨线程 Session 错误。
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            logger.debug(
                "共享预览：调用方已有事件循环，跳过方法树解析（不跨线程复用 Session）",
                extra={"tool_count": len(tool_ids)},
            )
            return {}

        try:
            # 两个 import 都放在 try 内：`bundle_install_service` 会拉整条工作流/
            # 模型栈，在裁掉那些包的部署形态里导入就会失败——预览是只读装饰，
            # 不能因为「拿不到更友好的方法名」而整个 500。
            from .bundle_install_service import _run_async
            from .tool_service import ToolService

            items, _missing = _run_async(
                lambda: ToolService(self.db).get_tool_methods_batch(
                    [str(tool_id) for tool_id in tool_ids],
                    tenant_id,
                    allow_remote_schema=False,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 预览不因方法树失败而整体 5xx
            logger.warning(
                "共享预览：工具方法树读取失败，所需方法退回原始标识",
                extra={"tool_ids": [str(tool_id) for tool_id in tool_ids],
                       "error": str(exc)},
            )
            return {}

        labels: dict[uuid.UUID, tuple[list[str], dict[str, str]]] = {}
        for item in items:
            try:
                tool_id = uuid.UUID(str(item["tool_id"]))
            except (KeyError, ValueError):
                continue
            all_labels: list[str] = []
            by_operation: dict[str, str] = {}
            for method in item.get("methods") or []:
                verb = str(method.get("method") or "").strip()
                path = str(method.get("path") or "").strip()
                operation_key = method.get("operation_key")
                if verb and path:
                    label = f"{verb}:{path}"
                else:
                    label = str(
                        operation_key or method.get("name") or method.get("method_id") or ""
                    ).strip()
                if not label:
                    continue
                if label not in all_labels:
                    all_labels.append(label)
                if operation_key is not None:
                    by_operation[str(operation_key)] = label
            labels[tool_id] = (all_labels, by_operation)
        return labels

    def _preview_skills(
            self, skill_refs: list[dict[str, Any]], tenant_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """技能/插件：随包并各自建市场条目（`bundled`）。插件按行归并，不展开成员。"""

        ids = {ref["id"] for ref in skill_refs if ref["id"] is not None}
        if not ids:
            return []
        rows = self.db.execute(
            select(
                Skill.id,
                Skill.name,
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
                Skill.id.in_(ids),
                Skill.is_active.is_(True),
            )
        ).all()
        by_id = {row[0]: row for row in rows}

        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for ref in skill_refs:
            row = by_id.get(ref["id"])
            if row is None:
                continue
            if row[2] is not None:
                kind, name = "plugin", str(row[3]) if row[3] else None
            else:
                kind, name = "skill", str(row[1]) if row[1] else None
            key = (kind, name or str(row[0]))
            if key in seen:
                continue
            seen.add(key)
            entries.append({"kind": kind, "name": name, "disposition": "bundled"})
        return entries

    def _preview_knowledge_bindings(self, release: AppRelease) -> list[dict[str, Any]]:
        """知识库只声明「需重新绑定」，文档与向量不跨租户复制（§7.5.3）。

        只扫主配置与集群主管配置，与 `MarketSubmissionService._collect_knowledge_bindings`
        同口径；子 Agent 的知识库在安装侧按它自己的快照重绑。
        """

        config = release.config if isinstance(release.config, dict) else {}
        holders = [config]
        supervisor = config.get("supervisor_config")
        if isinstance(supervisor, dict):
            holders.append(supervisor)

        bindings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for holder in holders:
            retrieval = holder.get("knowledge_retrieval")
            if not isinstance(retrieval, dict):
                continue
            for entry in retrieval.get("knowledge_bases") or []:
                if not isinstance(entry, dict) or not entry.get("enabled", True):
                    continue
                kb_id = entry.get("kb_id")
                if not kb_id or str(kb_id) in seen:
                    continue
                seen.add(str(kb_id))
                row = cross_domain_read.get_knowledge_row(
                    self.db, _coerce_uuid(kb_id)
                )
                bindings.append({
                    "kind": "knowledge_base",
                    "name": getattr(row, "name", None) if row is not None else None,
                })
        return bindings

    def _preview_cluster_agents(
            self, app: App, release: AppRelease
    ) -> list[dict[str, Any]]:
        """随包的集群子 Agent（不单独建市场条目，§7.2.1）。

        读的是快照里**钉住**的 `release_id`（`_pin_multi_agent_sub_agents` 写进去
        的那份），不是子 Agent 的当前草稿——用户看到的就是提交时会带走的版本。
        """

        if app.type != AppType.MULTI_AGENT:
            return []
        config = release.config if isinstance(release.config, dict) else {}
        members: list[dict[str, Any]] = []
        for entry in config.get("sub_agents") or []:
            if not isinstance(entry, dict):
                continue
            sub_release = self.db.get(AppRelease, _coerce_uuid(entry.get("release_id")))
            if sub_release is None or not sub_release.is_active:
                continue
            sub_app = self.db.get(App, sub_release.app_id)
            members.append({
                "agent_id": str(sub_release.app_id),
                "name": (
                    (sub_app.name if sub_app is not None else None)
                    or entry.get("name")
                    or sub_release.name
                ),
                "release_id": str(sub_release.id),
                "version_name": sub_release.version_name,
            })
        return members

    def _preview_blockers(
            self,
            release: AppRelease,
            tenant_id: uuid.UUID,
            unavailable: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """资源中心侧的前置阻断项：与提交时的 400 + 1003 同源。

        提交路径靠抛异常替用户挡住，预览必须改成「列出来」，否则确认页要么什么都
        不提示、要么只能自己去猜一套规则。
        """

        blockers: list[dict[str, Any]] = []
        for gap in self._metadata_gaps(release):
            blockers.append({
                "scope": "resource_center",
                "reason": "INCOMPLETE_METADATA",
                "message": f"该版本{gap['reason']}，请重新发布补齐后再共享",
            })
        for issue in unavailable:
            blockers.append({
                "scope": "resource_center",
                "reason": "DEPENDENCY_UNAVAILABLE",
                "message": _describe_unavailable_dependencies([issue]),
            })
        return blockers

    def _preview_market_blockers(
            self, *, app: App, release: AppRelease
    ) -> list[dict[str, Any]]:
        """社区市场侧的前置阻断项：只看应用自己的行与版本快照。

        判定与投稿入口一一对应（`market_submission_service` 的
        `gate_market_origin` / `gate_market_metadata` / `gate_categories`），`reason`
        取值逐字相同，前端按它分支。这里不调用投稿服务，也不发任何外部请求——预览
        是「打开弹窗时看一眼」，不该依赖市场在线。

        必填元数据按应用类型分流，与 `AppService.publish`（app_service.py:2730-2767）
        以及 `_metadata_gaps` 同一口径：工作流/纯工作流查「设置步骤」，Agent/集群查
        「使用示例」。不分流会让工作流永远投不出去——发布时 `usage_examples` 已被强制
        清空，按 Agent 规则判就恒为 0 条。

        代价是规则有两份实现：改这里的数字或文案时必须同步投稿入口，否则会出现
        「预览说能投、提交被拒」。判定只涉及四个本地字段（`apps.origin`、
        `app_releases.type` / `usage_examples` / `setup_steps` / `categories`），漂移面很小。
        """

        blockers: list[dict[str, Any]] = []

        # 市场来源的副本不能再投回去（产品定案，见 `gate_market_origin`）
        if str(app.origin or "").strip().lower() in ("external", "market"):
            blockers.append({
                "scope": "community_market",
                "reason": "ORIGIN_FROM_MARKET",
                # 与投稿入口的报错文案同一句：确认页提示和提交被拒看到的是同一段话
                "message": "应用是从社区市场复制来的副本，不能再投稿到社区市场",
            })

        workflow_types = (AppType.WORKFLOW, AppType.PURE_WORKFLOW)
        if release.type in workflow_types:
            # 工作流只要求设置步骤，使用示例在发布时已被清空、不参与判定。
            if not (release.setup_steps or "").strip():
                blockers.append({
                    "scope": "community_market",
                    "reason": "SETUP_STEPS_REQUIRED",
                    "message": "投稿社区市场需要填写设置步骤",
                })
        else:
            examples = [
                item for item in _expand_usage_examples(release.usage_examples)
                if isinstance(item, str) and item.strip()
            ]
            minimum = app_schema.USAGE_EXAMPLES_MIN_ITEMS
            if len(examples) < minimum:
                blockers.append({
                    "scope": "community_market",
                    "reason": "USAGE_EXAMPLES_INSUFFICIENT",
                    "message": f"投稿社区市场需要至少 {minimum} 条使用示例，当前 {len(examples)} 条",
                })

        from .category_service import CategoryService

        if not CategoryService(self.db).existing_codes(release.categories or []):
            blockers.append({
                "scope": "community_market",
                "reason": "CATEGORY_REQUIRED",
                "message": "投稿社区市场需要至少选择 1 个功能分类",
            })
        return blockers

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
                user_id=user_id,
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
            user_id: uuid.UUID,
            missing: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Rebind or copy the workspace-scoped references inside workflow nodes.

        Workflow node JSON has several node-specific schemas, so this walks nested
        dictionaries rather than coupling the catalog copy path to every node type.

        Three kinds of reference are handled, and the reason differs per kind:

        * **knowledge base / memory config** — cleaned. Their content is
          workspace-owned, so a copy must not inherit it (`_remap_ws_scoped`).
        * **agent nodes in `reference` mode** — the referenced Agent app is a real
          app row; leaving the source id in place yields a copy whose node can never
          be published (`_pin_workflow_agent_references` rejects a foreign
          workspace). It is copied into the target space instead, exactly like a
          cluster sub-agent.
        * **models / tools / skills** — untouched. They are *tenant*-scoped, and the
          resource center never crosses a tenant, so their ids stay valid.
        """

        copied = copy.deepcopy(nodes)
        if not is_cross_workspace:
            return copied

        # One copy per referenced Agent app, however many nodes point at it.
        copied_agents: dict[uuid.UUID, str] = {}

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

            result = self._remap_workflow_agent_reference(
                result, path, target_workspace_id, user_id, copied_agents, missing
            )

            for key, child in list(result.items()):
                if key != "knowledge_bases":
                    result[key] = clean(child, f"{path}.{key}")
            return result

        return clean(copied)

    def _remap_workflow_agent_reference(
            self,
            node: dict[str, Any],
            path: str,
            target_workspace_id: uuid.UUID,
            user_id: uuid.UUID,
            copied_agents: dict[uuid.UUID, str],
            missing: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Copy the Agent an `agent` node references, then repoint the node at it.

        Mirrors `_copy_cluster_config` for sub-agents: the referenced member lands as
        a draft in the target space, and the node is rewired to `release_policy=current`
        / `release_id=None` because release ids are meaningless in another space.

        A reference that cannot be honoured degrades to inline mode with a `missing`
        entry rather than keeping a dangling `app_id` — the market install path
        (`app_dsl_service._resolve_workflow_nodes`) does the same, and a dangling id
        would only surface later as a publish-time `WORKSPACE_NO_ACCESS`.
        """

        if node.get("type") != "agent":
            return node
        config = node.get("config")
        if not isinstance(config, dict) or config.get("mode") != "reference":
            return node
        reference = config.get("reference")
        if not isinstance(reference, dict):
            return node

        reference_path = f"{path}.config.reference"
        source_app_id = _coerce_uuid(reference.get("app_id"))
        if source_app_id is None:
            missing.append({
                "kind": "agent_ref",
                "name": None,
                "path": reference_path,
                "reason": "Agent 节点引用的应用 id 无效",
            })
            return self._degrade_agent_node(node, config)

        source_app = self.db.get(App, source_app_id)
        if source_app is None or not source_app.is_active:
            missing.append({
                "kind": "agent_ref",
                "name": None,
                "path": reference_path,
                "reason": "Agent 节点引用的应用不存在或已停用",
            })
            return self._degrade_agent_node(node, config)
        if source_app.type != AppType.AGENT:
            missing.append({
                "kind": "agent_ref",
                "name": source_app.name,
                "path": reference_path,
                "reason": "Agent 节点引用的应用不是 Agent 类型",
            })
            return self._degrade_agent_node(node, config)
        if source_app.workspace_id == target_workspace_id:
            # Already local to the target space: the reference stays valid as is.
            return node

        policy = reference.get("release_policy") or "current"
        pinned_release_id = (
            _coerce_uuid(reference.get("release_id"))
            if policy == "pinned"
            else source_app.current_release_id
        )
        release = self.db.get(AppRelease, pinned_release_id) if pinned_release_id else None
        if release is None or not release.is_active or release.app_id != source_app.id:
            missing.append({
                "kind": "agent_ref",
                "name": source_app.name,
                "path": reference_path,
                "reason": "Agent 节点引用的应用没有可用的发布版本",
            })
            return self._degrade_agent_node(node, config)

        new_app_id = copied_agents.get(source_app.id)
        if new_app_id is None:
            new_app_id = str(
                self._copy_from_release(
                    release=release,
                    source_app_id=source_app.id,
                    target_workspace_id=target_workspace_id,
                    user_id=user_id,
                    missing=missing,
                )
            )
            copied_agents[source_app.id] = new_app_id
            # No `missing` entry on success: that list means "缺失", and the cluster
            # copy path behaves the same way for sub-agents. The copy lands as a
            # draft, so the workflow cannot publish until the user publishes this
            # Agent first — the publish gate (`_pin_workflow_agent_references`)
            # reports that with an actionable message.

        node["config"] = {
            **config,
            "reference": {
                **reference,
                "app_id": new_app_id,
                "release_policy": "current",
                "release_id": None,
            },
        }
        return node

    @staticmethod
    def _degrade_agent_node(
            node: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        """Fall back an unresolvable agent reference to an inline node."""

        node["config"] = {**config, "mode": "inline", "reference": None}
        return node

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

        # 浏览数是展示指标，不是资源内容更新：显式把 `updated_at` 写成自身，
        # 压掉列上的 `onupdate`。否则每打开一次详情，条目就被刷成「刚更新」——
        # 列表按 `updated_at` 倒序会把刚被点开的排到最前，「我的共享」的
        # `shared_at` 也会变成刚才。同一句还顺手消掉 SELECT 后 +1 的竞态。
        view_count = self.db.execute(
            update(AppAsset)
            .where(AppAsset.id == asset.id)
            .values(
                view_count=AppAsset.view_count + 1,
                updated_at=AppAsset.updated_at,
            )
            .returning(AppAsset.view_count)
        ).scalar_one()
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
            "view_count": view_count,
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