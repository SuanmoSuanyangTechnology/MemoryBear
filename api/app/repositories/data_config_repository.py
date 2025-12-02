# -*- coding: utf-8 -*-
"""数据配置Repository模块

本模块提供data_config表的数据访问层，包括SQL查询构建和Neo4j Cypher查询。
从 app.core.memory.src.data_config_api.sql_queries 迁移而来。

Classes:
    DataConfigRepository: 数据配置仓储类，提供CRUD操作和查询构建
"""

from typing import Dict, Tuple, List
from sqlalchemy.orm import Session

from app.schemas.memory_storage_schema import (
    ConfigParamsCreate,
    ConfigParamsDelete,
    ConfigUpdate,
    ConfigUpdateExtracted,
    ConfigUpdateForget,
    ConfigKey,
)
from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()

# 表名常量
TABLE_NAME = "data_config"


class DataConfigRepository:
    """数据配置Repository
    
    提供data_config表的数据访问方法，包括：
    - SQL查询构建（PostgreSQL）
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
    
    # ==================== SQL 查询构建方法 ====================
    
    @staticmethod
    def build_insert(params: ConfigParamsCreate) -> Tuple[str, Dict]:
        """构建插入语句（PostgreSQL 命名参数）
        
        Args:
            params: 配置参数创建模型
            
        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
        """
        db_logger.debug(f"构建插入语句: config_name={params.config_name}, workspace_id={params.workspace_id}")
        
        columns = [
            "config_name",
            "config_desc",
            "workspace_id",
            "llm_id",
            "embedding_id",
            "rerank_id",
            "created_at",
        ]
        placeholders = [
            "%(config_name)s",
            "%(config_desc)s",
            "%(workspace_id)s::uuid",
            "%(llm_id)s",
            "%(embedding_id)s",
            "%(rerank_id)s",
            "timezone('Asia/Shanghai', now())",
        ]
        query = f"INSERT INTO {TABLE_NAME} (" + ",".join(columns) + ") VALUES (" + ",".join(placeholders) + ")"
        # 将 UUID 转换为字符串
        workspace_id_str = str(params.workspace_id) if params.workspace_id else None
        params_dict = {
            "config_name": params.config_name,
            "config_desc": params.config_desc,
            "workspace_id": workspace_id_str,
            "llm_id": params.llm_id,
            "embedding_id": params.embedding_id,
            "rerank_id": params.rerank_id,
        }
        return query, params_dict
    
    @staticmethod
    def build_update(update: ConfigUpdate) -> Tuple[str, Dict]:
        """构建基础配置更新语句（PostgreSQL 命名参数）
        
        Args:
            update: 配置更新模型
            
        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
            
        Raises:
            ValueError: 没有字段需要更新时抛出
        """
        db_logger.debug(f"构建更新语句: config_id={update.config_id}")
        
        key_where = "config_id = %(config_id)s"
        set_fields: List[str] = []
        params: Dict = {
            "config_id": update.config_id,
        }
        
        mapping = {
            "config_name": "config_name",
            "config_desc": "config_desc",
        }
        
        for api_field, db_col in mapping.items():
            value = getattr(update, api_field)
            if value is not None:
                set_fields.append(f"{db_col} = %({api_field})s")
                params[api_field] = value
        
        set_fields.append("updated_at = timezone('Asia/Shanghai', now())")
        if not set_fields:
            raise ValueError("No fields to update")
        query = f"UPDATE {TABLE_NAME} SET " + ", ".join(set_fields) + f" WHERE {key_where}"
        return query, params

    
    @staticmethod
    def build_update_extracted(update: ConfigUpdateExtracted) -> Tuple[str, Dict]:
        """构建记忆萃取引擎配置更新语句（PostgreSQL 命名参数）
        
        Args:
            update: 萃取配置更新模型
            
        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
            
        Raises:
            ValueError: 没有字段需要更新时抛出
        """
        db_logger.debug(f"构建萃取配置更新语句: config_id={update.config_id}")
        
        key_where = "config_id = %(config_id)s"
        set_fields: List[str] = []
        params: Dict = {
            "config_id": update.config_id,
        }
        
        mapping = {
            # 模型选择
            "llm_id": "llm",
            "embedding_id": "embedding",
            "rerank_id": "rerank",
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
        
        for api_field, db_col in mapping.items():
            value = getattr(update, api_field)
            if value is not None:
                set_fields.append(f"{db_col} = %({api_field})s")
                params[api_field] = value
        
        set_fields.append("updated_at = timezone('Asia/Shanghai', now())")
        if not set_fields:
            raise ValueError("No fields to update")
        query = f"UPDATE {TABLE_NAME} SET " + ", ".join(set_fields) + f" WHERE {key_where}"
        return query, params
    
    @staticmethod
    def build_update_forget(update: ConfigUpdateForget) -> Tuple[str, Dict]:
        """构建遗忘引擎配置更新语句（PostgreSQL 命名参数）
        
        Args:
            update: 遗忘配置更新模型
            
        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
            
        Raises:
            ValueError: 没有字段需要更新时抛出
        """
        db_logger.debug(f"构建遗忘配置更新语句: config_id={update.config_id}")
        
        key_where = "config_id = %(config_id)s"
        set_fields: List[str] = []
        params: Dict = {
            "config_id": update.config_id,
        }
        
        mapping = {
            # 遗忘引擎
            "lambda_time": "lambda_time",
            "lambda_mem": "lambda_mem",
            # 由于 PostgreSQL 中 OFFSET 是保留字，需使用双引号包裹列名
            "offset": '"offset"',
        }
        
        for api_field, db_col in mapping.items():
            value = getattr(update, api_field)
            if value is not None:
                set_fields.append(f"{db_col} = %({api_field})s")
                params[api_field] = value
        
        set_fields.append("updated_at = timezone('Asia/Shanghai', now())")
        if not set_fields:
            raise ValueError("No fields to update")
        query = f"UPDATE {TABLE_NAME} SET " + ", ".join(set_fields) + f" WHERE {key_where}"
        return query, params
    
    @staticmethod
    def build_select_extracted(key: ConfigKey) -> Tuple[str, Dict]:
        """构建萃取配置查询语句，通过主键查询某条配置（PostgreSQL 命名参数）
        
        Args:
            key: 配置键模型
            
        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
        """
        db_logger.debug(f"构建萃取配置查询语句: config_id={key.config_id}")
                    # f"SELECT statement_granularity, include_dialogue_context, max_context, "

        query = (
            f"SELECT llm_id, embedding_id, rerank_id, "
            f"enable_llm_dedup_blockwise, enable_llm_disambiguation, deep_retrieval, "
            f"t_type_strict, t_name_strict, t_overall, chunker_strategy, "
            f"statement_granularity, include_dialogue_context, max_context, "
            f"pruning_enabled, pruning_scene, pruning_threshold, "
            f"enable_self_reflexion, iteration_period, reflexion_range, baseline "
            f"FROM {TABLE_NAME} WHERE config_id = %(config_id)s"
        )
        params = {"config_id": key.config_id}
        return query, params
    
    @staticmethod
    def build_select_forget(key: ConfigKey) -> Tuple[str, Dict]:
        """构建遗忘配置查询语句，通过主键查询某条配置（PostgreSQL 命名参数）
        
        Args:
            key: 配置键模型
            
        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
        """
        db_logger.debug(f"构建遗忘配置查询语句: config_id={key.config_id}")
        
        query = (
            f"SELECT lambda_time, lambda_mem, \"offset\" "  # 用双引号包裹保留字别名
            f"FROM {TABLE_NAME} WHERE config_id = %(config_id)s"
        )
        params = {"config_id": key.config_id}
        return query, params
    
    @staticmethod
    def build_select_all(workspace_id = None) -> Tuple[str, Dict]:
        """构建查询所有配置参数的语句（PostgreSQL 命名参数）
        
        Args:
            workspace_id: 工作空间ID（UUID或字符串），用于过滤查询结果
        
        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
        """
        db_logger.debug(f"构建查询所有配置语句: workspace_id={workspace_id}")
        
        if workspace_id:
            # 将 UUID 转换为字符串以便在 SQL 中使用
            workspace_id_str = str(workspace_id) if workspace_id else None
            query = f"SELECT * FROM {TABLE_NAME} WHERE workspace_id = %(workspace_id)s::uuid ORDER BY updated_at DESC NULLS LAST"
            params = {"workspace_id": workspace_id_str}
        else:
            query = f"SELECT * FROM {TABLE_NAME} ORDER BY updated_at DESC NULLS LAST"
            params = {}
        return query, params
    
    @staticmethod
    def build_delete(key: ConfigParamsDelete) -> Tuple[str, Dict]:
        """构建删除语句，通过配置ID删除（PostgreSQL 命名参数）
        
        Args:
            key: 配置删除模型
            
        Returns:
            Tuple[str, Dict]: (SQL查询字符串, 参数字典)
        """
        db_logger.debug(f"构建删除语句: config_id={key.config_id}")
        
        query = (
            f"DELETE FROM {TABLE_NAME} WHERE config_id = %(config_id)s"
        )
        params = {"config_id": key.config_id}
        return query, params
