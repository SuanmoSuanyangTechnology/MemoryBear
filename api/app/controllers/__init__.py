"""管理端接口 - 基于 JWT 认证

路由前缀: /
认证方式: JWT Token
"""
from fastapi import APIRouter

from . import (
    api_key_controller,
    app_controller,
    auth_controller,
    chunk_controller,
    document_controller,
    emotion_config_controller,
    emotion_controller,
    file_controller,
    file_storage_controller,
    home_page_controller,
    implicit_memory_controller,
    knowledge_controller,
    knowledgeshare_controller,
    memory_agent_controller,
    memory_dashboard_controller,
    memory_episodic_controller,
    memory_explicit_controller,
    memory_forget_controller,
    memory_perceptual_controller,
    memory_reflection_controller,
    memory_short_term_controller,
    memory_storage_controller,
    memory_working_controller,
    model_controller,
    multi_agent_controller,
    prompt_optimizer_controller,
    public_share_controller,
    release_share_controller,
    setup_controller,
    task_controller,
    test_controller,
    tool_controller,
    upload_controller,
    user_controller,
    user_memory_controllers,
    workspace_controller,
    ontology_controller,
    skill_controller
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
manager_router.include_router(user_memory_controllers.router)
manager_router.include_router(memory_episodic_controller.router)
manager_router.include_router(memory_explicit_controller.router)
manager_router.include_router(api_key_controller.router)
manager_router.include_router(release_share_controller.router)
manager_router.include_router(public_share_controller.router)  # 公开路由（无需认证）
manager_router.include_router(memory_dashboard_controller.router)
manager_router.include_router(multi_agent_controller.router)
manager_router.include_router(emotion_controller.router)
manager_router.include_router(emotion_config_controller.router)
manager_router.include_router(prompt_optimizer_controller.router)
manager_router.include_router(memory_reflection_controller.router)
manager_router.include_router(memory_short_term_controller.router)
manager_router.include_router(tool_controller.router)
manager_router.include_router(memory_forget_controller.router)
manager_router.include_router(home_page_controller.router)
manager_router.include_router(implicit_memory_controller.router)
manager_router.include_router(memory_perceptual_controller.router)
manager_router.include_router(memory_working_controller.router)
manager_router.include_router(file_storage_controller.router)
manager_router.include_router(ontology_controller.router)
manager_router.include_router(skill_controller.router)

__all__ = ["manager_router"]
