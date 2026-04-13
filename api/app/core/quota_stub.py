"""
配额检查 stub - 社区版使用，所有检查直接放行。
企业版通过 premium.platform_admin.quota_decorator 提供真实实现。
"""
from functools import wraps
from typing import Callable


def _noop_decorator(func: Callable) -> Callable:
    """空装饰器，直接放行"""
    return func


def _noop_check(*args, **kwargs):
    """空检查函数，直接放行"""
    pass


try:
    from premium.platform_admin.quota_decorator import (
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
    )
except ModuleNotFoundError:
    check_workspace_quota = _noop_decorator
    check_skill_quota = _noop_decorator
    check_app_quota = _noop_decorator
    check_knowledge_capacity_quota = _noop_decorator
    check_memory_engine_quota = _noop_decorator
    check_end_user_quota = _noop_decorator
    check_ontology_project_quota = _noop_decorator
    check_model_quota = _noop_decorator
    check_model_activation_quota = _noop_decorator
    get_quota_usage = lambda db, tenant_id: {}
    _check_quota = _noop_check
