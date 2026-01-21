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
from datetime import datetime, timezone

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
from app.repositories.forgetting_cycle_history_repository import ForgettingCycleHistoryRepository


# 获取API专用日志器
api_logger = get_api_logger()


def convert_neo4j_datetime_to_python(value: Any) -> Optional[datetime]:
    """
    将 Neo4j DateTime 对象转换为 Python datetime 对象
    
    Args:
        value: Neo4j DateTime 对象、Python datetime 对象或字符串
    
    Returns:
        Python datetime 对象或 None
    """
    if value is None:
        return None
    
    try:
        # Neo4j DateTime 对象
        if hasattr(value, 'to_native'):
            return value.to_native()
        # Python datetime 对象
        elif isinstance(value, datetime):
            return value
        # 字符串格式
        elif isinstance(value, str):
            if value.endswith('Z'):
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            else:
                return datetime.fromisoformat(value)
        # 其他类型，尝试转换为字符串
        else:
            return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception as e:
        api_logger.warning(f"转换时间失败: {value} (类型: {type(value).__name__}), 错误: {e}")
        return None


class MemoryForgetService:
    """遗忘引擎服务类"""
    
    def __init__(self):
        """初始化服务"""
        self.config_repository = DataConfigRepository()
        self.history_repository = ForgettingCycleHistoryRepository()
    
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
        end_user_id: Optional[str] = None,
        forgetting_threshold: float = 0.3
    ) -> Dict[str, Any]:
        """
        获取知识层统计信息
        
        Args:
            connector: Neo4j 连接器
            end_user_id: 组ID（可选）
            forgetting_threshold: 遗忘阈值
        
        Returns:
            dict: 统计信息字典
        """
        # 构建查询
        query = """
        MATCH (n)
        WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary)
        """
        
        if end_user_id:
            query += " AND n.end_user_id = $end_user_id"
        
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
        if end_user_id:
            params['end_user_id'] = end_user_id
        
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
    
    async def _get_pending_forgetting_nodes(
        self,
        connector: Neo4jConnector,
        end_user_id: str,
        forgetting_threshold: float,
        min_days_since_access: int,
        limit: int = 20
    ) -> list[Dict[str, Any]]:
        """
        获取待遗忘节点列表
        
        查询满足遗忘条件的节点（激活值低于阈值且最后访问时间超过最小天数）
        
        Args:
            connector: Neo4j 连接器
            end_user_id: 组ID
            forgetting_threshold: 遗忘阈值
            min_days_since_access: 最小未访问天数
            limit: 返回节点数量限制
        
        Returns:
            list: 待遗忘节点列表
        """
        from datetime import timedelta
        
        # 计算最小访问时间（ISO 8601 格式字符串，使用 UTC 时区）
        min_access_time = datetime.now(timezone.utc) - timedelta(days=min_days_since_access)
        min_access_time_str = min_access_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        
        query = """
        MATCH (n)
        WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary)
          AND n.end_user_id = $end_user_id
          AND n.activation_value IS NOT NULL
          AND n.activation_value < $threshold
          AND n.last_access_time IS NOT NULL
          AND datetime(n.last_access_time) < datetime($min_access_time_str)
        RETURN 
          elementId(n) as node_id,
          labels(n)[0] as node_type,
          CASE 
            WHEN n:Statement THEN n.statement
            WHEN n:ExtractedEntity THEN n.name
            WHEN n:MemorySummary THEN n.content
            ELSE ''
          END as content_summary,
          n.activation_value as activation_value,
          n.last_access_time as last_access_time
        ORDER BY n.activation_value ASC
        LIMIT $limit
        """
        
        params = {
            'end_user_id': end_user_id,
            'threshold': forgetting_threshold,
            'min_access_time_str': min_access_time_str,
            'limit': limit
        }
        
        results = await connector.execute_query(query, **params)
        
        pending_nodes = []
        for result in results:
            # 将节点类型标签转换为小写
            node_type_label = result['node_type'].lower()
            if node_type_label == 'extractedentity':
                node_type_label = 'entity'
            elif node_type_label == 'memorysummary':
                node_type_label = 'summary'
            
            # 将 Neo4j DateTime 对象转换为时间戳（毫秒）
            last_access_time = result['last_access_time']
            last_access_dt = convert_neo4j_datetime_to_python(last_access_time)
            # 确保 datetime 带有时区信息(假定为 UTC),避免 naive datetime 导致的时区偏差
            if last_access_dt:
                if last_access_dt.tzinfo is None:
                    last_access_dt = last_access_dt.replace(tzinfo=timezone.utc)
                last_access_timestamp = int(last_access_dt.timestamp() * 1000)
            else:
                last_access_timestamp = 0
            
            pending_nodes.append({
                'node_id': str(result['node_id']),
                'node_type': node_type_label,
                'content_summary': result['content_summary'] or '',
                'activation_value': result['activation_value'],
                'last_access_time': last_access_timestamp
            })
        
        return pending_nodes
    
    async def trigger_forgetting_cycle(
        self,
        db: Session,
        end_user_id: str,
        max_merge_batch_size: Optional[int] = None,
        min_days_since_access: Optional[int] = None,
        config_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        手动触发遗忘周期
        
        执行一次完整的遗忘周期，识别并融合低激活值节点。
        
        Args:
            db: 数据库会话
            end_user_id: 组ID（即终端用户ID，必填）
            max_merge_batch_size: 最大融合批次大小（可选）
            min_days_since_access: 最小未访问天数（可选）
            config_id: 配置ID（必填，由控制器层通过 end_user_id 获取）
        
        Returns:
            dict: 遗忘报告
        """
        # 获取遗忘引擎组件
        _, _, forgetting_scheduler, config = await self._get_forgetting_components(db, config_id)
        
        # 记录执行开始时间
        execution_time = datetime.now()
        
        # 运行遗忘周期（LLM 客户端将在需要时由 forgetting_strategy 内部获取）
        report = await forgetting_scheduler.run_forgetting_cycle(
            end_user_id=end_user_id,
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
        
        # 获取当前的激活值统计（用于记录历史）
        try:
            connector = forgetting_scheduler.connector
            stats_query = """
            MATCH (n)
            WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary OR n:Chunk)
              AND n.end_user_id = $end_user_id
            RETURN 
                count(n) as total_nodes,
                avg(n.activation_value) as average_activation,
                sum(CASE WHEN n.activation_value IS NOT NULL AND n.activation_value < $threshold THEN 1 ELSE 0 END) as low_activation_nodes
            """
            
            stats_results = await connector.execute_query(
                stats_query,
                end_user_id=end_user_id,
                threshold=config['forgetting_threshold']
            )
            
            if stats_results:
                stats = stats_results[0]
                total_nodes = stats['total_nodes'] or 0
                average_activation = stats['average_activation']
                low_activation_nodes = stats['low_activation_nodes'] or 0
            else:
                total_nodes = 0
                average_activation = None
                low_activation_nodes = 0
            
            # 保存历史记录到数据库
            self.history_repository.create(
                db=db,
                end_user_id=end_user_id,
                execution_time=execution_time,
                merged_count=report['merged_count'],
                failed_count=report['failed_count'],
                average_activation_value=average_activation,
                total_nodes=total_nodes,
                low_activation_nodes=low_activation_nodes,
                duration_seconds=report['duration_seconds'],
                trigger_type='manual'
            )
            
            api_logger.info(
                f"已保存遗忘周期历史记录: end_user_id={end_user_id}, "
                f"merged_count={report['merged_count']}"
            )
        
        except Exception as e:
            # 记录历史失败不应影响主流程
            api_logger.error(f"保存遗忘周期历史记录失败: {str(e)}")
        
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
        end_user_id: Optional[str] = None,
        config_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        获取遗忘引擎统计信息
        
        返回知识层节点统计、激活值分布等信息。
        
        Args:
            db: 数据库会话
            end_user_id: 组ID（可选）
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
        
        if end_user_id:
            activation_query += " AND n.end_user_id = $end_user_id"
        
        activation_query += """
        RETURN 
            count(n) as total_nodes,
            sum(CASE WHEN n.activation_value IS NOT NULL THEN 1 ELSE 0 END) as nodes_with_activation,
            sum(CASE WHEN n.activation_value IS NULL THEN 1 ELSE 0 END) as nodes_without_activation,
            avg(n.activation_value) as average_activation,
            sum(CASE WHEN n.activation_value IS NOT NULL AND n.activation_value < $threshold THEN 1 ELSE 0 END) as low_activation_nodes
        """
        
        params = {'threshold': forgetting_threshold}
        if end_user_id:
            params['end_user_id'] = end_user_id
        
        activation_results = await connector.execute_query(activation_query, **params)
        
        if activation_results:
            result = activation_results[0]
            activation_metrics = {
                'total_nodes': result['total_nodes'] or 0,
                'nodes_with_activation': result['nodes_with_activation'] or 0,
                'nodes_without_activation': result['nodes_without_activation'] or 0,
                'average_activation_value': result['average_activation'],
                'low_activation_nodes': result['low_activation_nodes'] or 0,
                'forgetting_threshold': forgetting_threshold,
                'timestamp': int(datetime.now().timestamp() * 1000)
            }
        else:
            activation_metrics = {
                'total_nodes': 0,
                'nodes_with_activation': 0,
                'nodes_without_activation': 0,
                'average_activation_value': None,
                'low_activation_nodes': 0,
                'forgetting_threshold': forgetting_threshold,
                'timestamp': int(datetime.now().timestamp() * 1000)
            }
        
        # 收集节点类型分布
        distribution_query = """
        MATCH (n)
        WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary OR n:Chunk)
        """
        
        if end_user_id:
            distribution_query += " AND n.end_user_id = $end_user_id"
        
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
        if end_user_id:
            dist_params['end_user_id'] = end_user_id
        
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
        
        # 获取最近7个日期的历史趋势数据（每天取最后一次执行）
        recent_trends = []
        try:
            if end_user_id:
                # 查询所有历史记录
                history_records = self.history_repository.get_recent_by_end_user(
                    db=db,
                    end_user_id=end_user_id
                )
                
                # 按日期分组（一天可能有多次执行，取最后一次）
                from collections import OrderedDict
                daily_records = OrderedDict()
                
                # 遍历记录（已按时间降序），每个日期只保留第一次遇到的（即最后一次执行）
                for record in history_records:
                    # 提取日期（格式: "1/1", "1/2"）- 跨平台兼容
                    month = record.execution_time.month
                    day = record.execution_time.day
                    date_str = f"{month}/{day}"
                    
                    # 如果这个日期还没有记录，添加它（这是该日期最后一次执行）
                    if date_str not in daily_records:
                        daily_records[date_str] = record
                    
                    # 如果已经有7个不同的日期，停止
                    if len(daily_records) >= 7:
                        break
                
                # 构建趋势数据点（按时间从旧到新排序）
                sorted_dates = sorted(
                    daily_records.items(),
                    key=lambda x: x[1].execution_time
                )
                
                for date_str, record in sorted_dates:
                    recent_trends.append({
                        'date': date_str,
                        'merged_count': record.merged_count,
                        'average_activation': record.average_activation_value,
                        'total_nodes': record.total_nodes,
                        'execution_time': int(record.execution_time.timestamp() * 1000)
                    })
                
                api_logger.info(f"成功获取最近 {len(recent_trends)} 个日期的历史趋势数据")
            
        except Exception as e:
            api_logger.error(f"获取历史趋势数据失败: {str(e)}")
            # 失败时返回空列表，不影响主流程
        
        # 获取待遗忘节点列表（前20个满足遗忘条件的节点）
        pending_nodes = []
        try:
            if end_user_id:
                # 验证 min_days_since_access 配置值
                min_days = config.get('min_days_since_access')
                if min_days is None or not isinstance(min_days, (int, float)) or min_days < 0:
                    api_logger.warning(
                        f"min_days_since_access 配置无效: {min_days}, 使用默认值 7"
                    )
                    min_days = 7
                
                pending_nodes = await self._get_pending_forgetting_nodes(
                    connector=connector,
                    end_user_id=end_user_id,
                    forgetting_threshold=forgetting_threshold,
                    min_days_since_access=int(min_days),
                    limit=20
                )
                
                api_logger.info(f"成功获取 {len(pending_nodes)} 个待遗忘节点")
        
        except Exception as e:
            api_logger.error(f"获取待遗忘节点失败: {str(e)}")
            # 失败时返回空列表，不影响主流程
        
        # 构建统计信息
        stats = {
            'activation_metrics': activation_metrics,
            'node_distribution': node_distribution,
            'recent_trends': recent_trends,
            'pending_nodes': pending_nodes,
            'timestamp': int(datetime.now().timestamp() * 1000)
        }
        
        api_logger.info(
            f"成功获取遗忘引擎统计: total_nodes={stats['activation_metrics']['total_nodes']}, "
            f"low_activation_nodes={stats['activation_metrics']['low_activation_nodes']}, "
            f"trend_days={len(recent_trends)}, pending_nodes={len(pending_nodes)}"
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
