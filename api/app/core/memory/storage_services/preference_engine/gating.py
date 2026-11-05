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
from typing import Any, Optional, Tuple

from sqlalchemy import text

from app.core.config import settings
from app.db import get_db_context

logger = logging.getLogger(__name__)

DEFAULT_PREFERENCE_DOMAIN = "coding"
_APP_SCOPED_SOURCES = {"agent", "workflow"}


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
) -> Tuple[Optional[str], bool, bool]:
    """按会话反查应用当前生效本体的偏好域。

    Returns:
        (domain, is_default_fallback, conversation_found)
        - 会话不存在： (None, False, False)
        - 无绑定 / 悬空绑定： (默认本体 domain, True, True)
        - 绑定自定义本体： (该本体 domain 或 None, False, True)
    """
    with get_db_context() as db:
        row = db.execute(
            text(
                """
                SELECT CASE
                         WHEN b.binding_id IS NULL OR o.ontology_id IS NULL
                           THEN (SELECT d.preference_domain
                                 FROM ontology d
                                 WHERE d.tenant_id IS NULL AND d.is_system_default IS TRUE
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
    if settings.DEPLOYMENT_MODE == "community":
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
        if not found:
            _log(
                memory_config, conversation_id, source,
                None, "skipped", "conversation_missing",
            )
            return None
        if domain is None:
            _log(
                memory_config, conversation_id, source,
                None, "skipped", "custom_ontology_not_configured",
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
