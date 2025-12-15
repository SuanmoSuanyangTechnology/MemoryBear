from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
import uuid
import math
import time
import asyncio

from app.models.models_model import ModelConfig, ModelApiKey, ModelType
from app.repositories.model_repository import ModelConfigRepository, ModelApiKeyRepository
from app.schemas import model_schema
from app.schemas.model_schema import (
    ModelConfigCreate, ModelConfigUpdate, ModelApiKeyCreate, ModelApiKeyUpdate,
    ModelConfigQuery, ModelStats
)
from app.core.logging_config import get_business_logger
from app.schemas.response_schema import PageData, PageMeta
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode

logger = get_business_logger()


class ModelConfigService:
    """模型配置服务"""

    @staticmethod
    def get_model_by_id(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> ModelConfig:
        """根据ID获取模型配置"""
        model = ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id)
        if not model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        return model

    @staticmethod
    def get_model_list(db: Session, query: ModelConfigQuery, tenant_id: uuid.UUID | None = None) -> PageData:
        """获取模型配置列表"""
        models, total = ModelConfigRepository.get_list(db, query, tenant_id=tenant_id)
        pages = math.ceil(total / query.pagesize) if total > 0 else 0
        
        return PageData(
            page=PageMeta(
                page=query.page,
                pagesize=query.pagesize,
                total=total,
                hasnext=query.page < pages
            ),
            items=[model_schema.ModelConfig.model_validate(model) for model in models]
        )

    @staticmethod
    def get_model_by_name(db: Session, name: str, tenant_id: uuid.UUID | None = None) -> ModelConfig:
        """根据名称获取模型配置"""
        model = ModelConfigRepository.get_by_name(db, name, tenant_id=tenant_id)
        if not model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        return model

    @staticmethod
    def search_models_by_name(db: Session, name: str, tenant_id: uuid.UUID | None = None, limit: int = 10) -> List[ModelConfig]:
        """按名称模糊匹配获取模型配置列表"""
        return ModelConfigRepository.search_by_name(db, name, tenant_id=tenant_id, limit=limit)

    @staticmethod
    async def validate_model_config(
        db: Session,
        *,
        model_name: str,
        provider: str,
        api_key: str,
        api_base: Optional[str] = None,
        model_type: str = "llm",
        test_message: str = "Hello"
    ) -> Dict[str, Any]:
        """验证模型配置是否有效
        
        Args:
            db: 数据库会话
            model_name: 模型名称
            provider: 提供商
            api_key: API密钥
            api_base: API基础URL
            model_type: 模型类型 (llm/chat/embedding/rerank)
            test_message: 测试消息
            
        Returns:
            Dict: 验证结果
        """
        from app.core.models import RedBearLLM, RedBearRerank
        from app.core.models.base import RedBearModelConfig
        from app.core.models.embedding import RedBearEmbeddings
        import traceback
        
        try:
            start_time = time.time()
            
            model_config = RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base,
                temperature=0.7,
                max_tokens=100
            )
            
            # 根据模型类型选择不同的验证方式
            model_type_lower = model_type.lower()
            
            if model_type_lower in ["llm", "chat"]:
                # LLM/Chat 模型验证 - 统一使用字符串输入
                llm = RedBearLLM(model_config, type=ModelType.LLM if model_type_lower == "llm" else ModelType.CHAT)
                response = await llm.ainvoke(test_message)
                elapsed_time = time.time() - start_time
                
                content = response.content if hasattr(response, 'content') else str(response)
                usage = None
                if hasattr(response, 'usage_metadata'):
                    usage = {
                        "input_tokens": getattr(response.usage_metadata, 'input_tokens', 0),
                        "output_tokens": getattr(response.usage_metadata, 'output_tokens', 0),
                        "total_tokens": getattr(response.usage_metadata, 'total_tokens', 0)
                    }
                
                return {
                    "valid": True,
                    "message": f"{model_type.upper()} 模型配置验证成功",
                    "response": content,
                    "elapsed_time": elapsed_time,
                    "usage": usage,
                    "error": None
                }
                
            elif model_type_lower == "embedding":
                # Embedding 模型验证（在线程中运行同步方法）
                embedding = RedBearEmbeddings(model_config)
                test_texts = [test_message, "测试文本"]
                vectors = await asyncio.to_thread(embedding.embed_documents, test_texts)
                elapsed_time = time.time() - start_time
                
                return {
                    "valid": True,
                    "message": "Embedding 模型配置验证成功",
                    "response": f"成功生成 {len(vectors)} 个向量，维度: {len(vectors[0]) if vectors else 0}",
                    "elapsed_time": elapsed_time,
                    "usage": {
                        "input_tokens": len(test_message),
                        "vector_count": len(vectors),
                        "vector_dimension": len(vectors[0]) if vectors else 0
                    },
                    "error": None
                }
                
            elif model_type_lower == "rerank":
                # Rerank 模型验证（在线程中运行同步方法）
                rerank = RedBearRerank(model_config)
                query = test_message
                documents = ["这是第一个文档", "这是第二个文档", "这是第三个文档"]
                results = await asyncio.to_thread(rerank.rerank, query=query, documents=documents, top_n=3)
                elapsed_time = time.time() - start_time
                
                return {
                    "valid": True,
                    "message": "Rerank 模型配置验证成功",
                    "response": f"成功对 {len(documents)} 个文档进行重排序，返回 top {len(results) if results else 0} 结果",
                    "elapsed_time": elapsed_time,
                    "usage": {
                        "query_length": len(query),
                        "document_count": len(documents),
                        "result_count": len(results) if results else 0
                    },
                    "error": None
                }
                
            else:
                return {
                    "valid": False,
                    "message": "不支持的模型类型",
                    "response": None,
                    "elapsed_time": None,
                    "usage": None,
                    "error": f"不支持的模型类型: {model_type}"
                }
                
        except Exception as e:
            # 提取详细的错误信息
            error_message = str(e)
            error_type = type(e).__name__
            print("=========error_message:",error_message.lower())
            # 特殊处理常见的错误类型
            if "unsupported countries" in error_message.lower() or "unsupported region" in error_message.lower():
                # 区域/国家限制（适用于所有提供商）
                error_message = "区域限制: 该模型在当前区域或国家/地区不可用，请检查提供商的服务区域限制"
            elif "ValidationException" in error_type or "ValidationException" in error_message:
                # 其他验证错误
                if "access denied" in error_message.lower():
                    error_message = "访问被拒绝: 请检查 API 凭证和权限配置"
                else:
                    error_message = f"验证失败: {error_message}"
            elif "AuthenticationError" in error_type or "authentication" in error_message.lower():
                error_message = "认证失败: API Key 无效或已过期"
            elif "RateLimitError" in error_type or "rate limit" in error_message.lower():
                error_message = "请求频率限制: 已超过 API 调用限制"
            elif "InvalidRequestError" in error_type or "invalid request" in error_message.lower():
                error_message = f"无效请求: {error_message}"
            elif "model_copy" in error_message:
                error_message = "模型消息格式错误: 请确保使用正确的模型类型（LLM/Chat）"
            
            # 记录详细错误日志
            logger.error(f"模型验证失败 - 类型: {error_type}, 模型: {model_name}, 提供商: {provider}")
            logger.error(f"错误详情: {error_message}")
            logger.debug(f"完整堆栈: {traceback.format_exc()}")
            
            return {
                "valid": False,
                "message": f"{model_type.upper()} 模型配置验证失败",
                "response": None,
                "elapsed_time": None,
                "usage": None,
                "error": error_message,
                "error_type": error_type
            }

    @staticmethod
    async def create_model(db: Session, model_data: ModelConfigCreate, tenant_id: uuid.UUID) -> ModelConfig:
        """创建模型配置"""
        # 检查名称是否已存在（同租户内）
        if ModelConfigRepository.get_by_name(db, model_data.name, tenant_id=tenant_id):
            raise BusinessException("模型名称已存在", BizCode.DUPLICATE_NAME)

        # 验证配置
        if not model_data.skip_validation and model_data.api_keys:
            api_key_data = model_data.api_keys
            validation_result = await ModelConfigService.validate_model_config(
                db=db,
                model_name=api_key_data.model_name,
                provider=api_key_data.provider,
                api_key=api_key_data.api_key,
                api_base=api_key_data.api_base,
                model_type=model_data.type,  # 传递模型类型
                test_message="Hello"
            )
            if not validation_result["valid"]:
                raise BusinessException(
                    f"模型配置验证失败: {validation_result['error']}",
                    BizCode.INVALID_PARAMETER
                )

        # 事务处理
        api_key_data = model_data.api_keys
        model_config_data = model_data.dict(exclude={"api_keys", "skip_validation"})
        # 添加租户ID
        model_config_data["tenant_id"] = tenant_id
        
        model = ModelConfigRepository.create(db, model_config_data)
        db.flush()  # 获取生成的 ID

        if api_key_data:
            api_key_create_schema = ModelApiKeyCreate(
                model_config_id=model.id,
                **api_key_data.dict()
            )
            ModelApiKeyRepository.create(db, api_key_create_schema)
        
        db.commit()
        db.refresh(model)
        return model

    @staticmethod
    def update_model(db: Session, model_id: uuid.UUID, model_data: ModelConfigUpdate, tenant_id: uuid.UUID | None = None) -> ModelConfig:
        """更新模型配置"""
        existing_model = ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id)
        if not existing_model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        
        if model_data.name and model_data.name != existing_model.name:
            if ModelConfigRepository.get_by_name(db, model_data.name, tenant_id=tenant_id):
                raise BusinessException("模型名称已存在", BizCode.DUPLICATE_NAME)
        
        model = ModelConfigRepository.update(db, model_id, model_data, tenant_id=tenant_id)
        db.commit()
        db.refresh(model)
        return model

    @staticmethod
    def delete_model(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> bool:
        """删除模型配置"""
        if not ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id):
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        
        success = ModelConfigRepository.delete(db, model_id, tenant_id=tenant_id)
        db.commit()
        return success

    @staticmethod
    def get_model_stats(db: Session) -> ModelStats:
        """获取模型统计信息"""
        stats_data = ModelConfigRepository.get_stats(db)
        return ModelStats(
            total_models=stats_data["total_models"],
            active_models=stats_data["active_models"],
            llm_count=stats_data["llm_count"],
            embedding_count=stats_data["embedding_count"],
            rerank_count=stats_data["rerank_count"],
            provider_stats=stats_data["provider_stats"]
        )


class ModelApiKeyService:
    """模型API Key服务"""

    @staticmethod
    def get_api_key_by_id(db: Session, api_key_id: uuid.UUID) -> ModelApiKey:
        """根据ID获取API Key"""
        api_key = ModelApiKeyRepository.get_by_id(db, api_key_id)
        if not api_key:
            raise BusinessException("API Key不存在", BizCode.NOT_FOUND)
        return api_key

    @staticmethod
    def get_api_keys_by_model(db: Session, model_config_id: uuid.UUID, is_active: bool = True) -> List[ModelApiKey]:
        """根据模型配置ID获取API Key列表"""
        if not ModelConfigRepository.get_by_id(db, model_config_id):
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        
        return ModelApiKeyRepository.get_by_model_config(db, model_config_id, is_active)

    @staticmethod 
    async def create_api_key(db: Session, api_key_data: ModelApiKeyCreate) -> ModelApiKey:
        """创建API Key"""
        model_config = ModelConfigRepository.get_by_id(db, api_key_data.model_config_id)
        if not model_config:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        
        validation_result = await ModelConfigService.validate_model_config(
                db=db,
                model_name=api_key_data.model_name,
                provider=api_key_data.provider,
                api_key=api_key_data.api_key,
                api_base=api_key_data.api_base,
                model_type=model_config.type,  # 传递模型类型
                test_message="Hello"
            )
        print(validation_result)
        if not validation_result["valid"]:
                raise BusinessException(
                    f"模型配置验证失败: {validation_result['error']}",
                    BizCode.INVALID_PARAMETER
                )
                
        api_key = ModelApiKeyRepository.create(db, api_key_data)
        db.commit()
        db.refresh(api_key)
        return api_key

    @staticmethod
    async def update_api_key(db: Session, api_key_id: uuid.UUID, api_key_data: ModelApiKeyUpdate) -> ModelApiKey:
        """更新API Key"""
        existing_api_key = ModelApiKeyRepository.get_by_id(db, api_key_id)
        if not existing_api_key:
            raise BusinessException("API Key不存在", BizCode.NOT_FOUND)
        
        # 获取关联的模型配置以获取模型类型
        model_config = ModelConfigRepository.get_by_id(db, existing_api_key.model_config_id)
        if not model_config:
            raise BusinessException("关联的模型配置不存在", BizCode.MODEL_NOT_FOUND)
        
        validation_result = await ModelConfigService.validate_model_config(
                db=db,
                model_name=api_key_data.model_name,
                provider=api_key_data.provider,
                api_key=api_key_data.api_key,
                api_base=api_key_data.api_base,
                model_type=model_config.type,  # 传递模型类型
                test_message="Hello"
            )
        print(validation_result)
        if not validation_result["valid"]:
                raise BusinessException(
                    f"模型配置验证失败: {validation_result['error']}",
                    BizCode.INVALID_PARAMETER
                )
                
        api_key = ModelApiKeyRepository.update(db, api_key_id, api_key_data)
        db.commit()
        db.refresh(api_key)
        return api_key

    @staticmethod
    def delete_api_key(db: Session, api_key_id: uuid.UUID) -> bool:
        """删除API Key"""
        if not ModelApiKeyRepository.get_by_id(db, api_key_id):
            raise BusinessException("API Key不存在", BizCode.NOT_FOUND)
        
        success = ModelApiKeyRepository.delete(db, api_key_id)
        db.commit()
        return success

    @staticmethod
    def get_available_api_key(db: Session, model_config_id: uuid.UUID) -> Optional[ModelApiKey]:
        """获取可用的API Key（按优先级和负载均衡）"""
        api_keys = ModelApiKeyRepository.get_by_model_config(db, model_config_id, is_active=True)
        if not api_keys:
            return None
        return min(api_keys, key=lambda x: int(x.usage_count or "0"))

    @staticmethod
    def record_api_key_usage(db: Session, api_key_id: uuid.UUID) -> bool:
        """记录API Key使用"""
        success = ModelApiKeyRepository.update_usage(db, api_key_id)
        if success:
            db.commit()
        return success
