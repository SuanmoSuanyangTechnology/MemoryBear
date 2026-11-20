"""将平台预置的供应商目录同步到模型广场。"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

import yaml
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..infrastructure.model_provider_config import require_provider_supports_model_type
from ..models.models_model import ModelBase, ModelConfig, ModelProvider
from .model_service import _invalidate_model_base_caches

logger = logging.getLogger(__name__)

_YAML_DIR = Path(__file__).parent.parent / "infrastructure" / "loader_yaml"
_CONFIG_SYNC_FIELDS = (
    "logo",
    "input_modalities",
    "output_modalities",
    "features",
    "name",
    "provider",
    "type",
    "description",
)


def _load_yaml_config(provider: ModelProvider) -> list[dict]:
    """从YAML文件加载指定供应商的模型配置"""
    config_file = _YAML_DIR / f"{provider.value}_models.yaml"

    if not config_file.exists():
        return []

    with open(config_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or data.get("provider") != provider.value:
        raise ValueError(f"{config_file.name} 的供应商不匹配")
    models = data.get("models")
    if not isinstance(models, list):
        raise ValueError(f"{config_file.name} 缺少模型列表")

    names = set()
    for model in models:
        if not isinstance(model, dict) or model.get("provider") != provider.value:
            raise ValueError(f"{config_file.name} 存在供应商不匹配的模型")
        name = model.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(f"{config_file.name} 存在无效或重复的模型名称: {name!r}")
        names.add(name)
    return models


def _sync_model(db: Session, data: dict) -> tuple[bool, UUID | None]:
    require_provider_supports_model_type(data["provider"], data.get("type"))
    # 唯一键防止多个 model-service 副本启动时重复插入同一模型。
    inserted_id = db.scalar(
        insert(ModelBase)
        .values(**data)
        .on_conflict_do_nothing(index_elements=[ModelBase.name, ModelBase.provider])
        .returning(ModelBase.id)
    )
    if inserted_id is not None:
        return True, None

    existing = db.scalars(
        select(ModelBase)
        .where(ModelBase.name == data["name"], ModelBase.provider == data["provider"])
        .with_for_update()
    ).one()
    if existing.is_official is not True:
        return False, None

    changed_fields = {key: value for key, value in data.items() if getattr(existing, key) != value}
    for key, value in changed_fields.items():
        setattr(existing, key, value)

    config_values = {key: data[key] for key in _CONFIG_SYNC_FIELDS if key in data}
    differences = [
        getattr(ModelConfig, key).is_distinct_from(value) for key, value in config_values.items()
    ]
    updated_configs = (
        db.query(ModelConfig)
        .filter(ModelConfig.model_id == existing.id, or_(*differences))
        .update(config_values, synchronize_session=False)
    )
    changed = bool(changed_fields or updated_configs)
    return changed, existing.id if changed else None


def load_models(db: Session, providers: list[str] | None = None, silent: bool = False) -> dict:
    """
    加载模型配置到数据库。通过 is_deprecated 显式弃用，保留广场条目和已有绑定。

    Args:
        db: 数据库会话
        providers: 要加载的供应商列表，None表示加载所有
        silent: 是否静默模式（不输出详细日志）

    Returns:
        dict: 模型写入结果计数，以及缓存刷新是否失败。
    """
    result = {"success": 0, "skipped": 0, "failed": 0, "cache_refresh_failed": False}
    changed_base_ids: list[UUID] = []

    # 确定要加载的供应商
    if providers:
        target_providers = [ModelProvider(p) if isinstance(p, str) else p for p in providers]
    else:
        target_providers = [p for p in ModelProvider if p != ModelProvider.COMPOSITE]

    for provider in target_providers:
        try:
            models = _load_yaml_config(provider)
        except (OSError, ValueError, yaml.YAMLError):
            logger.exception("读取供应商 %s 的预置目录失败", provider.value)
            result["failed"] += 1
            continue

        if not models:
            if not silent:
                logger.warning("警告: 供应商 '%s' 暂无预定义模型", provider.value)
            continue

        if not silent:
            logger.info("正在加载 %s 的 %s 个模型...", provider.value, len(models))

        for model_data in models:
            try:
                changed, base_id = _sync_model(db, model_data)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("同步模型失败: %s/%s", provider.value, model_data["name"])
                result["failed"] += 1
                continue

            result["success" if changed else "skipped"] += 1
            if base_id is not None:
                changed_base_ids.append(base_id)
            if changed and not silent:
                logger.info("同步成功: %s/%s", provider.value, model_data["name"])

    if changed_base_ids:
        try:
            configs = db.scalars(
                select(ModelConfig).where(ModelConfig.model_id.in_(changed_base_ids))
            ).all()
            result["cache_refresh_failed"] = not _invalidate_model_base_caches(configs)
        except Exception:
            result["cache_refresh_failed"] = True
            logger.exception("预置模型已同步，关联模型的缓存刷新失败")

    return result


def load_models_by_provider(db: Session, provider: str) -> dict:
    """
    加载指定供应商的模型配置

    Args:
        db: 数据库会话
        provider: 供应商名称（字符串或ModelProvider枚举）

    Returns:
        dict: 加载结果统计
    """
    provider_enum = ModelProvider(provider) if isinstance(provider, str) else provider
    return load_models(db, providers=[provider_enum])


def get_available_providers() -> list[str]:
    """获取所有可用的供应商列表（从ModelProvider枚举获取，排除COMPOSITE）"""
    return [p.value for p in ModelProvider if p != ModelProvider.COMPOSITE]


def get_models_by_provider(provider: str) -> list[dict]:
    """获取指定供应商的模型配置列表"""
    provider_enum = ModelProvider(provider) if isinstance(provider, str) else provider
    return _load_yaml_config(provider_enum)
