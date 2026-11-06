# -*- coding: utf-8 -*-
"""偏好写入两级门控。

第一级：记忆配置开关 `preference_engine_enabled`。
第二级：应用当前生效本体的 `ontology.preference_domain`。

Agent / Workflow 按 `conversation_id` 反查 `conversations.app_id` → 有效绑定本体；
无绑定或悬空绑定回退系统默认本体（默认 coding）。自定义本体未勾选偏好时为 NULL，
不继承默认 coding。Service API / MCP 没有应用上下文，只看记忆开关，默认 coding。
召回接口不走这里。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.core.config import settings
from app.db import get_db_context

logger = logging.getLogger(__name__)

DEFAULT_PREFERENCE_DOMAIN = "coding"
_APP_SCOPED_SOURCES = {"agent", "workflow"}

_ONTOLOGY_SUPPORT_STATE: Optional[tuple[bool, float]] = None
_ONTOLOGY_SUPPORT_NEGATIVE_TTL_SECONDS = 60.0


def _mark_ontology_unsupported() -> None:
    """表/列缺失时记录负缓存，避免同一部署每条消息重复失败探测。"""
    global _ONTOLOGY_SUPPORT_STATE
    _ONTOLOGY_SUPPORT_STATE = (False, time.monotonic())


def _ontology_support_status() -> Optional[bool]:
    """本体表探测：True=有表；False=探测成功但无表；None=探测失败/未知。

    仅看 DEPLOYMENT_MODE 默认值会把未显式配置环境变量的私有化部署误判为
    社区版，从而静默跳过本体反查；这里用表存在性探测兜底。
    状态用单个元组赋值，避免多线程读到不一致的 support/checked_at 组合。
    正结果永久缓存；负结果缓存 60s；探测失败返回 None 且不缓存。
    """
    global _ONTOLOGY_SUPPORT_STATE
    state = _ONTOLOGY_SUPPORT_STATE
    now = time.monotonic()
    if state is not None:
        supported, checked_at = state
        if supported:
            return True
        if now - checked_at < _ONTOLOGY_SUPPORT_NEGATIVE_TTL_SECONDS:
            return False
    if settings.DEPLOYMENT_MODE != "community":
        _ONTOLOGY_SUPPORT_STATE = (True, now)
        return True
    try:
        with get_db_context() as db:
            supported = bool(
                db.execute(
                    text(
                        """
                        SELECT to_regclass('public.ontology') IS NOT NULL
                           AND to_regclass('public.app_ontology_binding') IS NOT NULL
                           AND EXISTS (
                               SELECT 1 FROM information_schema.columns
                               WHERE table_name = 'conversations'
                                 AND column_name = 'app_id'
                           )
                        """
                    )
                ).scalar()
            )
    except Exception:
        # 探测失败不缓存，下一次请求重试；由调用方按“未知”跳过偏好任务
        logger.warning("[PreferenceGate] ontology table probe failed", exc_info=True)
        return None
    _ONTOLOGY_SUPPORT_STATE = (supported, now)
    return supported


def _log(
    memory_config: Any,
    conversation_id: str,
    source: str,
    domain: Optional[str],
    result: str,
    reason: str,
) -> None:
    logger.info(
        "[PreferenceGate] workspace_id=%s conversation_id=%s source=%s "
        "domain=%s result=%s reason=%s",
        getattr(memory_config, "workspace_id", None),
        conversation_id,
        source,
        domain,
        result,
        reason,
    )


def resolve_conversation_preference_domain(
    conversation_id: str,
) -> Tuple[Optional[str], bool, Optional[bool]]:
    """按会话反查应用当前生效本体的偏好域。

    Returns:
        (domain, is_default_fallback, conversation_state)
        - 会话不存在： (None, False, False)
        - 查询异常：   (None, False, None)
        - 无绑定 / 悬空绑定： (默认本体 domain, True, True)
        - 绑定自定义本体： (该本体 domain 或 None, False, True)
    """
    try:
        uuid.UUID(str(conversation_id))
    except (TypeError, ValueError):
        return None, False, False

    try:
        with get_db_context() as db:
            row = db.execute(
                text(
                    """
                    SELECT CASE
                             WHEN b.binding_id IS NULL OR o.ontology_id IS NULL
                               THEN (SELECT d.preference_domain
                                     FROM ontology d
                                     WHERE d.tenant_id IS NULL AND d.is_system_default IS TRUE
                                     ORDER BY d.created_at ASC
                                     LIMIT 1)
                             ELSE o.preference_domain
                           END AS preference_domain,
                           (b.binding_id IS NULL OR o.ontology_id IS NULL) AS is_default_fallback
                    FROM conversations c
                    LEFT JOIN app_ontology_binding b
                           ON b.app_id = c.app_id AND b.is_deleted IS FALSE
                    LEFT JOIN ontology o
                           ON o.ontology_id = b.ontology_id
                    WHERE c.id = CAST(:conversation_id AS uuid)
                    LIMIT 1
                    """
                ),
                {"conversation_id": conversation_id},
            ).first()
    except ProgrammingError:
        # 表/列缺失：回写负缓存，避免同一部署每条消息重复失败查询
        _mark_ontology_unsupported()
        logger.error(
            "[PreferenceGate] ontology schema missing conversation_id=%s",
            conversation_id,
            exc_info=True,
        )
        return None, False, None
    except Exception:
        # 连接类异常统一在门控内降级，返回 None 表示“未知”
        logger.error(
            "[PreferenceGate] conversation domain query failed conversation_id=%s",
            conversation_id,
            exc_info=True,
        )
        return None, False, None

    if row is None:
        return None, False, False
    return row[0], bool(row[1]), True


def resolve_effective_preference_domain(
    *,
    memory_config: Any,
    conversation_id: str,
    source: str,
) -> Optional[str]:
    """返回本次写入应使用的偏好域；None 表示本次不跑偏好任务。"""
    if not getattr(memory_config, "preference_engine_enabled", False):
        _log(memory_config, conversation_id, source, None, "skipped", "engine_disabled")
        return None

    if not getattr(memory_config, "workspace_id", None):
        _log(memory_config, conversation_id, source, None, "skipped", "workspace_missing")
        return None

    # 社区版没有 ontology / app_ontology_binding 表，不执行反查，保持既有行为
    ontology_support = _ontology_support_status()
    if ontology_support is None:
        # 探测失败时按“未知”处理：跳过偏好任务，避免用错误的本体写入
        _log(
            memory_config, conversation_id, source,
            None, "skipped", "ontology_probe_failed",
        )
        return None
    if ontology_support is False:
        _log(
            memory_config, conversation_id, source,
            DEFAULT_PREFERENCE_DOMAIN, "enabled", "community_default",
        )
        return DEFAULT_PREFERENCE_DOMAIN

    if source in _APP_SCOPED_SOURCES:
        if not conversation_id:
            _log(
                memory_config, conversation_id, source,
                None, "skipped", "conversation_missing",
            )
            return None

        domain, is_default_fallback, found = resolve_conversation_preference_domain(
            conversation_id
        )
        if found is None:
            _log(
                memory_config, conversation_id, source,
                None, "skipped", "conversation_lookup_error",
            )
            return None
        if not found:
            _log(
                memory_config, conversation_id, source,
                None, "skipped", "conversation_missing",
            )
            return None
        if domain is None:
            reason = (
                "default_ontology_missing"
                if is_default_fallback
                else "custom_ontology_not_configured"
            )
            _log(
                memory_config, conversation_id, source,
                None, "skipped", reason,
            )
            return None

        _log(
            memory_config,
            conversation_id,
            source,
            domain,
            "enabled",
            "default_ontology_fallback" if is_default_fallback else "bound_ontology",
        )
        return domain

    # service_api / mcp 以及无应用上下文的来源：只看记忆开关，默认 coding
    _log(
        memory_config, conversation_id, source,
        DEFAULT_PREFERENCE_DOMAIN, "enabled", "source_default",
    )
    return DEFAULT_PREFERENCE_DOMAIN
