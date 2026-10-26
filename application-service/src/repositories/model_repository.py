import logging

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, List, Optional, Dict, Any, Tuple

from sqlalchemy import and_, case, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload

from ..utils.datetime_utils import utcnow_naive
from ..models.models_model import (
    LLM_FAMILY_TYPES,
    ModelConfig,
    ModelApiKey,
    ModelType,
    ModelBase,
    ModelProvider,
)
from ..schemas.model_schema import (
    ModelConfigQuery, ModelConfigQueryNew
)

if TYPE_CHECKING:
    from ..schemas.model_schema import ModelBaseQuery

# Logger dedicated to this database module
db_logger = logging.getLogger(__name__)


def _model_type_rank(column):
    """Type display rank (same order for /models, /models/new and model_plaza): llm/chat share a
    rank (chat is the legacy normalization) → embedding → rerank → image → video → 6 reserved
    for unknown types."""
    return case(
        (column.in_(LLM_FAMILY_TYPES), 1),
        (column == ModelType.EMBEDDING.value, 2),
        (column == ModelType.RERANK.value, 3),
        (column == ModelType.IMAGE.value, 4),
        (column == ModelType.VIDEO.value, 5),
        else_=6,
    )


def _model_config_display_order():
    """Display order for the model config list (identical for /models and /models/new):
    non-deprecated first → enabled first (disabled but usable follows right after) → type rank
    → newest created_at first.

    Deprecation state lives in model_bases (composite rows have a NULL model_id → subquery NULL
    → not deprecated).
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
    """Model config repository."""

    @staticmethod
    def get_by_id(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> Optional[ModelConfig]:
        """Get a model config by ID."""
        db_logger.debug(f"根据ID查询模型配置: model_id={model_id}, tenant_id={tenant_id}")

        try:
            # api_keys is only read by the off-mode rollback path (_select_legacy_key); retires with the legacy table
            query = db.query(ModelConfig).options(
                joinedload(ModelConfig.api_keys),
                joinedload(ModelConfig.model_base),
            ).filter(ModelConfig.id == model_id)

            # Add tenant filter
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
            # api_keys is only read by the off-mode rollback path (_select_legacy_key); retires with the legacy table
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
        """Get a model config by name and provider."""
        db_logger.debug(f"根据名称查询模型配置: name={name}, provider={provider}, tenant_id={tenant_id}")
        
        try:
            query = db.query(ModelConfig).filter(ModelConfig.name == name)
            
            # Add provider filter
            if provider:
                query = query.filter(ModelConfig.provider == provider)
            
            # Add tenant filter
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
        """Fuzzy-match model configs by name.

        Args:
            name: Model name keyword (fuzzy match)
            tenant_id: Tenant ID
            limit: Maximum number of rows to return
        Returns:
            Model config list
        """
        db_logger.debug(f"按名称模糊查询模型配置: name~{name}, tenant_id={tenant_id}, limit={limit}")
        try:
            query = db.query(ModelConfig).filter(ModelConfig.name.ilike(f"%{name}%"))
            
            # Add tenant filter
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
        """Candidates for the provider-level key-validation anchor: tenant-visible (own tenant/public) + same provider + non-composite.

        The ordering (non-deprecated first / type rank / created_at desc) is done in memory (the
        per-tenant model count is bounded), see model_channel_service._pick_validation_anchor.
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
        """Get the model config list."""
        db_logger.debug(f"查询模型配置列表: {query.model_dump()}, tenant_id={tenant_id}")

        try:
            # Build the query filters
            filters = []

            # Add tenant filter (this tenant's models or public models)
            if tenant_id:
                filters.append(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    )
                )

            # Support multiple type values (IN query; exact match after normalization, no more chat↔llm expansion)
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

            # Provider ownership follows the config row (channels only override credentials; models without channels must stay visible)
            if query.provider:
                filters.append(ModelConfig.provider == query.provider)

            # Build the base query
            base_query = db.query(ModelConfig).options(
                joinedload(ModelConfig.model_base),
            )

            if filters:
                base_query = base_query.filter(and_(*filters))

            # No pagination; fetch all rows (is_available filtering needs derived probing done in memory by the service layer)
            models = base_query.order_by(*_model_config_display_order()).all()

            db_logger.debug(f"模型配置列表查询成功: 行数={len(models)}, type筛选={query.type}")
            return models, len(models)

        except Exception as e:
            db_logger.error(f"查询模型配置列表失败: {str(e)}")
            raise

    @staticmethod
    def get_list_new(db: Session, query: ModelConfigQueryNew, tenant_id: uuid.UUID | None = None) -> tuple[
        dict[str, list[ModelConfig]], Any]:
        """Get the model config list."""
        db_logger.debug(f"查询模型配置列表: {query.model_dump()}, tenant_id={tenant_id}")
        
        try:
            # Build the query filters
            filters = []
            
            # Add tenant filter (this tenant's models or public models)
            if tenant_id:
                filters.append(
                    or_(
                        ModelConfig.tenant_id == tenant_id,
                        ModelConfig.is_public
                    )
                )
            
            # Support multiple type values (IN query; exact match after normalization, no more chat↔llm expansion)
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
            
            # Build the base query
            base_query = db.query(ModelConfig).options(
                joinedload(ModelConfig.model_base),
            )

            if filters:
                base_query = base_query.filter(and_(*filters))

            # Fetch the total count
            total = base_query.count()

            # Display order doubles as canonical selection (same-name policy: the display row is
            # the runtime-hit row): non-deprecated → enabled → type rank → newest; the first row is canonical
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
        """Get model configs by type, supporting multiple types (enum members or plain string values)."""
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
        """Batch-load composite member configs (single query): own tenant + non-composite, keyed by (provider, name).

        Ignores `config.is_active`: enabled/disabled is the config's own state, not a membership
        gate (member availability is decided by active channels and credentials). Among duplicate
        (provider, name) rows, prefer enabled and newer ones.
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
    def get_model_config_ids_by_provider(
        db: Session,
        tenant_id: uuid.UUID,
        provider: Any
    ) -> List[uuid.UUID]:
        """Get the model_config_id list by tenant_id and provider."""
        db_logger.debug(f"查询model_config_id列表: tenant_id={tenant_id}, provider={provider}")
        
        try:
            # Query the ModelApiKey rows associated with ModelConfig and pick the matching model_config_id values
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
    """Legacy API key repository (retiring: only the off-mode usage counter remains; association/CRUD were removed)."""

    @staticmethod
    def update_usage(db: Session, api_key_id: uuid.UUID) -> bool:
        """Update the API key usage counters (written only in the `MODEL_CHANNEL_RESOLUTION=off` rollback window)."""
        db_logger.debug(f"更新API Key使用统计: api_key_id={api_key_id}")
        
        try:
            db_api_key = db.query(ModelApiKey).filter(ModelApiKey.id == api_key_id).first()
            if not db_api_key:
                return False
            
            # Update the usage count and last-used time
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
    """Base model repository."""

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

        # Plaza ordering: non-deprecated first → API-family grouping → popularity within group → newest as tiebreaker
        return q.order_by(
            ModelBase.is_deprecated.asc(),
            _model_type_rank(ModelBase.type).asc(),
            ModelBase.add_count.desc(),
            ModelBase.created_at.desc().nullslast(),
        ).all()

    @staticmethod
    def get_by_name_and_provider(db: Session, name: str, provider: str) -> Optional['ModelBase']:
        return db.query(ModelBase).filter(
            ModelBase.name == name,
            ModelBase.provider == provider
        ).first()

    @staticmethod
    def get_by_name_provider_type(db: Session, name: str, provider: str, model_type: str) -> Optional['ModelBase']:
        """Plaza inclusion check ((name, provider, type) triple, used as a guard for the custom-model entry)."""
        return db.query(ModelBase).filter(
            ModelBase.name == name,
            ModelBase.provider == provider,
            ModelBase.type == model_type
        ).first()

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
        """Batch-return the set of base model IDs the tenant has added (replaces the per-row check_added_by_tenant in list flows)."""
        if not model_base_ids:
            return set()
        rows = db.query(ModelConfig.model_id).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.model_id.in_(model_base_ids)
        ).distinct().all()
        return {row[0] for row in rows}
