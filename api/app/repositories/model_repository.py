import uuid
from collections.abc import Sequence
from typing import List, Optional, Dict, Any, Tuple

from sqlalchemy import and_, case, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload

from app.core.utils.datetime_utils import utcnow_naive
from app.core.logging_config import get_db_logger
from app.models.models_model import (
    LLM_FAMILY_TYPES,
    ModelConfig,
    ModelApiKey,
    ModelType,
    ModelBase,
    ModelProvider,
)
from app.schemas.model_schema import (
    ModelConfigQuery, ModelConfigQueryNew
)

# 获取数据库专用日志器
db_logger = get_db_logger()


def _model_type_rank(column):
    """类型展示序（/models、/models/new、model_plaza 同序）：llm/chat 同序（chat 为存量
    归一口径）→ embedding → rerank → image → video → 表外预留 6。"""
    return case(
        (column.in_(LLM_FAMILY_TYPES), 1),
        (column == ModelType.EMBEDDING.value, 2),
        (column == ModelType.RERANK.value, 3),
        (column == ModelType.IMAGE.value, 4),
        (column == ModelType.VIDEO.value, 5),
        else_=6,
    )


def _model_config_display_order():
    """模型配置列表展示序（/models、/models/new 一致）：
    未弃用优先 → 启用优先（未启用但可用紧随其后的位置）→ 类型序 → created_at 新者优先。

    弃用态在 model_bases（组合行 model_id 空 → 子查询 NULL → 非弃用）。
    """
    deprecated_rank = case(
        (
            select(ModelBase.is_deprecated)
            .where(ModelBase.id == ModelConfig.model_id)
            .scalar_subquery()
            .is_(True),
            1,
        ),
        else_=0,
    )
    return (
        deprecated_rank.asc(),
        ModelConfig.is_active.desc(),
        _model_type_rank(ModelConfig.type).asc(),
        ModelConfig.created_at.desc().nullslast(),
    )


class ModelConfigRepository:
    """模型配置Repository"""

    @staticmethod
    def get_by_id(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> Optional[ModelConfig]:
        """根据ID获取模型配置"""
        db_logger.debug(f"根据ID查询模型配置: model_id={model_id}, tenant_id={tenant_id}")

        try:
            # api_keys 仅剩 off 模式回滚读（_select_legacy_key）；M6 随旧表退役
            query = db.query(ModelConfig).options(
                joinedload(ModelConfig.api_keys),
                joinedload(ModelConfig.model_base),
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
    async def get_by_id_async(db: AsyncSession, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> Optional[ModelConfig]:
        """Async version of get_by_id — uses select() for AsyncSession compatibility."""

        try:
            # api_keys 仅剩 off 模式回滚读（_select_legacy_key）；M6 随旧表退役
            query = select(ModelConfig).options(
                joinedload(ModelConfig.api_keys),
                joinedload(ModelConfig.model_base),
            ).filter(ModelConfig.id == model_id)

            if tenant_id:
                query = query.filter(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public,
                    )
                )

            result = await db.execute(query)
            model = result.scalars().first()

            if model:
                db_logger.debug(f"模型配置查询成功(异步): {model.name} (ID: {model_id})")
            else:
                db_logger.debug(f"模型配置不存在(异步): model_id={model_id}")
            return model
        except Exception as e:
            db_logger.error(f"根据ID查询模型配置失败(异步): model_id={model_id} - {str(e)}")
            raise

    @staticmethod
    def get_by_name(db: Session, name: str, provider: str | None = None, tenant_id: uuid.UUID | None = None) -> Optional[ModelConfig]:
        """根据名称和供应商获取模型配置"""
        db_logger.debug(f"根据名称查询模型配置: name={name}, provider={provider}, tenant_id={tenant_id}")
        
        try:
            query = db.query(ModelConfig).filter(ModelConfig.name == name)
            
            # 添加供应商过滤
            if provider:
                query = query.filter(ModelConfig.provider == provider)
            
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
            db_logger.error(f"根据名称查询模型配置失败: name={name}, provider={provider} - {str(e)}")
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
    def list_validation_candidates(
        db: Session, *, provider: str, tenant_id: uuid.UUID
    ) -> List[ModelConfig]:
        """provider 级密钥校验锚点候选：租户可见（本租户/公开）+ 同供应商 + 非组合。

        D4 排序（非废弃优先 / 类型序 / created_at desc）在内存完成（租户模型量有界），
        见 model_channel_service._pick_validation_anchor。
        """
        db_logger.debug(f"查询密钥校验锚点候选: provider={provider}, tenant_id={tenant_id}")
        try:
            stmt = (
                select(ModelConfig)
                .options(joinedload(ModelConfig.model_base))
                .where(
                    ModelConfig.provider == provider,
                    ModelConfig.provider != ModelProvider.COMPOSITE,
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public,
                    ),
                )
            )
            rows = list(db.execute(stmt).scalars().all())
            db_logger.debug(f"密钥校验锚点候选查询成功: 数量={len(rows)}")
            return rows
        except Exception as e:
            db_logger.error(f"查询密钥校验锚点候选失败: provider={provider} - {str(e)}")
            raise

    @staticmethod
    def get_list(db: Session, query: ModelConfigQuery, tenant_id: uuid.UUID | None = None) -> Tuple[List[ModelConfig], int]:
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

            # 支持多个 type 值（使用 IN 查询；13.2 归一后精确匹配，不再 chat↔llm 扩张）
            if query.type:
                filters.append(ModelConfig.type.in_(list(query.type)))

            if query.is_active is not None:
                filters.append(ModelConfig.is_active == query.is_active)

            if query.is_public is not None:
                filters.append(ModelConfig.is_public == query.is_public)

            if query.search:
                search_filter = or_(
                    ModelConfig.name.ilike(f"%{query.search}%"),
                    # ModelConfig.description.ilike(f"%{query.search}%")
                )
                filters.append(search_filter)

            # provider 归属以 config 行为准（渠道为凭据覆盖，无渠道的模型也须可见）
            if query.provider:
                filters.append(ModelConfig.provider == query.provider)

            # 构建基础查询
            base_query = db.query(ModelConfig).options(
                joinedload(ModelConfig.model_base),
            )

            if filters:
                base_query = base_query.filter(and_(*filters))

            # is_available 过滤需探测派生（SQL 不可达）：全量取行，过滤+分页由服务层内存完成
            if query.is_available is not None:
                models = base_query.order_by(*_model_config_display_order()).all()
                db_logger.debug(f"模型配置列表全量查询（is_available 过滤）: 行数={len(models)}")
                return models, len(models)

            # 获取总数
            total = base_query.count()

            # 分页查询
            models = base_query.order_by(*_model_config_display_order()).offset(
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
            
            # 支持多个 type 值（使用 IN 查询；13.2 归一后精确匹配，不再 chat↔llm 扩张）
            if query.type:
                filters.append(ModelConfig.type.in_(list(query.type)))
            
            if query.is_active is not None:
                filters.append(ModelConfig.is_active == query.is_active)
            
            if query.is_public is not None:
                filters.append(ModelConfig.is_public == query.is_public)

            if query.is_composite is not None:
                composite_predicate = ModelConfig.provider == ModelProvider.COMPOSITE
                filters.append(
                    composite_predicate if query.is_composite else ~composite_predicate
                )
            
            if query.provider:
                filters.append(ModelConfig.provider == query.provider)
            
            if query.search:
                search_filter = ModelConfig.name.ilike(f"%{query.search}%")
                filters.append(search_filter)
            
            # 构建基础查询
            base_query = db.query(ModelConfig).options(
                joinedload(ModelConfig.model_base),
            )

            if filters:
                base_query = base_query.filter(and_(*filters))

            # 获取总数
            total = base_query.count()

            # 展示序兼 canonical 选取（§13.1 同名口径：展示行 = 运行期命中行）：
            # 未弃用 → 启用 → 类型序 → 新者，排序首行即 canonical
            query_results = base_query.order_by(*_model_config_display_order()).all()

            provider_groups: Dict[str, List[ModelConfig]] = {}
            seen_keys: set = set()
            for model_config in query_results:
                key = (model_config.provider, model_config.name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                provider_groups.setdefault(model_config.provider, []).append(model_config)
            
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
    def get_by_type(db: Session, model_types: List[ModelType], tenant_id: uuid.UUID | None = None, is_active: bool = True) -> List[ModelConfig]:
        """根据类型获取模型配置，支持多类型查询（枚举成员或裸字符串值）"""
        type_values = [str(getattr(t, "value", t)) for t in model_types]
        db_logger.debug(f"根据类型查询模型配置: types={type_values}, tenant_id={tenant_id}, is_active={is_active}")

        try:
            query = db.query(ModelConfig).options(
                joinedload(ModelConfig.model_base),
            ).filter(ModelConfig.type.in_(type_values))

            if tenant_id:
                query = query.filter(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    )
                )

            if is_active:
                query = query.filter(ModelConfig.is_active)

            query = query.filter(ModelConfig.provider != ModelProvider.COMPOSITE)

            models = query.order_by(ModelConfig.created_at.desc()).all()
            db_logger.debug(f"根据类型查询模型配置成功: 数量={len(models)}")
            return models

        except Exception as e:
            db_logger.error(f"根据类型查询模型配置失败: types={model_types} - {str(e)}")
            raise

    @staticmethod
    def get_members_by_provider_names(
        db: Session,
        tenant_id: uuid.UUID,
        provider_names: Sequence[Tuple[str, str]],
    ) -> dict[Tuple[str, str], ModelConfig]:
        """批量查组合成员 config（单查询）：本租户 + 非组合，按 (provider, name) 定位。

        不看 `config.is_active`：启用/禁用是 config 自身状态位，不是成员资格闸门
        （成员可用性由渠道与凭据活跃决定）。同 (provider, name) 多行时优先启用、较新者。
        """
        if not provider_names:
            return {}
        db_logger.debug(f"批量查询组合成员配置: count={len(provider_names)}, tenant_id={tenant_id}")

        try:
            stmt = (
                select(ModelConfig)
                .where(
                    ModelConfig.tenant_id == tenant_id,
                    ModelConfig.provider != ModelProvider.COMPOSITE,
                    tuple_(ModelConfig.provider, ModelConfig.name).in_(list(provider_names)),
                )
                .order_by(
                    ModelConfig.is_active.desc(),
                    ModelConfig.created_at.desc().nullslast(),
                )
            )
            rows = db.execute(stmt).scalars().all()
            index: dict[Tuple[str, str], ModelConfig] = {}
            for row in rows:
                index.setdefault((row.provider, row.name), row)
            return index
        except Exception as e:
            db_logger.error(f"批量查询组合成员配置失败: {str(e)}")
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
    def update(db: Session, model_id: uuid.UUID, update_data: dict, tenant_id: uuid.UUID | None = None) -> Optional[ModelConfig]:
        """更新模型配置（update_data 由服务层构造：含三新列换算，见 model_service._config_update_payload）"""
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
    def get_model_config_ids_by_provider(
        db: Session,
        tenant_id: uuid.UUID,
        provider: Any
    ) -> List[uuid.UUID]:
        """根据tenant_id和provider获取model_config_id列表"""
        db_logger.debug(f"查询model_config_id列表: tenant_id={tenant_id}, provider={provider}")
        
        try:
            # 查询ModelConfig关联的ModelApiKey，筛选出匹配的model_config_id
            model_config_ids = db.query(ModelConfig.id).filter(
                and_(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    ),
                    ModelConfig.provider == provider,
                    ModelConfig.provider != ModelProvider.COMPOSITE
                )
            ).all()

            db_logger.debug(f"查询成功: 数量={len(model_config_ids)}")
            return [row[0] for row in model_config_ids]
            
        except Exception as e:
            db_logger.error(f"查询model_config_id列表失败: {str(e)}")
            raise


class ModelApiKeyRepository:
    """旧表 API Key Repository（M6 退役：仅保留 off 模式 usage 计数，关联/CRUD 已随 Task 13 删除）"""

    @staticmethod
    def update_usage(db: Session, api_key_id: uuid.UUID) -> bool:
        """更新API Key使用统计（仅 `MODEL_CHANNEL_RESOLUTION=off` 回滚窗口写入）"""
        db_logger.debug(f"更新API Key使用统计: api_key_id={api_key_id}")
        
        try:
            db_api_key = db.query(ModelApiKey).filter(ModelApiKey.id == api_key_id).first()
            if not db_api_key:
                return False
            
            # 更新使用次数和最后使用时间
            current_count = int(db_api_key.usage_count or "0")
            db_api_key.usage_count = str(current_count + 1)
            db_api_key.last_used_at = utcnow_naive()
            
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

        # 广场排序（G4/D13.9）：未下线优先 → 接口族分组 → 组内热度 → 新旧兜底
        return q.order_by(
            ModelBase.is_deprecated.asc(),
            _model_type_rank(ModelBase.type).asc(),
            ModelBase.add_count.desc(),
            ModelBase.created_at.desc().nullslast(),
        ).all()

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
    def get_by_name_provider_type(db: Session, name: str, provider: str, model_type: str) -> Optional['ModelBase']:
        """广场收录判定（(name, provider, type) 三元组，供自定义模型入口守卫用）。"""
        return db.query(ModelBase).filter(
            ModelBase.name == name,
            ModelBase.provider == provider,
            ModelBase.type == model_type
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
                ModelConfig.provider != ModelProvider.COMPOSITE
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

    @staticmethod
    def get_added_model_ids(
        db: Session, tenant_id: uuid.UUID, model_base_ids: List[uuid.UUID]
    ) -> set:
        """批量返回租户已添加的基础模型ID集合（替代列表场景逐行 check_added_by_tenant）。"""
        if not model_base_ids:
            return set()
        rows = db.query(ModelConfig.model_id).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.model_id.in_(model_base_ids)
        ).distinct().all()
        return {row[0] for row in rows}
