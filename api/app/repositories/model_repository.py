from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import and_, or_, func, desc, select
from typing import List, Optional, Dict, Any, Tuple
import uuid

from app.models.models_model import ModelConfig, ModelApiKey, ModelType, ModelBase, model_config_api_key_association
from app.schemas.model_schema import (
    ModelConfigUpdate, ModelApiKeyCreate, ModelApiKeyUpdate,
    ModelConfigQuery, ModelConfigQueryNew
)
from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()


class ModelConfigRepository:
    """模型配置Repository"""

    @staticmethod
    def get_by_id(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> Optional[ModelConfig]:
        """根据ID获取模型配置"""
        db_logger.debug(f"根据ID查询模型配置: model_id={model_id}, tenant_id={tenant_id}")
        
        try:
            query = db.query(ModelConfig).options(
                joinedload(ModelConfig.api_keys)
            ).filter(ModelConfig.id == model_id)
            
            # 添加租户过滤
            if tenant_id:
                query = query.filter(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    )
                )
            
            model = query.first()
            
            if model:
                db_logger.debug(f"模型配置查询成功: {model.name} (ID: {model_id})")
            else:
                db_logger.debug(f"模型配置不存在: model_id={model_id}")
            return model
        except Exception as e:
            db_logger.error(f"根据ID查询模型配置失败: model_id={model_id} - {str(e)}")
            raise

    @staticmethod
    def get_by_name(db: Session, name: str, tenant_id: uuid.UUID | None = None) -> Optional[ModelConfig]:
        """根据名称获取模型配置"""
        db_logger.debug(f"根据名称查询模型配置: name={name}, tenant_id={tenant_id}")
        
        try:
            query = db.query(ModelConfig).filter(ModelConfig.name == name)
            
            # 添加租户过滤
            if tenant_id:
                query = query.filter(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    )
                )
            
            model = query.first()
            if model:
                db_logger.debug(f"模型配置查询成功: {model.name}")
            return model
        except Exception as e:
            db_logger.error(f"根据名称查询模型配置失败: name={name} - {str(e)}")
            raise

    @staticmethod
    def search_by_name(db: Session, name: str, tenant_id: uuid.UUID | None = None, limit: int = 10) -> List[ModelConfig]:
        """按名称模糊匹配获取模型配置列表
        
        Args:
            name: 模型名称关键词（模糊匹配）
            tenant_id: 租户ID
            limit: 返回数量上限
        Returns:
            模型配置列表
        """
        db_logger.debug(f"按名称模糊查询模型配置: name~{name}, tenant_id={tenant_id}, limit={limit}")
        try:
            query = db.query(ModelConfig).filter(ModelConfig.name.ilike(f"%{name}%"))
            
            # 添加租户过滤
            if tenant_id:
                query = query.filter(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    )
                )
            
            models = query.order_by(ModelConfig.name).limit(limit).all()
            db_logger.debug(f"模糊查询成功: 返回数量={len(models)}")
            return models
        except Exception as e:
            db_logger.error(f"按名称模糊查询模型配置失败: name~{name} - {str(e)}")
            raise

    @staticmethod
    def get_list(db: Session, query: ModelConfigQuery, tenant_id: uuid.UUID | None = None) -> Tuple[List[ModelConfig], int]:
        """获取模型配置列表"""
        db_logger.debug(f"查询模型配置列表: {query.dict()}, tenant_id={tenant_id}")

        try:
            # 构建查询条件
            filters = []

            # 添加租户过滤（查询本租户的模型或公开模型）
            if tenant_id:
                filters.append(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    )
                )

            # 支持多个 type 值（使用 IN 查询）
            # 兼容 chat 和 llm 类型：如果查询包含其中一个，则同时匹配两者
            if query.type:
                type_values = list(query.type)
                # 如果包含 chat 或 llm，则同时包含两者
                if ModelType.CHAT in type_values or ModelType.LLM in type_values:
                    if ModelType.CHAT not in type_values:
                        type_values.append(ModelType.CHAT)
                    if ModelType.LLM not in type_values:
                        type_values.append(ModelType.LLM)
                filters.append(ModelConfig.type.in_(type_values))

            if query.is_active is not None:
                filters.append(ModelConfig.is_active == query.is_active)

            if query.is_public is not None:
                filters.append(ModelConfig.is_public == query.is_public)

            if query.search:
                # 搜索逻辑需要join ModelApiKey表来搜索model_name
                search_filter = or_(
                    ModelConfig.name.ilike(f"%{query.search}%"),
                    # ModelConfig.description.ilike(f"%{query.search}%")
                )
                filters.append(search_filter)

            # 构建基础查询
            base_query = db.query(ModelConfig).options(
                joinedload(ModelConfig.api_keys)
            )

            # 如果需要按provider筛选，需要join ModelApiKey表
            if query.provider:
                base_query = base_query.join(ModelApiKey).filter(
                    ModelApiKey.provider == query.provider
                ).distinct()

            if filters:
                base_query = base_query.filter(and_(*filters))

            # 获取总数
            total = base_query.count()

            # 分页查询
            models = base_query.order_by(desc(ModelConfig.created_at)).offset(
                (query.page - 1) * query.pagesize
            ).limit(query.pagesize).all()

            db_logger.debug(f"模型配置列表查询成功: 总数={total}, 当前页={len(models)}, type筛选={query.type}")
            return models, total

        except Exception as e:
            db_logger.error(f"查询模型配置列表失败: {str(e)}")
            raise

    @staticmethod
    def get_list_new(db: Session, query: ModelConfigQueryNew, tenant_id: uuid.UUID | None = None) -> tuple[
        dict[str, list[ModelConfig]], Any]:
        """获取模型配置列表"""
        db_logger.debug(f"查询模型配置列表: {query.model_dump()}, tenant_id={tenant_id}")
        
        try:
            # 构建查询条件
            filters = []
            
            # 添加租户过滤（查询本租户的模型或公开模型）
            if tenant_id:
                filters.append(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    )
                )
            
            # 支持多个 type 值（使用 IN 查询）
            # 兼容 chat 和 llm 类型：如果查询包含其中一个，则同时匹配两者
            if query.type:
                type_values = list(query.type)
                # 如果包含 chat 或 llm，则同时包含两者
                if ModelType.CHAT in type_values or ModelType.LLM in type_values:
                    if ModelType.CHAT not in type_values:
                        type_values.append(ModelType.CHAT)
                    if ModelType.LLM not in type_values:
                        type_values.append(ModelType.LLM)
                filters.append(ModelConfig.type.in_(type_values))
            
            if query.is_active is not None:
                filters.append(ModelConfig.is_active == query.is_active)
            
            if query.is_public is not None:
                filters.append(ModelConfig.is_public == query.is_public)

            if query.is_composite is not None:
                filters.append(ModelConfig.is_composite == query.is_composite)
            
            if query.provider:
                filters.append(ModelConfig.provider == query.provider)
            
            if query.search:
                search_filter = ModelConfig.name.ilike(f"%{query.search}%")
                filters.append(search_filter)
            
            # 构建基础查询
            base_query = db.query(ModelConfig).options(
                joinedload(ModelConfig.api_keys)
            )
            
            if filters:
                base_query = base_query.filter(and_(*filters))
            
            # 获取总数
            total = base_query.count()

            query_results = base_query.order_by(desc(ModelConfig.created_at)).all()

            provider_groups: Dict[str, List[ModelConfig]] = {}
            for model_config in query_results:
                provider = model_config.provider
                if provider not in provider_groups:
                    provider_groups[provider] = []
                provider_groups[provider].append(model_config)
            
            db_logger.debug(
                f"模型配置列表查询成功: 总数={total}, "
                f"分组数={len(provider_groups)}, "
                f"各分组模型数={[len(v) for v in provider_groups.values()]}, "
                f"type筛选={query.type}")
            return provider_groups, total
            
        except Exception as e:
            db_logger.error(f"查询模型配置列表失败(按provider分组/无分页): {str(e)}")
            raise

    @staticmethod
    def get_by_type(db: Session, model_type: ModelType, tenant_id: uuid.UUID | None = None, is_active: bool = True) -> List[ModelConfig]:
        """根据类型获取模型配置"""
        db_logger.debug(f"根据类型查询模型配置: type={model_type}, tenant_id={tenant_id}, is_active={is_active}")
        
        try:
            query = db.query(ModelConfig).options(
                joinedload(ModelConfig.api_keys)
            ).filter(ModelConfig.type == model_type)
            
            # 添加租户过滤
            if tenant_id:
                query = query.filter(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    )
                )
            
            if is_active:
                query = query.filter(ModelConfig.is_active)
            
            models = query.order_by(ModelConfig.name).all()
            db_logger.debug(f"根据类型查询模型配置成功: 数量={len(models)}")
            return models
            
        except Exception as e:
            db_logger.error(f"根据类型查询模型配置失败: type={model_type} - {str(e)}")
            raise

    @staticmethod
    def create(db: Session, model_data: dict) -> ModelConfig:
        """创建模型配置"""
        db_logger.debug(f"创建模型配置: {model_data.get('name')}")
        
        try:
            db_model = ModelConfig(**model_data)
            db.add(db_model)
            
            db_logger.info(f"模型配置已添加到会话: {db_model.name}")
            return db_model
            
        except Exception as e:
            db.rollback()
            db_logger.error(f"创建模型配置失败: {model_data.get('name')} - {str(e)}")
            raise

    @staticmethod
    def update(db: Session, model_id: uuid.UUID, model_data: ModelConfigUpdate, tenant_id: uuid.UUID | None = None) -> Optional[ModelConfig]:
        """更新模型配置"""
        db_logger.debug(f"更新模型配置: model_id={model_id}, tenant_id={tenant_id}")
        
        try:
            query = db.query(ModelConfig).filter(ModelConfig.id == model_id)
            
            # 添加租户过滤（只能更新本租户的模型）
            if tenant_id:
                query = query.filter(ModelConfig.tenant_id == tenant_id)
            
            db_model = query.first()
            if not db_model:
                db_logger.warning(f"模型配置不存在或无权限: model_id={model_id}")
                return None
            
            # 更新字段
            update_data = model_data.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                setattr(db_model, field, value)
            
            db.commit()
            db.refresh(db_model)
            
            db_logger.info(f"模型配置更新成功: {db_model.name} (ID: {model_id})")
            return db_model
            
        except Exception as e:
            db.rollback()
            db_logger.error(f"更新模型配置失败: model_id={model_id} - {str(e)}")
            raise

    @staticmethod
    def delete(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> bool:
        """删除模型配置"""
        db_logger.debug(f"删除模型配置: model_id={model_id}, tenant_id={tenant_id}")
        
        try:
            query = db.query(ModelConfig).filter(ModelConfig.id == model_id)
            
            # 添加租户过滤（只能删除本租户的模型）
            if tenant_id:
                query = query.filter(ModelConfig.tenant_id == tenant_id)
            
            db_model = query.first()
            if not db_model:
                db_logger.warning(f"模型配置不存在或无权限: model_id={model_id}")
                return False
            
            # 逻辑删除模型配置
            db_model.is_active = False
            db.commit()
            
            db_logger.info(f"模型配置删除成功（逻辑删除）: model_id={model_id}")
            return True
            
        except Exception as e:
            db.rollback()
            db_logger.error(f"删除模型配置失败: model_id={model_id} - {str(e)}")
            raise

    @staticmethod
    def get_stats(db: Session) -> Dict[str, Any]:
        """获取模型统计信息"""
        db_logger.debug("获取模型统计信息")
        
        try:
            # 总数统计
            total_models = db.query(ModelConfig).count()
            active_models = db.query(ModelConfig).filter(ModelConfig.is_active).count()
            
            # 按类型统计
            llm_count = db.query(ModelConfig).filter(ModelConfig.type == ModelType.LLM).count()
            embedding_count = db.query(ModelConfig).filter(ModelConfig.type == ModelType.EMBEDDING).count()
            rerank_count = db.query(ModelConfig).filter(ModelConfig.type == ModelType.RERANK).count()
            
            # 按提供商统计 - 现在从ModelApiKey表获取
            provider_stats = {}
            provider_results = db.query(
                # 保留 provider 字段
                ModelApiKey.provider,
                # 统计中间表中 唯一的 model_config_id 数量（替换原 ModelApiKey.model_config_id）
                func.count(func.distinct(model_config_api_key_association.c.model_config_id))
            ).join(
                # 联表：ModelApiKey <-> 中间表（多对多关联）
                model_config_api_key_association,
                ModelApiKey.id == model_config_api_key_association.c.api_key_id
            ).group_by(
                # 按 provider 分组（保留原有逻辑）
                ModelApiKey.provider
            ).all()
            
            for provider, count in provider_results:
                provider_stats[provider.value] = count
            
            stats = {
                "total_models": total_models,
                "active_models": active_models,
                "llm_count": llm_count,
                "embedding_count": embedding_count,
                "rerank_count": rerank_count,
                "provider_stats": provider_stats
            }
            
            db_logger.debug(f"模型统计信息获取成功: {stats}")
            return stats
            
        except Exception as e:
            db_logger.error(f"获取模型统计信息失败: {str(e)}")
            raise

    @staticmethod
    def get_model_config_ids_by_provider(
        db: Session,
        tenant_id: uuid.UUID,
        provider: Any
    ) -> List[uuid.UUID]:
        """根据tenant_id和provider获取model_config_id列表"""
        db_logger.debug(f"查询model_config_id列表: tenant_id={tenant_id}, provider={provider}")
        
        try:
            # 查询ModelConfig关联的ModelApiKey，筛选出匹配的model_config_id
            model_config_ids = db.query(ModelConfig.id).join(
                ModelBase, ModelConfig.model_id == ModelBase.id
            ).filter(
                and_(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    ),
                    ModelBase.provider == provider,
                    ModelConfig.is_active,
                    ~ModelConfig.is_composite
                )
            ).distinct().all()

            db_logger.debug(f"查询成功: 数量={len(model_config_ids)}")
            return [row[0] for row in model_config_ids]
            
        except Exception as e:
            db_logger.error(f"查询model_config_id列表失败: {str(e)}")
            raise


class ModelApiKeyRepository:
    """模型API Key Repository"""

    @staticmethod
    def get_by_id(db: Session, api_key_id: uuid.UUID) -> Optional[ModelApiKey]:
        """根据ID获取API Key"""
        db_logger.debug(f"根据ID查询API Key: api_key_id={api_key_id}")
        
        try:
            api_key = db.query(ModelApiKey).filter(ModelApiKey.id == api_key_id).first()
            if api_key:
                db_logger.debug(f"API Key查询成功: {api_key.model_name} (ID: {api_key_id})")
            return api_key
        except Exception as e:
            db_logger.error(f"根据ID查询API Key失败: api_key_id={api_key_id} - {str(e)}")
            raise

    @staticmethod
    def get_by_model_config(db: Session, model_config_id: uuid.UUID, is_active: bool = True) -> List[ModelApiKey]:
        """根据模型配置ID获取API Key列表"""
        db_logger.debug(f"根据模型配置ID查询API Key: model_config_id={model_config_id}")
        
        try:
            from app.models.models_model import ModelConfig, model_config_api_key_association
            
            query = db.query(ModelApiKey).join(
                model_config_api_key_association,
                ModelApiKey.id == model_config_api_key_association.c.api_key_id
            ).filter(
                model_config_api_key_association.c.model_config_id == model_config_id
            )
            
            if is_active:
                query = query.filter(ModelApiKey.is_active)
            
            api_keys = query.order_by(ModelApiKey.priority, ModelApiKey.created_at).all()
            db_logger.debug(f"API Key列表查询成功: 数量={len(api_keys)}")
            return api_keys
            
        except Exception as e:
            db_logger.error(f"根据模型配置ID查询API Key失败: model_config_id={model_config_id} - {str(e)}")
            raise

    @staticmethod
    def create(db: Session, api_key_data: ModelApiKeyCreate) -> ModelApiKey:
        """创建API Key"""
        db_logger.debug(f"创建API Key: {api_key_data.provider}")
        
        try:
            from app.models.models_model import ModelConfig
            
            # 创建API Key，不包含model_config_ids
            api_key_dict = api_key_data.model_dump(exclude={"model_config_ids"})
            db_api_key = ModelApiKey(**api_key_dict)
            db.add(db_api_key)
            db.flush()  # 获取生成的ID
            
            # 关联ModelConfig
            if api_key_data.model_config_ids:
                for model_config_id in api_key_data.model_config_ids:
                    model_config = db.query(ModelConfig).filter(ModelConfig.id == model_config_id).first()
                    if model_config:
                        db_api_key.model_configs.append(model_config)
            
            db_logger.info(f"API Key已添加到会话: {db_api_key.provider}")
            return db_api_key
            
        except Exception as e:
            db.rollback()
            db_logger.error(f"创建API Key失败: {api_key_data.provider} - {str(e)}")
            raise

    @staticmethod
    def update(db: Session, api_key_id: uuid.UUID, api_key_data: ModelApiKeyUpdate) -> Optional[ModelApiKey]:
        """更新API Key"""
        db_logger.debug(f"更新API Key: api_key_id={api_key_id}")
        
        try:
            db_api_key = db.query(ModelApiKey).filter(ModelApiKey.id == api_key_id).first()
            if not db_api_key:
                db_logger.warning(f"API Key不存在: api_key_id={api_key_id}")
                return None
            
            # 更新字段
            update_data = api_key_data.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                setattr(db_api_key, field, value)
            
            db.commit()
            db.refresh(db_api_key)
            
            db_logger.info(f"API Key更新成功: {db_api_key.model_name} (ID: {api_key_id})")
            return db_api_key
            
        except Exception as e:
            db.rollback()
            db_logger.error(f"更新API Key失败: api_key_id={api_key_id} - {str(e)}")
            raise

    @staticmethod
    def delete(db: Session, api_key_id: uuid.UUID) -> bool:
        """删除API Key"""
        db_logger.debug(f"删除API Key: api_key_id={api_key_id}")
        
        try:
            db_api_key = db.query(ModelApiKey).filter(ModelApiKey.id == api_key_id).first()
            if not db_api_key:
                db_logger.warning(f"API Key不存在: api_key_id={api_key_id}")
                return False
            
            # 逻辑删除 API Key
            db_api_key.is_active = False
            db.commit()
            
            db_logger.info(f"API Key删除成功（逻辑删除）: api_key_id={api_key_id}")
            return True
            
        except Exception as e:
            db.rollback()
            db_logger.error(f"删除API Key失败: api_key_id={api_key_id} - {str(e)}")
            raise

    @staticmethod
    def update_usage(db: Session, api_key_id: uuid.UUID) -> bool:
        """更新API Key使用统计"""
        db_logger.debug(f"更新API Key使用统计: api_key_id={api_key_id}")
        
        try:
            db_api_key = db.query(ModelApiKey).filter(ModelApiKey.id == api_key_id).first()
            if not db_api_key:
                return False
            
            # 更新使用次数和最后使用时间
            current_count = int(db_api_key.usage_count or "0")
            db_api_key.usage_count = str(current_count + 1)
            db_api_key.last_used_at = func.now()
            
            db.flush()
            db_logger.debug(f"API Key使用统计更新成功: api_key_id={api_key_id}")
            return True
            
        except Exception as e:
            db.rollback()
            db_logger.error(f"更新API Key使用统计失败: api_key_id={api_key_id} - {str(e)}")
            raise


class ModelBaseRepository:
    """基础模型Repository"""

    @staticmethod
    def get_by_id(db: Session, model_base_id: uuid.UUID) -> Optional['ModelBase']:
        return db.query(ModelBase).filter(ModelBase.id == model_base_id).first()

    @staticmethod
    def get_list(db: Session, query: 'ModelBaseQuery') -> List['ModelBase']:
        
        filters = []
        if query.type:
            filters.append(ModelBase.type == query.type)
        if query.provider:
            filters.append(ModelBase.provider == query.provider)
        if query.is_official is not None:
            filters.append(ModelBase.is_official == query.is_official)
        if query.is_deprecated is not None:
            filters.append(ModelBase.is_deprecated == query.is_deprecated)
        if query.search:
            filters.append(or_(
                ModelBase.name.ilike(f"%{query.search}%"),
                # ModelBase.description.ilike(f"%{query.search}%")
            ))
        
        q = db.query(ModelBase)
        if filters:
            q = q.filter(and_(*filters))
        
        return q.order_by(ModelBase.add_count.desc(), ModelBase.created_at.desc()).all()

    @staticmethod
    def create(db: Session, data: dict) -> 'ModelBase':
        model_base = ModelBase(**data)
        db.add(model_base)
        return model_base

    @staticmethod
    def get_by_name_and_provider(db: Session, name: str, provider: str) -> Optional['ModelBase']:
        return db.query(ModelBase).filter(
            ModelBase.name == name,
            ModelBase.provider == provider
        ).first()

    @staticmethod
    def update(db: Session, model_base_id: uuid.UUID, data: dict) -> Optional['ModelBase']:
        model_base = db.query(ModelBase).filter(ModelBase.id == model_base_id).first()
        if not model_base:
            return None
        for key, value in data.items():
            setattr(model_base, key, value)
        
        # 同步更新绑定的非组合模型配置
        if any(k in data for k in ['name', 'description', 'logo']):
            db.query(ModelConfig).filter(
                ModelConfig.model_id == model_base_id,
                ModelConfig.is_composite == False
            ).update({
                k: v for k, v in data.items() 
                if k in ['name', 'description', 'logo']
            }, synchronize_session=False)
        
        return model_base

    @staticmethod
    def delete(db: Session, model_base_id: uuid.UUID) -> bool:
        model_base = db.query(ModelBase).filter(ModelBase.id == model_base_id).first()
        if not model_base:
            return False
        db.delete(model_base)
        return True

    @staticmethod
    def increment_add_count(db: Session, model_base_id: uuid.UUID) -> bool:
        model_base = db.query(ModelBase).filter(ModelBase.id == model_base_id).first()
        if not model_base:
            return False
        model_base.add_count += 1
        return True

    @staticmethod
    def check_added_by_tenant(db: Session, model_base_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        return db.query(ModelConfig).filter(
            ModelConfig.model_id == model_base_id,
            ModelConfig.tenant_id == tenant_id
        ).first() is not None
