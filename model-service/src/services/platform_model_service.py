"""平台代管系统模型（SpeedBear）写路径：服务侧全编排（A1，M7-5）。

premium 控制台只传目标系统租户与上游模型 ID；上游拉取、重复/冲突校验、中性行数据
构造（企业语义经 ``enterprise_loader`` 惰性注入）、落库、引用面检查与删除全在此完成
（D8：开源侧只到中性 ``source='platform'`` 行数据，不识别 SpeedBear 协议）。

错误语汇：业务拒绝一律 ``BusinessException``（premium 侧按 code 还原 400/404）；
上游传输失败映射 ``SERVICE_UNAVAILABLE``。读路径（列表/类型选项）仍留 premium 本地。
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..infrastructure.redis_cache import (
    invalidate_runtime_model_info,
    invalidate_runtime_model_info_batch,
    invalidate_workspace_model_options,
)
from ..models.models_model import ModelConfig, ModelProvider, ModelType
from ..models.references.agent_config_model import AgentConfig
from ..models.references.app_model import App
from ..models.references.app_release_model import AppRelease
from ..models.references.multi_agent_config_model import MultiAgentConfig
from ..repositories.model_repository import ModelConfigRepository
from .enterprise_loader import get_platform_semantics
from .model_profile_view import normalize_type, write_columns

logger = logging.getLogger(__name__)


def _upstream_type_resolver(upstream_model_id: Any) -> Callable[[str], str]:
    """上游 ``billing_type`` → 宿主类型归一（交扩展回调，见 enterprise_loader 协议）。

    缺省与存量 ``chat``（含大小写）归 llm；未知类型拒绝导入（值错误 + 引导文案），
    不做静默降级。
    """

    def resolve(value: str) -> str:
        candidate = str(value or "").strip().lower()
        if not candidate:
            return ModelType.LLM.value
        try:
            return ModelType(candidate).value
        except ValueError as exc:
            raise ValueError(
                f"上游模型类型不受支持: model={upstream_model_id} billing_type={candidate!r}，"
                f"仅支持 {', '.join(item.value for item in ModelType)}（chat 归 llm）"
            ) from exc

    return resolve


def _submitted_values(payload: dict[str, Any], field: str) -> list[str] | None:
    """提交态取值：未提交 → None（write_columns 取 fallback_row 现值）；提交 → 字符串列表。

    显式 null / 空列表归一为空列表 → write_columns 抛 BusinessException（400），不逃逸 500。
    """
    if field not in payload:
        return None
    return [str(getattr(item, "value", item)) for item in payload[field] or ()]


def _option_state(model: ModelConfig) -> tuple[uuid.UUID | None, bool]:
    provider = str(getattr(model.provider, "value", model.provider) or "").lower()
    return model.tenant_id, provider == ModelProvider.SPEEDBEAR.value and bool(model.is_public)


def _invalidate_platform_model_options(models: Sequence[ModelConfig]) -> None:
    """公共目录变更后：删运行期模型缓存（按租户精确删）并推工作空间模型选项版本。"""
    if not models:
        return
    ids = [model.id for model in models]
    tenant_ids = sorted(
        {model.tenant_id for model in models if model.tenant_id is not None}, key=str
    )
    for tenant_id in tenant_ids:
        invalidate_runtime_model_info_batch(ids, tenant_id)
    if not tenant_ids:
        invalidate_runtime_model_info_batch(ids)
    invalidate_workspace_model_options(
        tenant_ids,
        public_catalog_changed=any(is_public for _, is_public in map(_option_state, models)),
    )


class PlatformModelService:
    """平台代管模型写路径（创建/更新/启停/删除），全编排在服务侧。"""

    @staticmethod
    def create_system_models(
        db: Session, *, tenant_id: uuid.UUID, model_ids: Sequence[str]
    ) -> list[ModelConfig]:
        """按上游模型 ID 批量创建系统公共模型（任一校验失败整批拒绝，不半途落行）。"""
        ids = [str(model_id) for model_id in model_ids]
        if not ids:
            raise BusinessException("至少需要一个系统模型", BizCode.INVALID_PARAMETER)
        duplicate_model_ids = sorted(
            {model_id for model_id, count in Counter(ids).items() if count > 1}
        )
        if duplicate_model_ids:
            raise BusinessException(
                f"请求中存在重复的模型 ID: {', '.join(duplicate_model_ids)}",
                BizCode.INVALID_PARAMETER,
            )

        upstream_map = {
            str(item.get("id")): item
            for item in PlatformModelService._fetch_upstream_models()
        }
        details: list[dict[str, Any]] = []
        for model_id in ids:
            item = upstream_map.get(model_id)
            if not item:
                raise BusinessException(
                    f"上游模型不存在或未启用: {model_id}", BizCode.INVALID_PARAMETER
                )
            details.append(dict(item))

        # 先全量构造（含类型校验）：任一上游模型类型不可识别即整批拒绝，不半途落行
        payloads = [
            PlatformModelService._build_create_payload(detail, tenant_id=tenant_id)
            for detail in details
        ]
        names = [payload["name"] for payload in payloads]
        duplicate_names = sorted({name for name, count in Counter(names).items() if count > 1})
        if duplicate_names:
            raise BusinessException(
                f"上游模型名称重复，无法创建系统模型: {', '.join(duplicate_names)}",
                BizCode.INVALID_PARAMETER,
            )

        existing = (
            db.query(ModelConfig)
            .filter(
                ModelConfig.provider == ModelProvider.SPEEDBEAR.value,
                ModelConfig.is_public.is_(True),
            )
            .all()
        )
        conflict_names = sorted({model.name for model in existing} & set(names))
        if conflict_names:
            raise BusinessException(
                f"系统模型名称已存在: {', '.join(conflict_names)}", BizCode.INVALID_PARAMETER
            )
        existing_upstream_ids = {
            str(model.config.get("upstream_model_id"))
            for model in existing
            if isinstance(model.config, dict) and model.config.get("upstream_model_id")
        }
        conflict_model_ids = sorted(set(ids) & existing_upstream_ids)
        if conflict_model_ids:
            raise BusinessException(
                f"上游模型已创建为系统模型: {', '.join(conflict_model_ids)}",
                BizCode.INVALID_PARAMETER,
            )

        models = [ModelConfigRepository.create(db, payload) for payload in payloads]
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        for model in models:
            db.refresh(model)
        _invalidate_platform_model_options(models)
        return models

    @staticmethod
    def update_system_model(
        db: Session,
        *,
        tenant_id: uuid.UUID,
        model_id: uuid.UUID,
        update_data: dict[str, Any],
    ) -> ModelConfig:
        """更新系统模型类型/三列（未提交字段保持现值；旧列 capability/is_omni 停写）。"""
        model = PlatformModelService._get_system_model(
            db, tenant_id=tenant_id, model_id=model_id
        )
        if update_data.get("type") is not None:
            model.type = normalize_type(update_data["type"])
        for column, value in write_columns(
            input_modalities=_submitted_values(update_data, "input_modalities"),
            output_modalities=_submitted_values(update_data, "output_modalities"),
            features=_submitted_values(update_data, "features"),
            fallback_row=model,
        ).items():
            setattr(model, column, value)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(model)
        _invalidate_platform_model_options([model])
        return model

    @staticmethod
    def update_system_model_status(
        db: Session, *, tenant_id: uuid.UUID, model_id: uuid.UUID, is_active: bool
    ) -> ModelConfig:
        model = PlatformModelService._get_system_model(
            db, tenant_id=tenant_id, model_id=model_id
        )
        model.is_active = is_active
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(model)
        invalidate_runtime_model_info(model_id)
        _invalidate_platform_model_options([model])
        return model

    @staticmethod
    def delete_system_model(
        db: Session, *, tenant_id: uuid.UUID, model_id: uuid.UUID
    ) -> None:
        """硬删系统公共模型；仍被应用引用时拒绝并给出应用名清单（400）。"""
        model = PlatformModelService._get_system_model(
            db, tenant_id=tenant_id, model_id=model_id
        )
        names = PlatformModelService._referencing_app_names(db, model_id)
        if names:
            raise BusinessException(
                f"模型正在被以下应用使用，无法删除：{', '.join(names)}",
                BizCode.RESOURCE_IN_USE,
            )
        state = _option_state(model)
        db.delete(model)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        invalidate_runtime_model_info(model_id)
        invalidate_workspace_model_options(
            [state[0]], public_catalog_changed=state[1]
        )

    @staticmethod
    def _get_system_model(
        db: Session, *, tenant_id: uuid.UUID, model_id: uuid.UUID
    ) -> ModelConfig:
        model = (
            db.query(ModelConfig)
            .filter(
                ModelConfig.id == model_id,
                ModelConfig.tenant_id == tenant_id,
                ModelConfig.provider == ModelProvider.SPEEDBEAR.value,
                ModelConfig.is_public.is_(True),
            )
            .first()
        )
        if not model:
            raise BusinessException("系统模型不存在", BizCode.MODEL_NOT_FOUND)
        return model

    @staticmethod
    def _referencing_app_names(db: Session, model_id: uuid.UUID) -> list[str]:
        """三处引用面批量查询（每表一条查询，无 N+1），返回去重排序后的应用名。"""
        names: set[str] = set()
        for config_model in (AgentConfig, MultiAgentConfig, AppRelease):
            rows = (
                db.query(App.name)
                .join(config_model, config_model.app_id == App.id)
                .filter(config_model.default_model_config_id == model_id)
                .all()
            )
            names.update(name for (name,) in rows if name)
        return sorted(names)

    @staticmethod
    def _fetch_upstream_models() -> list[dict[str, Any]]:
        semantics = get_platform_semantics()
        try:
            return semantics.fetch_upstream_models()
        except ValueError as exc:
            raise BusinessException(str(exc), BizCode.INVALID_PARAMETER) from exc
        except httpx.HTTPError as exc:
            raise BusinessException(
                f"SpeedBear 网关请求失败: {exc}", BizCode.SERVICE_UNAVAILABLE
            ) from exc

    @staticmethod
    def _build_create_payload(
        detail: dict[str, Any], *, tenant_id: uuid.UUID
    ) -> dict[str, Any]:
        """上游详情 → 落库行数据（中性字段由扩展产出，provider/可见性/三列在此补齐）。"""
        semantics = get_platform_semantics()
        try:
            row = semantics.build_model_row(
                detail,
                tenant_id=tenant_id,
                normalize_type=_upstream_type_resolver(detail.get("id")),
            )
        except ValueError as exc:
            raise BusinessException(str(exc), BizCode.INVALID_PARAMETER) from exc
        payload = {
            **row,
            "provider": ModelProvider.SPEEDBEAR.value,
            "is_public": True,
            "is_active": True,
        }
        payload.update(
            write_columns(
                row_type=row["type"],
                provider=payload["provider"],
                features=row["features"],
            )
        )
        return payload


__all__ = ["PlatformModelService"]
