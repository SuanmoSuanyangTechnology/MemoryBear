"""模型配置加载器 - 用于将预定义模型批量导入到数据库"""

from pathlib import Path
from typing import Callable

import yaml
from sqlalchemy.orm import Session

from app.models.models_model import ModelBase, ModelProvider, ModelConfig


def _load_yaml_config(provider: ModelProvider) -> list[dict]:
    """从YAML文件加载指定供应商的模型配置"""
    config_dir = Path(__file__).parent
    config_file = config_dir / f"{provider.value}_models.yaml"
    
    if not config_file.exists():
        return []
    
    with open(config_file, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
        return data.get('models', [])


def load_models(db: Session, providers: list[str] = None, silent: bool = False) -> dict:
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
                print(f"警告: 供应商 '{provider.value}' 暂无预定义模型")
            continue
            
        if not silent:
            print(f"\n正在加载 {provider.value} 的 {len(models)} 个模型...")

        for model_data in models:
            config_sync_fields = {
                "logo": None,
                "capability": None,
                "is_omni": None,
                "name": None,
                "provider": None,
                "type": None,
                "description": None
            }
            try:
                # 检查模型是否已存在
                existing = db.query(ModelBase).filter(
                    ModelBase.name == model_data["name"],
                    ModelBase.provider == model_data["provider"]
                ).first()
                
                if existing:
                    # 更新现有模型配置
                    for key, value in model_data.items():
                        setattr(existing, key, value)
                    
                    # 更新绑定了该 model_id 的 ModelConfig 和 ModelApiKey
                    sync_fields = [k for k in config_sync_fields.keys() if k in model_data]
                    if sync_fields:
                        # 批量更新 ModelConfig
                        update_kwargs = {k: model_data[k] for k in sync_fields}
                        db.query(ModelConfig).filter(ModelConfig.model_id == existing.id).update(
                            update_kwargs,
                            synchronize_session=False
                        )
                        
                        # 更新 ModelApiKey 的 capability 和 is_omni
                        if 'capability' in model_data or 'is_omni' in model_data:
                            from app.models.models_model import ModelApiKey, model_config_api_key_association
                            api_key_update = {}
                            if 'capability' in model_data:
                                api_key_update['capability'] = model_data['capability']
                            if 'is_omni' in model_data:
                                api_key_update['is_omni'] = model_data['is_omni']
                            
                            if api_key_update:
                                # 查找所有关联的 API Key
                                api_key_ids = db.query(model_config_api_key_association.c.api_key_id).join(
                                    ModelConfig,
                                    ModelConfig.id == model_config_api_key_association.c.model_config_id
                                ).filter(ModelConfig.model_id == existing.id).distinct().all()
                                
                                if api_key_ids:
                                    api_key_ids = [aid[0] for aid in api_key_ids]
                                    db.query(ModelApiKey).filter(ModelApiKey.id.in_(api_key_ids)).update(
                                        api_key_update,
                                        synchronize_session=False
                                    )
                    
                    db.commit()
                    if not silent:
                        print(f"更新成功: {model_data['name']}")
                    result["success"] += 1
                else:
                    # 创建新模型
                    model = ModelBase(**model_data)
                    db.add(model)
                    db.commit()
                    if not silent:
                        print(f"添加成功: {model_data['name']}")
                    result["success"] += 1
                
            except Exception as e:
                db.rollback()
                if not silent:
                    print(f"添加失败: {model_data['name']} - {str(e)}")
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


def get_available_providers() -> list[Callable[[], str]]:
    """获取所有可用的供应商列表（从ModelProvider枚举获取，排除COMPOSITE）"""
    return [p.value for p in ModelProvider if p != ModelProvider.COMPOSITE]


def get_models_by_provider(provider: str) -> list[dict]:
    """获取指定供应商的模型配置列表"""
    provider_enum = ModelProvider(provider) if isinstance(provider, str) else provider
    return _load_yaml_config(provider_enum)
