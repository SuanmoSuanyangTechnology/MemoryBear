"""模型配置加载器 — 将预定义模型（YAML 种子）批量同步到数据库。

机械移植自宿主 `app/core/models/scripts/loader.py`（M7 D-M7-5：服务=唯一写者），
差异仅两处：
1. YAML 目录随迁至 ``src/infrastructure/loader_yaml/``；
2. ``print`` 改 ``logging``（服务侧统一日志口径，宿主无 logger 基建）。

同步语义：已存在（同 name+provider）→ 字段覆盖并回写绑定该 base 的 ModelConfig
（能力以 config 为源）；不存在 → 新建。逐条独立 commit，单条失败不中断整批。
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from ..models.models_model import ModelBase, ModelConfig, ModelProvider

logger = logging.getLogger(__name__)

_YAML_DIR = Path(__file__).parent.parent / "infrastructure" / "loader_yaml"


def _load_yaml_config(provider: ModelProvider) -> list[dict]:
    """从YAML文件加载指定供应商的模型配置"""
    config_file = _YAML_DIR / f"{provider.value}_models.yaml"

    if not config_file.exists():
        return []

    with open(config_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data.get("models", [])


def load_models(db: Session, providers: list[str] | None = None, silent: bool = False) -> dict:
    """
    加载模型配置到数据库

    Args:
        db: 数据库会话
        providers: 要加载的供应商列表，None表示加载所有
        silent: 是否静默模式（不输出详细日志）

    Returns:
        dict: 加载结果统计 {"success": int, "skipped": int, "failed": int}
    """
    result = {"success": 0, "skipped": 0, "failed": 0}

    # 确定要加载的供应商
    if providers:
        target_providers = [ModelProvider(p) if isinstance(p, str) else p for p in providers]
    else:
        target_providers = [p for p in ModelProvider if p != ModelProvider.COMPOSITE]

    for provider in target_providers:
        # 从YAML文件加载模型配置
        models = _load_yaml_config(provider)

        if not models:
            if not silent:
                logger.warning("警告: 供应商 '%s' 暂无预定义模型", provider.value)
            continue

        if not silent:
            logger.info("正在加载 %s 的 %s 个模型...", provider.value, len(models))

        for model_data in models:
            config_sync_fields = {
                "logo": None,
                "input_modalities": None,
                "output_modalities": None,
                "features": None,
                "name": None,
                "provider": None,
                "type": None,
                "description": None,
            }
            try:
                # 检查模型是否已存在
                existing = db.query(ModelBase).filter(
                    ModelBase.name == model_data["name"],
                    ModelBase.provider == model_data["provider"],
                ).first()

                if existing:
                    # 更新现有模型配置
                    for key, value in model_data.items():
                        setattr(existing, key, value)

                    # 更新绑定该 model_id 的 ModelConfig
                    # （能力以 config 为源，渠道运行期取 config 快照）
                    sync_fields = [k for k in config_sync_fields if k in model_data]
                    if sync_fields:
                        # 批量更新 ModelConfig
                        update_kwargs = {k: model_data[k] for k in sync_fields}
                        db.query(ModelConfig).filter(ModelConfig.model_id == existing.id).update(
                            update_kwargs,
                            synchronize_session=False,
                        )

                    db.commit()
                    if not silent:
                        logger.info("更新成功: %s", model_data["name"])
                    result["success"] += 1
                else:
                    # 创建新模型
                    model = ModelBase(**model_data)
                    db.add(model)
                    db.commit()
                    if not silent:
                        logger.info("添加成功: %s", model_data["name"])
                    result["success"] += 1

            except Exception as e:
                db.rollback()
                if not silent:
                    logger.warning("添加失败: %s - %s", model_data["name"], str(e))
                result["failed"] += 1

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
