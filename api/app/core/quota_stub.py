"""
配额检查 stub - 社区版和 SaaS 版统一使用 core.quota_manager 实现

所有配额检查逻辑统一在 core 层实现，两个版本共用：
- 社区版：从 default_free_plan.py 读取配额限制
- SaaS 版：优先从 tenant_subscriptions 表读取，降级到配置文件
"""
from app.core.quota_manager import (
    check_workspace_quota,
    check_skill_quota,
    check_app_quota,
    check_knowledge_capacity_quota,
    check_memory_engine_quota,
    check_end_user_quota,
    check_ontology_project_quota,
    check_model_quota,
    check_model_activation_quota,
    get_quota_usage,
    _check_quota,
    QuotaUsageRepository,
)

__all__ = [
    "check_workspace_quota",
    "check_skill_quota",
    "check_app_quota",
    "check_knowledge_capacity_quota",
    "check_memory_engine_quota",
    "check_end_user_quota",
    "check_ontology_project_quota",
    "check_model_quota",
    "check_model_activation_quota",
    "get_quota_usage",
    "_check_quota",
    "QuotaUsageRepository",
]
