"""Service API Controllers - 基于 API Key 认证的服务接口

路由前缀: /v1
认证方式: API Key
"""
from fastapi import APIRouter
from . import app_api_controller, rag_api_knowledge_controller, rag_api_document_controller, rag_api_file_controller, rag_api_chunk_controller, memory_api_controller

# 创建 V1 API 路由器
service_router = APIRouter()

# 注册子路由
service_router.include_router(app_api_controller.router)
service_router.include_router(rag_api_knowledge_controller.router)
service_router.include_router(rag_api_document_controller.router)
service_router.include_router(rag_api_file_controller.router)
service_router.include_router(rag_api_chunk_controller.router)
service_router.include_router(memory_api_controller.router)

__all__ = ["service_router"]
