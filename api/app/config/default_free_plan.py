"""
社区版默认免费套餐配置
当无法从 SaaS 版获取 premium 模块时，使用此配置作为兜底

可通过环境变量覆盖配额配置，格式：QUOTA_<QUOTA_NAME>
例如：QUOTA_END_USER_QUOTA=100
"""

import os


def _get_quota_from_env():
    """从环境变量获取配额配置"""
    quota_keys = [
        "workspace_quota",
        "skill_quota",
        "app_quota",
        "knowledge_capacity_quota",
        "memory_engine_quota",
        "end_user_quota",
        "ontology_project_quota",
        "model_quota",
        "api_ops_rate_limit",
    ]
    quotas = {}
    for key in quota_keys:
        env_key = f"QUOTA_{key.upper()}"
        env_value = os.getenv(env_key)
        if env_value is not None:
            try:
                quotas[key] = float(env_value) if '.' in env_value else int(env_value)
            except ValueError:
                pass
    return quotas


def _build_default_free_plan():
    """构建默认免费套餐配置"""
    base = {
        "name": "记忆体验版",
        "name_en": "Memory Experience",
        "category": "saas_personal",
        "tier_level": 0,
        "version": "1.0",
        "status": True,
        "price": 0,
        "billing_cycle": "permanent_free",
        "core_value": "感受永久记忆",
        "core_value_en": "Experience Permanent Memory",
        "tech_support": "社群交流",
        "tech_support_en": "Community Support",
        "sla_compliance": "无",
        "sla_compliance_en": "None",
        "page_customization": "无",
        "page_customization_en": "None",
        "theme_color": "#64748B",
        "quotas": {
            "workspace_quota": 1,
            "skill_quota": 5,
            "app_quota": 2,
            "knowledge_capacity_quota": 0.3,
            "memory_engine_quota": 1,
            "end_user_quota": 10,
            "ontology_project_quota": 3,
            "model_quota": 1,
            "api_ops_rate_limit": 50,
        },
    }

    env_quotas = _get_quota_from_env()
    if env_quotas:
        base["quotas"].update(env_quotas)

    return base


DEFAULT_FREE_PLAN = _build_default_free_plan()
