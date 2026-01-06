"""
遗忘引擎服务层模块

本模块提供遗忘引擎的业务逻辑实现，包括：
1. 遗忘周期执行
2. 配置管理
3. 统计信息查询
4. 遗忘曲线生成

所有业务逻辑从控制器层分离到此服务层。
"""

from typing import Optional, Dict, Any, Tuple
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.logging_config import get_api_logger
from app.core.memory.storage_services.forgetting_engine.actr_calculator import ACTRCalculator
from app.core.memory.storage_services.forgetting_engine.forgetting_strategy import ForgettingStrategy
from app.core.memory.storage_services.forgetting_engine.forgetting_scheduler import ForgettingScheduler
from app.core.memory.storage_services.forgetting_engine.config_utils import (
    load_actr_config_from_db,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.data_config_repository import DataConfigRepository


# 获取API专用日志器
api_logger = get_api_logger()


class MemoryForgetService:
    """遗忘引擎服务类"""
    
    def __init__(self):
        """初始化服务"""
        self.config_repository = DataConfigRepository()
    
    def _get_neo4j_connector(self) -> Neo4jConnector:
        """
        获取 Neo4j 连接器实例
        
        Returns:
            Neo4jConnector: Neo4j 连接器实例
        """
        # 这里应该从配置或依赖注入获取连接器
        # 暂时创建新实例（实际应该使用单例或连接池）
        return Neo4jConnector()
    
    async def _get_forgetting_components(
        self,
        db: Session,
        config_id: Optional[int] = None
    ) -> Tuple[ACTRCalculator, ForgettingStrategy, ForgettingScheduler, Dict[str, Any]]:
        """
        获取遗忘引擎组件（计算器、策略、调度器）
        
        Args:
            db: 数据库会话
            config_id: 配置ID（可选）
        
        Returns:
            tuple: (actr_calculator, forgetting_strategy, forgetting_scheduler, config)
        """
        # 加载配置
        config = load_actr_config_from_db(db, config_id)
        
        # 创建 ACT-R 计算器
        actr_calculator = ACTRCalculator(
            decay_constant=config['decay_constant'],
            forgetting_rate=config['forgetting_rate'],
            offset=config['offset'],
            max_history_length=config['max_history_length']
        )
        
        # 获取 Neo4j 连接器
        connector = self._get_neo4j_connector()
        
        # 创建遗忘策略执行器
        forgetting_strategy = ForgettingStrategy(
            connector=connector,
            actr_calculator=actr_calculator,
            forgetting_threshold=config['forgetting_threshold'],
            enable_llm_summary=config['enable_llm_summary']
        )
        
        # 创建遗忘调度器
        forgetting_scheduler = ForgettingScheduler(
            forgetting_strategy=forgetting_strategy,
            connector=connector
        )
        
        return actr_calculator, forgetting_strategy, forgetting_scheduler, config
    
    async def _get_knowledge_stats(
        self,
        connector: Neo4jConnector,
        group_id: Optional[str] = None,
        forgetting_threshold: float = 0.3
    ) -> Dict[str, Any]:
        """
        获取知识层统计信息
        
        Args:
            connector: Neo4j 连接器
            group_id: 组ID（可选）
            forgetting_threshold: 遗忘阈值
        
        Returns:
            dict: 统计信息字典
        """
        # 构建查询
        query = """
        MATCH (n)
        WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary)
        """
        
        if group_id:
            query += " AND n.group_id = $group_id"
        
        query += """
        WITH n,
             CASE 
               WHEN n:Statement THEN 'statement'
               WHEN n:ExtractedEntity THEN 'entity'
               WHEN n:MemorySummary THEN 'summary'
             END as node_type
        RETURN 
            count(n) as total_nodes,
            sum(CASE WHEN node_type = 'statement' THEN 1 ELSE 0 END) as statement_count,
            sum(CASE WHEN node_type = 'entity' THEN 1 ELSE 0 END) as entity_count,
            sum(CASE WHEN node_type = 'summary' THEN 1 ELSE 0 END) as summary_count,
            avg(n.activation_value) as average_activation,
            sum(CASE WHEN n.activation_value IS NOT NULL AND n.activation_value < $threshold THEN 1 ELSE 0 END) as low_activation_nodes
        """
        
        params = {'threshold': forgetting_threshold}
        if group_id:
            params['group_id'] = group_id
        
        results = await connector.execute_query(query, **params)
        
        if results:
            result = results[0]
            return {
                'total_nodes': result['total_nodes'] or 0,
                'statement_count': result['statement_count'] or 0,
                'entity_count': result['entity_count'] or 0,
                'summary_count': result['summary_count'] or 0,
                'average_activation': result['average_activation'],
                'low_activation_nodes': result['low_activation_nodes'] or 0
            }
        
        return {
            'total_nodes': 0,
            'statement_count': 0,
            'entity_count': 0,
            'summary_count': 0,
            'average_activation': None,
            'low_activation_nodes': 0
        }
    
    async def trigger_forgetting_cycle(
        self,
        db: Session,
        group_id: str,
        max_merge_batch_size: Optional[int] = None,
        min_days_since_access: Optional[int] = None,
        config_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        手动触发遗忘周期
        
        执行一次完整的遗忘周期，识别并融合低激活值节点。
        
        Args:
            db: 数据库会话
            group_id: 组ID（即终端用户ID，必填）
            max_merge_batch_size: 最大融合批次大小（可选）
            min_days_since_access: 最小未访问天数（可选）
            config_id: 配置ID（必填，由控制器层通过 group_id 获取）
        
        Returns:
            dict: 遗忘报告
        """
        # 获取遗忘引擎组件
        _, _, forgetting_scheduler, config = await self._get_forgetting_components(db, config_id)
        
        # 运行遗忘周期（LLM 客户端将在需要时由 forgetting_strategy 内部获取）
        report = await forgetting_scheduler.run_forgetting_cycle(
            group_id=group_id,
            max_merge_batch_size=max_merge_batch_size,
            min_days_since_access=min_days_since_access,
            config_id=config_id,
            db=db
        )
        
        api_logger.info(
            f"遗忘周期完成: 融合 {report['merged_count']} 对节点, "
            f"失败 {report['failed_count']} 对, "
            f"耗时 {report['duration_seconds']:.2f} 秒"
        )
        
        return report
    
    def read_forgetting_config(
        self,
        db: Session,
        config_id: int
    ) -> Dict[str, Any]:
        """
        获取遗忘引擎配置
        
        读取指定配置ID的遗忘引擎参数。
        
        Args:
            db: 数据库会话
            config_id: 配置ID
        
        Returns:
            dict: 配置信息字典
        """
        # 加载配置
        config = load_actr_config_from_db(db, config_id)
        
        # 添加 config_id 到返回结果
        config['config_id'] = config_id
        
        api_logger.info(f"成功读取遗忘引擎配置: config_id={config_id}")
        
        return config
    
    def update_forgetting_config(
        self,
        db: Session,
        config_id: int,
        update_fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        更新遗忘引擎配置
        
        更新指定配置ID的遗忘引擎参数。
        
        Args:
            db: 数据库会话
            config_id: 配置ID
            update_fields: 要更新的字段字典
        
        Returns:
            dict: 更新后的配置信息
        
        Raises:
            ValueError: 配置不存在
        """
        # 检查配置是否存在
        db_config = self.config_repository.get_by_id(db, config_id)
        if db_config is None:
            raise ValueError(f"配置不存在: {config_id}")
        
        # 执行更新
        if update_fields:
            for key, value in update_fields.items():
                if hasattr(db_config, key):
                    setattr(db_config, key, value)
            
            db.commit()
            db.refresh(db_config)
            
            api_logger.info(
                f"成功更新遗忘引擎配置: config_id={config_id}, "
                f"更新字段: {list(update_fields.keys())}"
            )
        else:
            api_logger.info(f"没有字段需要更新: config_id={config_id}")
        
        # 重新加载配置并返回
        config = load_actr_config_from_db(db, config_id)
        config['config_id'] = config_id
        
        return config
    
    async def get_forgetting_stats(
        self,
        db: Session,
        group_id: Optional[str] = None,
        config_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        获取遗忘引擎统计信息
        
        返回知识层节点统计、激活值分布等信息。
        
        Args:
            db: 数据库会话
            group_id: 组ID（可选）
            config_id: 配置ID（可选，用于获取遗忘阈值）
        
        Returns:
            dict: 统计信息字典
        """
        # 获取遗忘引擎组件
        _, _, forgetting_scheduler, config = await self._get_forgetting_components(db, config_id)
        
        connector = forgetting_scheduler.connector
        forgetting_threshold = config['forgetting_threshold']
        
        # 收集激活值指标
        activation_query = """
        MATCH (n)
        WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary OR n:Chunk)
        """
        
        if group_id:
            activation_query += " AND n.group_id = $group_id"
        
        activation_query += """
        RETURN 
            count(n) as total_nodes,
            sum(CASE WHEN n.activation_value IS NOT NULL THEN 1 ELSE 0 END) as nodes_with_activation,
            sum(CASE WHEN n.activation_value IS NULL THEN 1 ELSE 0 END) as nodes_without_activation,
            avg(n.activation_value) as average_activation,
            sum(CASE WHEN n.activation_value IS NOT NULL AND n.activation_value < $threshold THEN 1 ELSE 0 END) as low_activation_nodes
        """
        
        params = {'threshold': forgetting_threshold}
        if group_id:
            params['group_id'] = group_id
        
        activation_results = await connector.execute_query(activation_query, **params)
        
        if activation_results:
            result = activation_results[0]
            activation_metrics = {
                'total_nodes': result['total_nodes'] or 0,
                'nodes_with_activation': result['nodes_with_activation'] or 0,
                'nodes_without_activation': result['nodes_without_activation'] or 0,
                'average_activation_value': result['average_activation'],
                'low_activation_nodes': result['low_activation_nodes'] or 0,
                'timestamp': int(datetime.now().timestamp())
            }
        else:
            activation_metrics = {
                'total_nodes': 0,
                'nodes_with_activation': 0,
                'nodes_without_activation': 0,
                'average_activation_value': None,
                'low_activation_nodes': 0,
                'timestamp': int(datetime.now().timestamp())
            }
        
        # 收集节点类型分布
        distribution_query = """
        MATCH (n)
        WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary OR n:Chunk)
        """
        
        if group_id:
            distribution_query += " AND n.group_id = $group_id"
        
        distribution_query += """
        WITH n,
             CASE 
               WHEN n:Statement THEN 'statement'
               WHEN n:ExtractedEntity THEN 'entity'
               WHEN n:MemorySummary THEN 'summary'
               WHEN n:Chunk THEN 'chunk'
             END as node_type
        RETURN 
            sum(CASE WHEN node_type = 'statement' THEN 1 ELSE 0 END) as statement_count,
            sum(CASE WHEN node_type = 'entity' THEN 1 ELSE 0 END) as entity_count,
            sum(CASE WHEN node_type = 'summary' THEN 1 ELSE 0 END) as summary_count,
            sum(CASE WHEN node_type = 'chunk' THEN 1 ELSE 0 END) as chunk_count
        """
        
        dist_params = {}
        if group_id:
            dist_params['group_id'] = group_id
        
        distribution_results = await connector.execute_query(distribution_query, **dist_params)
        
        if distribution_results:
            result = distribution_results[0]
            node_distribution = {
                'statement_count': result['statement_count'] or 0,
                'entity_count': result['entity_count'] or 0,
                'summary_count': result['summary_count'] or 0,
                'chunk_count': result['chunk_count'] or 0
            }
        else:
            node_distribution = {
                'statement_count': 0,
                'entity_count': 0,
                'summary_count': 0,
                'chunk_count': 0
            }
        
        # 构建统计信息（不包含监控历史数据）
        stats = {
            'activation_metrics': activation_metrics,
            'node_distribution': node_distribution,
            'consistency_check': None,  # 不再提供一致性检查
            'nodes_merged_total': 0,  # 不再跟踪累计融合数
            'recent_cycles': [],  # 不再提供历史记录
            'timestamp': int(datetime.now().timestamp())
        }
        
        api_logger.info(
            f"成功获取遗忘引擎统计: total_nodes={stats['activation_metrics']['total_nodes']}, "
            f"low_activation_nodes={stats['activation_metrics']['low_activation_nodes']}"
        )
        
        return stats
    
    async def get_forgetting_curve(
        self,
        db: Session,
        importance_score: float,
        days: int,
        config_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        获取遗忘曲线数据
        
        生成遗忘曲线数据用于可视化，模拟记忆激活值随时间的衰减。
        
        Args:
            db: 数据库会话
            importance_score: 重要性分数（0-1）
            days: 模拟天数
            config_id: 配置ID（可选）
        
        Returns:
            dict: 包含曲线数据和配置的字典
        """
        # 获取 ACT-R 计算器
        actr_calculator, _, _, config = await self._get_forgetting_components(db, config_id)
        
        # 生成遗忘曲线数据
        initial_time = datetime.now()
        curve_data = actr_calculator.get_forgetting_curve(
            initial_time=initial_time,
            importance_score=importance_score,
            days=days
        )
        
        api_logger.info(
            f"成功生成遗忘曲线数据: {len(curve_data)} 个数据点"
        )
        
        return {
            'curve_data': curve_data,
            'config': {
                'decay_constant': config['decay_constant'],
                'forgetting_rate': config['forgetting_rate'],
                'offset': config['offset'],
                'importance_score': importance_score,
                'days': days
            }
        }
