"""Model reference gate (availability semantics of the host
``ModelConfigService`` / ``ModelApiKeyService``).

Consumed by the publish / rollback activation points and by the multi-agent
publish completeness check. The model tables are host-owned: every read goes
through the cross-domain read exit.
"""

from __future__ import annotations

from typing import NoReturn

from sqlalchemy import Row
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..repositories import cross_domain_read


def assert_refs_publishable(db: Session, config_ids: list) -> None:
    """Publish gate: referenced model configs must be enabled and their base
    model not deprecated.

    Single batched query (no N+1); all violations are aggregated into one
    raise, with deprecated reported before disabled. Missing reference rows
    (hard-deleted / invalid ids) are not blocked here and surface as runtime
    errors instead.
    """
    ids = list(dict.fromkeys(config_ids))
    if not ids:
        return
    rows = cross_domain_read.get_publishable_ref_rows(db, ids)
    deprecated = [row.name for row in rows if row.is_deprecated]
    disabled = [row.name for row in rows if not row.is_active]
    if deprecated:
        raise BusinessException(
            f"以下模型已下线，请更换后再发布：{'、'.join(deprecated)}",
            BizCode.MODEL_DEPRECATED,
        )
    if disabled:
        raise BusinessException(
            f"以下模型已禁用，请更换后再发布：{'、'.join(disabled)}",
            BizCode.INVALID_PARAMETER,
        )


def resolve_invoke_ref(db: Session, model_config_id, tenant_id=None) -> Row | None:
    """Runtime invoke reference (no decryption): only "config exists and is
    active" counts (host ``ModelApiKeyService.resolve_invoke_ref``; callers only
    branch on None / non-None, so this port returns the model config row
    directly)."""

    row = cross_domain_read.get_model_config_row(db, model_config_id, tenant_id=tenant_id)
    if not row or not row.is_active:
        return None
    return row


def raise_model_unavailable(db: Session, model_config_id, tenant_id=None) -> NoReturn:
    """Fill in full error semantics when credentials cannot be resolved: model
    missing, deprecated, disabled, or lacking an API key.
    """

    row = cross_domain_read.get_model_config_row(db, model_config_id, tenant_id=tenant_id)
    if not row:
        raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
    if row.is_deprecated:
        raise BusinessException(
            f"模型 '{row.name}' 已弃用，请在模型配置中更换为其他模型",
            BizCode.MODEL_DEPRECATED,
        )
    _raise_no_credential_error(row)


def _raise_no_credential_error(row: Row) -> NoReturn:
    if not row.is_active:
        raise BusinessException(
            "当前模型未启用，请在模型配置中确认 API Key 和 URL 已配置后启用模型",
            BizCode.MODEL_CONFIG_INVALID,
        )
    raise BusinessException("模型配置缺少 API Key", BizCode.INVALID_PARAMETER)


__all__ = ["assert_refs_publishable", "raise_model_unavailable", "resolve_invoke_ref"]
