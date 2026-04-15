"""
社区版默认免费套餐配置
当无法从 SaaS 版获取 premium 模块时，使用此配置作为兜底
"""

DEFAULT_FREE_PLAN = {
    "name": "记忆体验版",
    "category": "saas_personal",
    "tier_level": 0,
    "version": "1.0",
    "status": True,
    "price": 0,
    "billing_cycle": "permanent_free",
    "core_value": "感受永久记忆",
    "tech_support": "社群交流",
    "sla_compliance": "无",
    "page_customization": "无",
    "theme_color": "#64748B",
    "quotas": {
        "workspace_quota": 1,
        "skill_quota": 5,
        "app_quota": 2,
        "knowledge_capacity_quota": 0.3,
        "memory_engine_quota": 1,
        "end_user_quota": 1,
        "ontology_project_quota": 3,
        "model_quota": 1,
        "api_ops_rate_limit": 50,
    },
}
