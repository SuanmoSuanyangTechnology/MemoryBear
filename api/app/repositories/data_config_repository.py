# -*- coding: utf-8 -*-
"""数据配置Repository模块

本模块提供data_config表的数据访问层，使用SQLAlchemy ORM进行数据库操作。
包括CRUD操作和Neo4j Cypher查询常量。

Classes:
    DataConfigRepository: 数据配置仓储类，提供CRUD操作
"""

from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import desc
import uuid

from app.models.data_config_model import DataConfig
from app.schemas.memory_storage_schema import (
    ConfigParamsCreate,
    ConfigUpdate,
    ConfigUpdateExtracted,
    ConfigUpdateForget,
)
from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()

TABLE_NAME = "data_config"
class DataConfigRepository:
    """数据配置Repository

    提供data_config表的数据访问方法，包括：
    - SQLAlchemy ORM 数据库操作
    - Neo4j Cypher查询常量
    """

    # ==================== Neo4j Cypher 查询常量 ====================

    # Dialogue count by group
    SEARCH_FOR_DIALOGUE = """
    MATCH (n:Dialogue) WHERE n.group_id = $group_id RETURN COUNT(n) AS num
    """

    # Chunk count by group
    SEARCH_FOR_CHUNK = """
    MATCH (n:Chunk) WHERE n.group_id = $group_id RETURN COUNT(n) AS num
    """

    # Statement count by group
    SEARCH_FOR_STATEMENT = """
    MATCH (n:Statement) WHERE n.group_id = $group_id RETURN COUNT(n) AS num
    """

    # ExtractedEntity count by group
    SEARCH_FOR_ENTITY = """
    MATCH (n:ExtractedEntity) WHERE n.group_id = $group_id RETURN COUNT(n) AS num
    """

    # All counts by label and total
    SEARCH_FOR_ALL = """
    OPTIONAL MATCH (n:Dialogue) WHERE n.group_id = $group_id RETURN 'Dialogue' AS Label, COUNT(n) AS Count
    UNION ALL
    OPTIONAL MATCH (n:Chunk) WHERE n.group_id = $group_id RETURN 'Chunk' AS Label, COUNT(n) AS Count
    UNION ALL
    OPTIONAL MATCH (n:Statement) WHERE n.group_id = $group_id RETURN 'Statement' AS Label, COUNT(n) AS Count
    UNION ALL
    OPTIONAL MATCH (n:ExtractedEntity) WHERE n.group_id = $group_id RETURN 'ExtractedEntity' AS Label, COUNT(n) AS Count
    UNION ALL
    OPTIONAL MATCH (n) WHERE n.group_id = $group_id RETURN 'ALL' AS Label, COUNT(n) AS Count
    """

    # Extracted entity details within group/app/user
    SEARCH_FOR_DETIALS = """
    MATCH (n:ExtractedEntity)
    WHERE n.group_id = $group_id
    RETURN n.entity_idx AS entity_idx, 
        n.connect_strength AS connect_strength, 
        n.description AS description, 
        n.entity_type AS entity_type, 
        n.name AS name,
        n.fact_summary AS fact_summary,
        n.group_id AS group_id,
        n.apply_id AS apply_id,
        n.user_id AS user_id,
        n.id AS id
    """

    # Edges between extracted entities within group/app/user
    SEARCH_FOR_EDGES = """
    MATCH (n:ExtractedEntity)-[r]->(m:ExtractedEntity)
    WHERE n.group_id = $group_id
    RETURN
      r.group_id AS group_id,
      r.apply_id AS apply_id,
      r.user_id AS user_id,
      elementId(r) AS rel_id,
      startNode(r).id AS source_id,
      endNode(r).id AS target_id,
      r.predicate AS predicate,
      r.statement_id AS statement_id,
      r.statement AS statement
    """

    # Entity graph within group (source node, edge, target node)
    SEARCH_FOR_ENTITY_GRAPH = """
    MATCH (n:ExtractedEntity)-[r]->(m:ExtractedEntity)
    WHERE n.group_id = $group_id
    RETURN
      {
        entity_idx: n.entity_idx,
        connect_strength: n.connect_strength,
        description: n.description,
        entity_type: n.entity_type,
        name: n.name,
        fact_summary: n.fact_summary,
        id: n.id
      } AS sourceNode,
      {
        rel_id: elementId(r),
        source_id: startNode(r).id,
        target_id: endNode(r).id,
        predicate: r.predicate,
        statement_id: r.statement_id,
        statement: r.statement
      } AS edge,
      {
        entity_idx: m.entity_idx,
        connect_strength: m.connect_strength,
        description: m.description,
        entity_type: m.entity_type,
        name: m.name,
        fact_summary: m.fact_summary,
        id: m.id
      } AS targetNode
    """

    # ==================== SQLAlchemy ORM 数据库操作方法 ====================
    @staticmethod
    def build_update_reflection(config_id: int, **kwargs) -> Tuple[str, Dict]:
        """构建反思配置更新语句（SQLAlchemy text() 命名参数）

        Args:
            config_id: 配置ID
            **kwargs: 反思配置参数

        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)

        Raises:
            ValueError: 没有字段需要更新时抛出
        """
        db_logger.debug(f"构建反思配置更新语句: config_id={config_id}")

        key_where = "config_id = :config_id"
        set_fields: List[str] = []
        params: Dict = {
            "config_id": config_id,
        }

        # 反思配置字段映射
        mapping = {
            "enable_self_reflexion": "enable_self_reflexion",
            "iteration_period": "iteration_period",
            "reflexion_range": "reflexion_range",
            "baseline": "baseline",
            "reflection_model_id": "reflection_model_id",
            "memory_verify": "memory_verify",
            "quality_assessment": "quality_assessment",
        }

        for api_field, db_col in mapping.items():
            if api_field in kwargs and kwargs[api_field] is not None:
                set_fields.append(f"{db_col} = :{api_field}")
                params[api_field] = kwargs[api_field]

        if not set_fields:
            raise ValueError("No fields to update")

        set_fields.append("updated_at = timezone('Asia/Shanghai', now())")
        query = f"UPDATE {TABLE_NAME} SET " + ", ".join(set_fields) + f" WHERE {key_where}"
        return query, params

    @staticmethod
    def build_select_reflection(config_id: int) -> Tuple[str, Dict]:
        """构建反思配置查询语句，通过config_id查询反思配置（SQLAlchemy text() 命名参数）

        Args:
            config_id: 配置ID

        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
        """
        db_logger.debug(f"构建反思配置查询语句: config_id={config_id}")

        query = (
            f"SELECT config_id, enable_self_reflexion, iteration_period, reflexion_range, baseline, "
            f"reflection_model_id, memory_verify, quality_assessment, user_id "
            f"FROM {TABLE_NAME} WHERE config_id = :config_id"
        )
        params = {"config_id": config_id}
        return query, params

    @staticmethod
    def build_select_all(workspace_id: uuid.UUID) -> Tuple[str, Dict]:
        """构建查询所有配置的语句（SQLAlchemy text() 命名参数）

        Args:
            workspace_id: 工作空间ID

        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
        """
        db_logger.debug(f"构建查询所有配置语句: workspace_id={workspace_id}")

        query = (
            f"SELECT config_id, config_name, enable_self_reflexion, iteration_period, reflexion_range, baseline, "
            f"reflection_model_id, memory_verify, quality_assessment, user_id, created_at, updated_at "
            f"FROM {TABLE_NAME} WHERE workspace_id = :workspace_id ORDER BY updated_at DESC"
        )
        params = {"workspace_id": workspace_id}
        return query, params

    @staticmethod
    def create(db: Session, params: ConfigParamsCreate) -> DataConfig:
        """创建数据配置

        Args:
            db: 数据库会话
            params: 配置参数创建模型

        Returns:
            DataConfig: 创建的配置对象
        """
        db_logger.debug(f"创建数据配置: config_name={params.config_name}, workspace_id={params.workspace_id}")

        try:
            db_config = DataConfig(
                config_name=params.config_name,
                config_desc=params.config_desc,
                workspace_id=params.workspace_id,
                llm_id=params.llm_id,
                embedding_id=params.embedding_id,
                rerank_id=params.rerank_id,
            )
            db.add(db_config)
            db.flush()  # 获取自增ID但不提交事务

            db_logger.info(f"数据配置已添加到会话: {db_config.config_name} (ID: {db_config.config_id})")
            return db_config

        except Exception as e:
            db.rollback()
            db_logger.error(f"创建数据配置失败: {params.config_name} - {str(e)}")
            raise

    @staticmethod
    def update(db: Session, update: ConfigUpdate) -> Optional[DataConfig]:
        """更新基础配置

        Args:
            db: 数据库会话
            update: 配置更新模型

        Returns:
            Optional[DataConfig]: 更新后的配置对象，不存在则返回None

        Raises:
            ValueError: 没有字段需要更新时抛出
        """
        db_logger.debug(f"更新数据配置: config_id={update.config_id}")

        try:
            db_config = db.query(DataConfig).filter(DataConfig.config_id == update.config_id).first()
            if not db_config:
                db_logger.warning(f"数据配置不存在: config_id={update.config_id}")
                return None

            # 更新字段
            has_update = False
            if update.config_name is not None:
                db_config.config_name = update.config_name
                has_update = True
            if update.config_desc is not None:
                db_config.config_desc = update.config_desc
                has_update = True

            if not has_update:
                raise ValueError("No fields to update")

            db.commit()
            db.refresh(db_config)

            db_logger.info(f"数据配置更新成功: {db_config.config_name} (ID: {update.config_id})")
            return db_config

        except Exception as e:
            db.rollback()
            db_logger.error(f"更新数据配置失败: config_id={update.config_id} - {str(e)}")
            raise


    @staticmethod
    def update_extracted(db: Session, update: ConfigUpdateExtracted) -> Optional[DataConfig]:
        """更新记忆萃取引擎配置

        Args:
            db: 数据库会话
            update: 萃取配置更新模型

        Returns:
            Optional[DataConfig]: 更新后的配置对象，不存在则返回None

        Raises:
            ValueError: 没有字段需要更新时抛出
        """
        db_logger.debug(f"更新萃取配置: config_id={update.config_id}")

        try:
            db_config = db.query(DataConfig).filter(DataConfig.config_id == update.config_id).first()
            if not db_config:
                db_logger.warning(f"数据配置不存在: config_id={update.config_id}")
                return None

            # 更新字段映射
            field_mapping = {
                # 模型选择
                "llm_id": "llm",
                "embedding_id": "embedding_id",
                "rerank_id": "rerank_id",
                # 记忆萃取引擎
                "enable_llm_dedup_blockwise": "enable_llm_dedup_blockwise",
                "enable_llm_disambiguation": "enable_llm_disambiguation",
                "deep_retrieval": "deep_retrieval",
                "t_type_strict": "t_type_strict",
                "t_name_strict": "t_name_strict",
                "t_overall": "t_overall",
                "state": "state",
                "chunker_strategy": "chunker_strategy",
                # 句子提取
                "statement_granularity": "statement_granularity",
                "include_dialogue_context": "include_dialogue_context",
                "max_context": "max_context",
                # 剪枝配置
                "pruning_enabled": "pruning_enabled",
                "pruning_scene": "pruning_scene",
                "pruning_threshold": "pruning_threshold",
                # 自我反思配置
                "enable_self_reflexion": "enable_self_reflexion",
                "iteration_period": "iteration_period",
                "reflexion_range": "reflexion_range",
                "baseline": "baseline",
            }

            has_update = False
            for api_field, db_field in field_mapping.items():
                value = getattr(update, api_field, None)
                if value is not None:
                    setattr(db_config, db_field, value)
                    has_update = True

            if not has_update:
                raise ValueError("No fields to update")

            db.commit()
            db.refresh(db_config)

            db_logger.info(f"萃取配置更新成功: config_id={update.config_id}")
            return db_config

        except Exception as e:
            db.rollback()
            db_logger.error(f"更新萃取配置失败: config_id={update.config_id} - {str(e)}")
            raise

    @staticmethod
    def update_forget(db: Session, update: ConfigUpdateForget) -> Optional[DataConfig]:
        """更新遗忘引擎配置

        Args:
            db: 数据库会话
            update: 遗忘配置更新模型

        Returns:
            Optional[DataConfig]: 更新后的配置对象，不存在则返回None

        Raises:
            ValueError: 没有字段需要更新时抛出
        """
        db_logger.debug(f"更新遗忘配置: config_id={update.config_id}")

        try:
            db_config = db.query(DataConfig).filter(DataConfig.config_id == update.config_id).first()
            if not db_config:
                db_logger.warning(f"数据配置不存在: config_id={update.config_id}")
                return None

            # 更新字段
            has_update = False
            if update.lambda_time is not None:
                db_config.lambda_time = update.lambda_time
                has_update = True
            if update.lambda_mem is not None:
                db_config.lambda_mem = update.lambda_mem
                has_update = True
            if update.offset is not None:
                db_config.offset = update.offset
                has_update = True

            if not has_update:
                raise ValueError("No fields to update")

            db.commit()
            db.refresh(db_config)

            db_logger.info(f"遗忘配置更新成功: config_id={update.config_id}")
            return db_config

        except Exception as e:
            db.rollback()
            db_logger.error(f"更新遗忘配置失败: config_id={update.config_id} - {str(e)}")
            raise

    @staticmethod
    def get_extracted_config(db: Session, config_id: int) -> Optional[Dict]:
        """获取萃取配置，通过主键查询某条配置

        Args:
            db: 数据库会话
            config_id: 配置ID

        Returns:
            Optional[Dict]: 萃取配置字典，不存在则返回None
        """
        db_logger.debug(f"查询萃取配置: config_id={config_id}")

        try:
            db_config = db.query(DataConfig).filter(DataConfig.config_id == config_id).first()
            if not db_config:
                db_logger.debug(f"萃取配置不存在: config_id={config_id}")
                return None

            result = {
                "llm_id": db_config.llm_id,
                "embedding_id": db_config.embedding_id,
                "rerank_id": db_config.rerank_id,
                "enable_llm_dedup_blockwise": db_config.enable_llm_dedup_blockwise,
                "enable_llm_disambiguation": db_config.enable_llm_disambiguation,
                "deep_retrieval": db_config.deep_retrieval,
                "t_type_strict": db_config.t_type_strict,
                "t_name_strict": db_config.t_name_strict,
                "t_overall": db_config.t_overall,
                "chunker_strategy": db_config.chunker_strategy,
                "statement_granularity": db_config.statement_granularity,
                "include_dialogue_context": db_config.include_dialogue_context,
                "max_context": db_config.max_context,
                "pruning_enabled": db_config.pruning_enabled,
                "pruning_scene": db_config.pruning_scene,
                "pruning_threshold": db_config.pruning_threshold,
                "enable_self_reflexion": db_config.enable_self_reflexion,
                "iteration_period": db_config.iteration_period,
                "reflexion_range": db_config.reflexion_range,
                "baseline": db_config.baseline,
            }

            db_logger.debug(f"萃取配置查询成功: config_id={config_id}")
            return result

        except Exception as e:
            db_logger.error(f"查询萃取配置失败: config_id={config_id} - {str(e)}")
            raise

    @staticmethod
    def get_forget_config(db: Session, config_id: int) -> Optional[Dict]:
        """获取遗忘配置，通过主键查询某条配置

        Args:
            db: 数据库会话
            config_id: 配置ID

        Returns:
            Optional[Dict]: 遗忘配置字典，不存在则返回None
        """
        db_logger.debug(f"查询遗忘配置: config_id={config_id}")

        try:
            db_config = db.query(DataConfig).filter(DataConfig.config_id == config_id).first()
            if not db_config:
                db_logger.debug(f"遗忘配置不存在: config_id={config_id}")
                return None

            result = {
                "lambda_time": db_config.lambda_time,
                "lambda_mem": db_config.lambda_mem,
                "offset": db_config.offset,
            }

            db_logger.debug(f"遗忘配置查询成功: config_id={config_id}")
            return result

        except Exception as e:
            db_logger.error(f"查询遗忘配置失败: config_id={config_id} - {str(e)}")
            raise

    @staticmethod
    def get_by_id(db: Session, config_id: int) -> Optional[DataConfig]:
        """根据ID获取数据配置

        Args:
            db: 数据库会话
            config_id: 配置ID

        Returns:
            Optional[DataConfig]: 配置对象，不存在则返回None
        """
        db_logger.debug(f"根据ID查询数据配置: config_id={config_id}")

        try:
            config = db.query(DataConfig).filter(DataConfig.config_id == config_id).first()

            if config:
                db_logger.debug(f"数据配置查询成功: {config.config_name} (ID: {config_id})")
            else:
                db_logger.debug(f"数据配置不存在: config_id={config_id}")
            return config
        except Exception as e:
            db_logger.error(f"根据ID查询数据配置失败: config_id={config_id} - {str(e)}")
            raise

    @staticmethod
    def get_all(db: Session, workspace_id: Optional[uuid.UUID] = None) -> List[DataConfig]:
        """获取所有配置参数

        Args:
            db: 数据库会话
            workspace_id: 工作空间ID，用于过滤查询结果

        Returns:
            List[DataConfig]: 配置列表
        """
        db_logger.debug(f"查询所有配置: workspace_id={workspace_id}")

        try:
            query = db.query(DataConfig)

            if workspace_id:
                query = query.filter(DataConfig.workspace_id == workspace_id)

            configs = query.order_by(desc(DataConfig.updated_at)).all()

            db_logger.debug(f"配置列表查询成功: 数量={len(configs)}")
            return configs

        except Exception as e:
            db_logger.error(f"查询所有配置失败: workspace_id={workspace_id} - {str(e)}")
            raise

    @staticmethod
    def delete(db: Session, config_id: int) -> bool:
        """删除数据配置

        Args:
            db: 数据库会话
            config_id: 配置ID

        Returns:
            bool: 删除成功返回True，配置不存在返回False
        """
        db_logger.debug(f"删除数据配置: config_id={config_id}")

        try:
            db_config = db.query(DataConfig).filter(DataConfig.config_id == config_id).first()
            if not db_config:
                db_logger.warning(f"数据配置不存在: config_id={config_id}")
                return False

            db.delete(db_config)
            db.commit()

            db_logger.info(f"数据配置删除成功: config_id={config_id}")
            return True

        except Exception as e:
            db.rollback()
            db_logger.error(f"删除数据配置失败: config_id={config_id} - {str(e)}")
            raise

