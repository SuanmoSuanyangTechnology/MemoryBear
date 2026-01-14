"""
访问历史管理器模块

本模块实现访问历史的追踪、更新和一致性保证。
负责在知识节点被访问时原子性地更新激活值相关的所有字段。

Classes:
    AccessHistoryManager: 访问历史管理器，提供并发安全的访问记录和一致性检查
"""

import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.core.memory.storage_services.forgetting_engine.actr_calculator import (
    ACTRCalculator,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


class ConsistencyCheckResult(Enum):
    """一致性检查结果枚举"""
    CONSISTENT = "consistent"  # 数据一致
    INCONSISTENT_HISTORY_TIME = "inconsistent_history_time"  # access_history[-1] != last_access_time
    INCONSISTENT_HISTORY_COUNT = "inconsistent_history_count"  # len(access_history) != access_count
    MISSING_ACTIVATION = "missing_activation"  # 有访问历史但无激活值
    INVALID_ACTIVATION_RANGE = "invalid_activation_range"  # 激活值超出有效范围


class AccessHistoryManager:
    """
    访问历史管理器
    
    负责追踪知识节点的访问历史，并在访问时原子性地更新所有相关字段：
    - activation_value: 激活值
    - access_history: 访问历史时间戳数组
    - last_access_time: 最后访问时间
    - access_count: 访问次数
    
    特性：
    - 原子性更新：使用Neo4j事务确保所有字段同时更新或回滚
    - 并发安全：使用乐观锁机制防止并发冲突
    - 一致性保证：提供一致性检查和自动修复功能
    - 智能修剪：自动修剪过长的访问历史
    
    Attributes:
        connector: Neo4j连接器实例
        actr_calculator: ACT-R激活值计算器实例
        max_retries: 并发冲突时的最大重试次数
    """
    
    def __init__(
        self,
        connector: Neo4jConnector,
        actr_calculator: ACTRCalculator,
        max_retries: int = 3
    ):
        """
        初始化访问历史管理器
        
        Args:
            connector: Neo4j连接器实例
            actr_calculator: ACT-R激活值计算器实例
            max_retries: 并发冲突时的最大重试次数（默认3次）
        """
        self.connector = connector
        self.actr_calculator = actr_calculator
        self.max_retries = max_retries
    
    async def record_access(
        self,
        node_id: str,
        node_label: str,
        group_id: Optional[str] = None,
        current_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        记录节点访问并原子性更新所有相关字段
        
        这是核心方法，实现了：
        1. 首次访问：初始化access_history，计算初始激活值
        2. 后续访问：追加访问历史，重新计算激活值
        3. 历史修剪：当历史过长时自动修剪
        4. 原子性：所有字段在单个事务中更新
        5. 并发安全：使用乐观锁重试机制
        
        Args:
            node_id: 节点ID
            node_label: 节点标签（Statement, ExtractedEntity, MemorySummary）
            group_id: 组ID（可选，用于过滤）
            current_time: 当前时间（可选，默认使用系统时间）
        
        Returns:
            Dict[str, Any]: 更新后的节点数据，包含：
                - id: 节点ID
                - activation_value: 更新后的激活值
                - access_history: 更新后的访问历史
                - last_access_time: 最后访问时间
                - access_count: 访问次数
                - importance_score: 重要性分数
        
        Raises:
            ValueError: 如果节点不存在或节点标签无效
            RuntimeError: 如果重试次数耗尽仍然失败
        """
        if current_time is None:
            current_time = datetime.now()
        
        current_time_iso = current_time.isoformat()
        
        # 验证节点标签
        valid_labels = ["Statement", "ExtractedEntity", "MemorySummary"]
        if node_label not in valid_labels:
            raise ValueError(
                f"Invalid node_label: {node_label}. Must be one of {valid_labels}"
            )
        
        # 使用乐观锁重试机制处理并发冲突
        for attempt in range(self.max_retries):
            try:
                # 步骤1：读取当前节点状态
                node_data = await self._fetch_node(node_id, node_label, group_id)
                
                if not node_data:
                    raise ValueError(
                        f"Node not found: {node_label} with id={node_id}"
                    )
                
                # 步骤2：计算新的访问历史和激活值
                update_data = await self._calculate_update(
                    node_data=node_data,
                    current_time=current_time,
                    current_time_iso=current_time_iso
                )
                
                # 步骤3：原子性更新节点（使用事务）
                updated_node = await self._atomic_update(
                    node_id=node_id,
                    node_label=node_label,
                    update_data=update_data,
                    group_id=group_id
                )
                
                logger.info(
                    f"成功记录访问: {node_label}[{node_id}], "
                    f"activation={update_data['activation_value']:.4f}, "
                    f"access_count={update_data['access_count']}"
                )
                
                return updated_node
                
            except Exception as e:
                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"访问记录失败（尝试 {attempt + 1}/{self.max_retries}）: {str(e)}"
                    )
                    continue
                else:
                    logger.error(
                        f"访问记录失败，重试次数耗尽: {node_label}[{node_id}], "
                        f"错误: {str(e)}"
                    )
                    raise RuntimeError(
                        f"Failed to record access after {self.max_retries} attempts: {str(e)}"
                    )
    
    async def record_batch_access(
        self,
        node_ids: List[str],
        node_label: str,
        group_id: Optional[str] = None,
        current_time: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        批量记录多个节点的访问
        
        为提高性能，批量更新多个节点的访问历史。
        每个节点独立更新，失败的节点不影响其他节点。
        
        Args:
            node_ids: 节点ID列表
            node_label: 节点标签（所有节点必须是同一类型）
            group_id: 组ID（可选）
            current_time: 当前时间（可选）
        
        Returns:
            List[Dict[str, Any]]: 成功更新的节点列表
        """
        import time
        batch_start = time.time()
        
        if current_time is None:
            current_time = datetime.now()
        
        # PERFORMANCE FIX: Process all nodes in parallel instead of sequentially
        tasks = []
        for node_id in node_ids:
            task = self.record_access(
                node_id=node_id,
                node_label=node_label,
                group_id=group_id,
                current_time=current_time
            )
            tasks.append(task)
        
        # Execute all tasks in parallel
        task_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect successful results and count failures
        results = []
        failed_count = 0
        
        for node_id, result in zip(node_ids, task_results):
            if isinstance(result, Exception):
                failed_count += 1
                logger.warning(
                    f"批量访问记录失败: {node_label}[{node_id}], 错误: {str(result)}"
                )
            else:
                results.append(result)
        
        batch_duration = time.time() - batch_start
        logger.info(
            f"[PERF] 批量访问记录完成: 成功 {len(results)}/{len(node_ids)}, "
            f"失败 {failed_count}, 耗时 {batch_duration:.4f}s"
        )
        
        return results
    
    async def check_consistency(
        self,
        node_id: str,
        node_label: str,
        group_id: Optional[str] = None
    ) -> Tuple[ConsistencyCheckResult, Optional[str]]:
        """
        检查节点数据的一致性
        
        验证以下一致性规则：
        1. access_history[-1] == last_access_time
        2. len(access_history) == access_count
        3. 如果有访问历史，必须有激活值
        4. 激活值必须在有效范围内 [offset, 1.0]
        
        Args:
            node_id: 节点ID
            node_label: 节点标签
            group_id: 组ID（可选）
        
        Returns:
            Tuple[ConsistencyCheckResult, Optional[str]]: 
                - 一致性检查结果枚举
                - 错误描述（如果不一致）
        """
        node_data = await self._fetch_node(node_id, node_label, group_id)
        
        if not node_data:
            return ConsistencyCheckResult.CONSISTENT, None
        
        access_history = node_data.get('access_history') or []
        last_access_time = node_data.get('last_access_time')
        access_count = node_data.get('access_count', 0)
        activation_value = node_data.get('activation_value')
        
        # 检查1：access_history[-1] == last_access_time
        if access_history and last_access_time:
            if access_history[-1] != last_access_time:
                return (
                    ConsistencyCheckResult.INCONSISTENT_HISTORY_TIME,
                    f"access_history[-1]={access_history[-1]} != "
                    f"last_access_time={last_access_time}"
                )
        
        # 检查2：len(access_history) == access_count
        if len(access_history) != access_count:
            return (
                ConsistencyCheckResult.INCONSISTENT_HISTORY_COUNT,
                f"len(access_history)={len(access_history)} != "
                f"access_count={access_count}"
            )
        
        # 检查3：有访问历史必须有激活值
        if access_history and activation_value is None:
            return (
                ConsistencyCheckResult.MISSING_ACTIVATION,
                "Node has access_history but activation_value is None"
            )
        
        # 检查4：激活值范围
        if activation_value is not None:
            offset = self.actr_calculator.offset
            if not (offset <= activation_value <= 1.0):
                return (
                    ConsistencyCheckResult.INVALID_ACTIVATION_RANGE,
                    f"activation_value={activation_value} out of range "
                    f"[{offset}, 1.0]"
                )
        
        return ConsistencyCheckResult.CONSISTENT, None
    
    async def check_batch_consistency(
        self,
        node_label: str,
        group_id: Optional[str] = None,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        批量检查多个节点的一致性
        
        Args:
            node_label: 节点标签
            group_id: 组ID（可选）
            limit: 检查的最大节点数
        
        Returns:
            Dict[str, Any]: 一致性检查报告，包含：
                - total_checked: 检查的节点总数
                - consistent_count: 一致的节点数
                - inconsistent_count: 不一致的节点数
                - inconsistencies: 不一致节点的详细信息列表
                - consistency_rate: 一致性率（0-1）
        """
        # 查询所有相关节点
        query = f"""
        MATCH (n:{node_label})
        WHERE n.access_history IS NOT NULL
        """
        if group_id:
            query += " AND n.group_id = $group_id"
        query += """
        RETURN n.id as id
        LIMIT $limit
        """
        
        params = {"limit": limit}
        if group_id:
            params["group_id"] = group_id
        
        results = await self.connector.execute_query(query, **params)
        node_ids = [r['id'] for r in results]
        
        # 检查每个节点
        inconsistencies = []
        consistent_count = 0
        
        for node_id in node_ids:
            result, message = await self.check_consistency(
                node_id=node_id,
                node_label=node_label,
                group_id=group_id
            )
            
            if result == ConsistencyCheckResult.CONSISTENT:
                consistent_count += 1
            else:
                inconsistencies.append({
                    'node_id': node_id,
                    'result': result.value,
                    'message': message
                })
        
        total_checked = len(node_ids)
        inconsistent_count = len(inconsistencies)
        consistency_rate = consistent_count / total_checked if total_checked > 0 else 1.0
        
        report = {
            'total_checked': total_checked,
            'consistent_count': consistent_count,
            'inconsistent_count': inconsistent_count,
            'inconsistencies': inconsistencies,
            'consistency_rate': consistency_rate
        }
        
        logger.info(
            f"一致性检查完成: {node_label}, "
            f"一致率={consistency_rate:.2%}, "
            f"不一致节点={inconsistent_count}/{total_checked}"
        )
        
        return report
    
    async def repair_inconsistency(
        self,
        node_id: str,
        node_label: str,
        group_id: Optional[str] = None
    ) -> bool:
        """
        自动修复节点的数据不一致问题
        
        修复策略：
        1. 如果access_history[-1] != last_access_time：使用access_history[-1]
        2. 如果len(access_history) != access_count：使用len(access_history)
        3. 如果有历史但无激活值：重新计算激活值
        4. 如果激活值超出范围：重新计算激活值
        
        Args:
            node_id: 节点ID
            node_label: 节点标签
            group_id: 组ID（可选）
        
        Returns:
            bool: 修复成功返回True，否则返回False
        """
        try:
            # 检查一致性
            result, message = await self.check_consistency(
                node_id=node_id,
                node_label=node_label,
                group_id=group_id
            )
            
            if result == ConsistencyCheckResult.CONSISTENT:
                logger.info(f"节点数据一致，无需修复: {node_label}[{node_id}]")
                return True
            
            # 获取节点数据
            node_data = await self._fetch_node(node_id, node_label, group_id)
            if not node_data:
                logger.error(f"节点不存在，无法修复: {node_label}[{node_id}]")
                return False
            
            access_history = node_data.get('access_history') or []
            importance_score = node_data.get('importance_score', 0.5)
            
            # 准备修复数据
            repair_data = {}
            
            # 修复last_access_time
            if access_history:
                repair_data['last_access_time'] = access_history[-1]
            
            # 修复access_count
            repair_data['access_count'] = len(access_history)
            
            # 修复activation_value
            if access_history:
                current_time = datetime.now()
                last_access_dt = datetime.fromisoformat(access_history[-1])
                access_history_dt = [
                    datetime.fromisoformat(ts) for ts in access_history
                ]
                
                activation_value = self.actr_calculator.calculate_memory_activation(
                    access_history=access_history_dt,
                    current_time=current_time,
                    last_access_time=last_access_dt,
                    importance_score=importance_score
                )
                repair_data['activation_value'] = activation_value
            
            # 执行修复
            query = f"""
            MATCH (n:{node_label} {{id: $node_id}})
            """
            if group_id:
                query += " WHERE n.group_id = $group_id"
            query += """
            SET n += $repair_data
            RETURN n
            """
            
            params = {
                'node_id': node_id,
                'repair_data': repair_data
            }
            if group_id:
                params['group_id'] = group_id
            
            await self.connector.execute_query(query, **params)
            
            logger.info(
                f"成功修复节点不一致: {node_label}[{node_id}], "
                f"问题类型={result.value}"
            )
            return True
            
        except Exception as e:
            logger.error(
                f"修复节点失败: {node_label}[{node_id}], 错误: {str(e)}"
            )
            return False
    
    # ==================== 私有辅助方法 ====================
    
    async def _fetch_node(
        self,
        node_id: str,
        node_label: str,
        group_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取节点数据
        
        Args:
            node_id: 节点ID
            node_label: 节点标签
            group_id: 组ID（可选）
        
        Returns:
            Optional[Dict[str, Any]]: 节点数据，如果不存在返回None
        """
        query = f"""
        MATCH (n:{node_label} {{id: $node_id}})
        """
        if group_id:
            query += " WHERE n.group_id = $group_id"
        query += """
        RETURN n.id as id,
               n.importance_score as importance_score,
               n.activation_value as activation_value,
               n.access_history as access_history,
               n.last_access_time as last_access_time,
               n.access_count as access_count
        """
        
        params = {'node_id': node_id}
        if group_id:
            params['group_id'] = group_id
        
        results = await self.connector.execute_query(query, **params)
        
        if results:
            return results[0]
        return None
    
    async def _calculate_update(
        self,
        node_data: Dict[str, Any],
        current_time: datetime,
        current_time_iso: str
    ) -> Dict[str, Any]:
        """
        计算更新数据
        
        Args:
            node_data: 当前节点数据
            current_time: 当前时间（datetime对象）
            current_time_iso: 当前时间（ISO格式字符串）
        
        Returns:
            Dict[str, Any]: 更新数据，包含所有需要更新的字段
        """
        access_history = node_data.get('access_history') or []
        # Handle None importance_score - default to 0.5
        importance_score = node_data.get('importance_score')
        if importance_score is None:
            importance_score = 0.5
        
        # 追加新的访问时间
        new_access_history = access_history + [current_time_iso]
        
        # 修剪访问历史（如果过长）
        access_history_dt = [
            datetime.fromisoformat(ts) for ts in new_access_history
        ]
        trimmed_history_dt = self.actr_calculator.trim_access_history(
            access_history=access_history_dt,
            current_time=current_time
        )
        trimmed_history = [ts.isoformat() for ts in trimmed_history_dt]
        
        # 计算新的激活值
        activation_value = self.actr_calculator.calculate_memory_activation(
            access_history=trimmed_history_dt,
            current_time=current_time,
            last_access_time=current_time,  # 最后访问时间就是当前时间
            importance_score=importance_score
        )
        
        # 返回所有需要更新的字段
        return {
            'activation_value': activation_value,
            'access_history': trimmed_history,
            'last_access_time': current_time_iso,
            'access_count': len(trimmed_history)
        }
    
    async def _atomic_update(
        self,
        node_id: str,
        node_label: str,
        update_data: Dict[str, Any],
        group_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        原子性更新节点（使用乐观锁）
        
        使用Neo4j事务和版本号确保所有字段同时更新或回滚。
        实现乐观锁机制防止并发冲突。
        
        Args:
            node_id: 节点ID
            node_label: 节点标签
            update_data: 更新数据
            group_id: 组ID（可选）
        
        Returns:
            Dict[str, Any]: 更新后的节点数据
        
        Raises:
            RuntimeError: 如果更新失败或发生版本冲突
        """
        # 定义事务函数
        async def update_transaction(tx, node_id, node_label, update_data, group_id):
            # 步骤1：读取当前节点并获取版本号
            read_query = f"""
            MATCH (n:{node_label} {{id: $node_id}})
            """
            if group_id:
                read_query += " WHERE n.group_id = $group_id"
            read_query += """
            RETURN n.id as id,
                   n.version as version,
                   n.activation_value as activation_value,
                   n.access_history as access_history,
                   n.last_access_time as last_access_time,
                   n.access_count as access_count,
                   n.importance_score as importance_score
            """
            
            read_params = {'node_id': node_id}
            if group_id:
                read_params['group_id'] = group_id
            
            read_result = await tx.run(read_query, **read_params)
            current_node = await read_result.single()
            
            if not current_node:
                raise RuntimeError(f"Node not found: {node_label}[{node_id}]")
            
            # 获取当前版本号（如果不存在则为0）
            current_version = current_node.get('version', 0) or 0
            new_version = current_version + 1
            
            # 步骤2：使用乐观锁更新节点
            # 根据节点类型构建完整的查询语句
            content_field_map = {
                'Statement': 'n.statement as statement',
                'MemorySummary': 'n.content as content',
                'ExtractedEntity': 'null as content_placeholder'  # 占位符，后续会被过滤
            }
            
            # 显式检查节点类型，不支持的类型抛出错误
            if node_label not in content_field_map:
                raise ValueError(
                    f"Unsupported node_label: {node_label}. "
                    f"Supported labels are: {list(content_field_map.keys())}"
                )
            
            content_field = content_field_map[node_label]
            
            # 构建 WHERE 子句
            where_conditions = []
            if group_id:
                where_conditions.append("n.group_id = $group_id")
            
            # 添加版本检查
            if current_version > 0:
                where_conditions.append("n.version = $current_version")
            else:
                where_conditions.append("(n.version IS NULL OR n.version = 0)")
            
            where_clause = " AND ".join(where_conditions) if where_conditions else "true"
            
            # 构建完整的更新查询
            update_query = f"""
            MATCH (n:{node_label} {{id: $node_id}})
            WHERE {where_clause}
            SET n.activation_value = $activation_value,
                n.access_history = $access_history,
                n.last_access_time = $last_access_time,
                n.access_count = $access_count,
                n.version = $new_version
            RETURN n.id as id,
                   n.activation_value as activation_value,
                   n.access_history as access_history,
                   n.last_access_time as last_access_time,
                   n.access_count as access_count,
                   n.importance_score as importance_score,
                   n.version as version,
                   {content_field}
            """
            
            update_params = {
                'node_id': node_id,
                'current_version': current_version,
                'new_version': new_version,
                'activation_value': update_data['activation_value'],
                'access_history': update_data['access_history'],
                'last_access_time': update_data['last_access_time'],
                'access_count': update_data['access_count']
            }
            if group_id:
                update_params['group_id'] = group_id
            
            update_result = await tx.run(update_query, **update_params)
            updated_node = await update_result.single()
            
            if not updated_node:
                raise RuntimeError(
                    f"Version conflict detected for {node_label}[{node_id}]. "
                    f"Expected version {current_version}, but node was modified by another transaction."
                )
            
            # 转换为字典并移除占位符字段
            result_dict = dict(updated_node)
            result_dict.pop('content_placeholder', None)
            
            return result_dict
        
        # 执行事务
        try:
            result = await self.connector.execute_write_transaction(
                update_transaction,
                node_id=node_id,
                node_label=node_label,
                update_data=update_data,
                group_id=group_id
            )
            return result
        except Exception as e:
            logger.error(
                f"原子性更新失败: {node_label}[{node_id}], 错误: {str(e)}"
            )
            raise RuntimeError(
                f"Failed to atomically update node: {str(e)}"
            ) from e
