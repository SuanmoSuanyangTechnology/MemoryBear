"""管理端接口 - 基于 JWT 认证

路由前缀: /
认证方式: JWT Token
"""
from fastapi import APIRouter
from . import (
    model_controller,
    task_controller,
    test_controller,
    user_controller,
    auth_controller,
    workspace_controller,
    setup_controller,
    file_controller,
    document_controller,
    knowledge_controller,
    chunk_controller,
    knowledgeshare_controller,
    app_controller,
    upload_controller,
    memory_agent_controller,
    memory_dashboard_controller,
    memory_storage_controller,
    memory_dashboard_controller,
    memory_reflection_controller,
    api_key_controller,
    release_share_controller,
    public_share_controller,
    multi_agent_controller,
    workflow_controller,
    prompt_optimizer_controller,
    tool_controller,
    tool_execution_controller,
)

# 创建管理端 API 路由器
manager_router = APIRouter()

# 注册所有管理端路由
manager_router.include_router(task_controller.router)
manager_router.include_router(user_controller.router)
manager_router.include_router(auth_controller.router)
manager_router.include_router(workspace_controller.router)
manager_router.include_router(workspace_controller.public_router)  # 公开路由（无需认证）
manager_router.include_router(setup_controller.router)
manager_router.include_router(model_controller.router)
manager_router.include_router(file_controller.router)
manager_router.include_router(document_controller.router)
manager_router.include_router(knowledge_controller.router)
manager_router.include_router(chunk_controller.router)
manager_router.include_router(test_controller.router)
manager_router.include_router(knowledgeshare_controller.router)
manager_router.include_router(app_controller.router)
manager_router.include_router(upload_controller.router)
manager_router.include_router(memory_agent_controller.router)
manager_router.include_router(memory_dashboard_controller.router)
manager_router.include_router(memory_storage_controller.router)
manager_router.include_router(api_key_controller.router)
manager_router.include_router(release_share_controller.router)
manager_router.include_router(public_share_controller.router)  # 公开路由（无需认证）
manager_router.include_router(memory_dashboard_controller.router)
manager_router.include_router(multi_agent_controller.router)
manager_router.include_router(workflow_controller.router)
manager_router.include_router(prompt_optimizer_controller.router)
manager_router.include_router(memory_reflection_controller.router)
manager_router.include_router(tool_controller.router)
manager_router.include_router(tool_execution_controller.router)

__all__ = ["manager_router"]
