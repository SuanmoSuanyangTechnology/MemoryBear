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
    - 原子性更新：使用 APOC 原子操作确保并发安全
    - 批次内合并：同一批次中对同一节点的多次访问合并为一次更新
    - 一致性保证：提供一致性检查和自动修复功能
    - 智能修剪：自动修剪过长的访问历史
    
    Attributes:
        connector: Neo4j连接器实例
        actr_calculator: ACT-R激活值计算器实例
    """
    
    def __init__(
        self,
        connector: Neo4jConnector,
        actr_calculator: ACTRCalculator,
        max_retries: int = 5
    ):
        """
        初始化访问历史管理器
        
        Args:
            connector: Neo4j连接器实例
            actr_calculator: ACT-R激活值计算器实例
            max_retries: 已废弃，保留参数兼容性（APOC 原子操作无需重试）
        """
        self.connector = connector
        self.actr_calculator = actr_calculator

    async def record_access(
        self,
        node_id: str,
        node_label: str,
        end_user_id: Optional[str] = None,
        current_time: Optional[datetime] = None,
        access_times: int = 1
    ) -> Dict[str, Any]:
        """
        记录节点访问并原子性更新所有相关字段
        
        Args:
            node_id: 节点ID
            node_label: 节点标签（Statement, ExtractedEntity, MemorySummary）
            end_user_id: 组ID（可选，用于过滤）
            current_time: 当前时间（可选，默认使用系统时间）
            access_times: 本次访问次数（默认1，批量合并时可能大于1）
        
        Returns:
            Dict[str, Any]: 更新后的节点数据
        
        Raises:
            ValueError: 如果节点不存在或节点标签无效
            RuntimeError: 如果更新失败
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
        
        try:
            # 步骤1：读取当前节点状态
            node_data = await self._fetch_node(node_id, node_label, end_user_id)
            
            if not node_data:
                raise ValueError(
                    f"Node not found: {node_label} with id={node_id}"
                )
            
            # 步骤2：计算新的访问历史和激活值
            update_data = await self._calculate_update(
                node_data=node_data,
                current_time=current_time,
                current_time_iso=current_time_iso,
                access_times=access_times
            )
            
            # 步骤3：使用 APOC 原子操作更新节点（无需重试）
            updated_node = await self._atomic_update(
                node_id=node_id,
                node_label=node_label,
                update_data=update_data,
                end_user_id=end_user_id
            )
            
            logger.info(
                f"成功记录访问: {node_label}[{node_id}], "
                f"activation={update_data['activation_value']:.4f}, "
                f"access_count={update_data['access_count']}"
                f"{f', 合并访问次数={access_times}' if access_times > 1 else ''}"
            )
            
            return updated_node
            
        except Exception as e:
            logger.error(
                f"访问记录失败: {node_label}[{node_id}], 错误: {str(e)}"
            )
            raise RuntimeError(
                f"Failed to record access: {str(e)}"
            ) from e

    async def record_batch_access(
        self,
        node_ids: List[str],
        node_label: str,
        end_user_id: Optional[str] = None,
        current_time: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        批量记录多个节点的访问
        
        对同一个节点的多次访问会先在内存中合并，只发起一次更新。
        
        Args:
            node_ids: 节点ID列表（可包含重复ID）
            node_label: 节点标签（所有节点必须是同一类型）
            end_user_id: 组ID（可选）
            current_time: 当前时间（可选）
        
        Returns:
            List[Dict[str, Any]]: 成功更新的节点列表
        """
        import time
        batch_start = time.time()
        
        if current_time is None:
            current_time = datetime.now()
        
        # 合并同一节点的访问次数，避免对同一节点并发写入
        access_count_map: Dict[str, int] = {}
        for node_id in node_ids:
            access_count_map[node_id] = access_count_map.get(node_id, 0) + 1
        
        merged_count = len(node_ids) - len(access_count_map)
        if merged_count > 0:
            logger.info(
                f"批量访问合并: 原始={len(node_ids)}, "
                f"去重后={len(access_count_map)}, 合并={merged_count}"
            )
        
        # 对去重后的节点并行发起更新
        tasks = []
        for node_id, access_times in access_count_map.items():
            task = self.record_access(
                node_id=node_id,
                node_label=node_label,
                end_user_id=end_user_id,
                current_time=current_time,
                access_times=access_times
            )
            tasks.append((node_id, task))
        
        task_results = await asyncio.gather(
            *[t for _, t in tasks], return_exceptions=True
        )
        
        results = []
        failed_count = 0
        
        for (node_id, _), result in zip(tasks, task_results):
            if isinstance(result, Exception):
                failed_count += 1
                logger.warning(
                    f"批量访问记录失败: {node_label}[{node_id}], 错误: {str(result)}"
                )
            else:
                results.append(result)
        
        batch_duration = time.time() - batch_start
        logger.info(
            f"[PERF] 批量访问记录完成: 成功 {len(results)}/{len(access_count_map)}, "
            f"失败 {failed_count}, 耗时 {batch_duration:.4f}s"
        )
        
        return results

    async def check_consistency(
        self,
        node_id: str,
        node_label: str,
        end_user_id: Optional[str] = None
    ) -> Tuple[ConsistencyCheckResult, Optional[str]]:
        """
        检查节点数据的一致性
        """
        node_data = await self._fetch_node(node_id, node_label, end_user_id)
        
        if not node_data:
            return ConsistencyCheckResult.CONSISTENT, None
        
        access_history = node_data.get('access_history') or []
        last_access_time = node_data.get('last_access_time')
        access_count = node_data.get('access_count', 0)
        activation_value = node_data.get('activation_value')
        
        if access_history and last_access_time:
            if access_history[-1] != last_access_time:
                return (
                    ConsistencyCheckResult.INCONSISTENT_HISTORY_TIME,
                    f"access_history[-1]={access_history[-1]} != "
                    f"last_access_time={last_access_time}"
                )
        
        if len(access_history) != access_count:
            return (
                ConsistencyCheckResult.INCONSISTENT_HISTORY_COUNT,
                f"len(access_history)={len(access_history)} != "
                f"access_count={access_count}"
            )
        
        if access_history and activation_value is None:
            return (
                ConsistencyCheckResult.MISSING_ACTIVATION,
                "Node has access_history but activation_value is None"
            )
        
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
        end_user_id: Optional[str] = None,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """批量检查多个节点的一致性"""
        query = f"""
        MATCH (n:{node_label})
        WHERE n.access_history IS NOT NULL
        """
        if end_user_id:
            query += " AND n.end_user_id = $end_user_id"
        query += """
        RETURN n.id as id
        LIMIT $limit
        """
        
        params = {"limit": limit}
        if end_user_id:
            params["end_user_id"] = end_user_id
        
        results = await self.connector.execute_query(query, **params)
        node_ids = [r['id'] for r in results]
        
        inconsistencies = []
        consistent_count = 0
        
        for node_id in node_ids:
            result, message = await self.check_consistency(
                node_id=node_id,
                node_label=node_label,
                end_user_id=end_user_id
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
        end_user_id: Optional[str] = None
    ) -> bool:
        """自动修复节点的数据不一致问题"""
        try:
            result, message = await self.check_consistency(
                node_id=node_id,
                node_label=node_label,
                end_user_id=end_user_id
            )
            
            if result == ConsistencyCheckResult.CONSISTENT:
                logger.info(f"节点数据一致，无需修复: {node_label}[{node_id}]")
                return True
            
            node_data = await self._fetch_node(node_id, node_label, end_user_id)
            if not node_data:
                logger.error(f"节点不存在，无法修复: {node_label}[{node_id}]")
                return False
            
            access_history = node_data.get('access_history') or []
            importance_score = node_data.get('importance_score', 0.5)
            
            repair_data = {}
            
            if access_history:
                repair_data['last_access_time'] = access_history[-1]
            
            repair_data['access_count'] = len(access_history)
            
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
            
            query = f"""
            MATCH (n:{node_label} {{id: $node_id}})
            """
            if end_user_id:
                query += " WHERE n.end_user_id = $end_user_id"
            query += """
            SET n += $repair_data
            RETURN n
            """
            
            params = {
                'node_id': node_id,
                'repair_data': repair_data
            }
            if end_user_id:
                params['end_user_id'] = end_user_id
            
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
        end_user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """获取节点数据"""
        query = f"""
        MATCH (n:{node_label} {{id: $node_id}})
        """
        if end_user_id:
            query += " WHERE n.end_user_id = $end_user_id"
        query += """
        RETURN n.id as id,
               n.importance_score as importance_score,
               n.activation_value as activation_value,
               n.access_history as access_history,
               n.last_access_time as last_access_time,
               n.access_count as access_count
        """
        
        params = {'node_id': node_id}
        if end_user_id:
            params['end_user_id'] = end_user_id
        
        results = await self.connector.execute_query(query, **params)
        
        if results:
            return results[0]
        return None

    async def _calculate_update(
        self,
        node_data: Dict[str, Any],
        current_time: datetime,
        current_time_iso: str,
        access_times: int = 1
    ) -> Dict[str, Any]:
        """
        计算更新数据
        
        Args:
            node_data: 当前节点数据
            current_time: 当前时间（datetime对象）
            current_time_iso: 当前时间（ISO格式字符串）
            access_times: 本次访问次数（合并后可能大于1）
        
        Returns:
            Dict[str, Any]: 更新数据
        """
        importance_score = node_data.get('importance_score')
        if importance_score is None:
            importance_score = 0.5
        
        # 本次新增的时间戳
        new_timestamps = [current_time_iso] * access_times
        
        # 仅用本次新增的访问记录计算激活值
        new_history_dt = [current_time] * access_times
        trimmed_history_dt = self.actr_calculator.trim_access_history(
            access_history=new_history_dt,
            current_time=current_time
        )
        
        activation_value = self.actr_calculator.calculate_memory_activation(
            access_history=trimmed_history_dt,
            current_time=current_time,
            last_access_time=current_time,
            importance_score=importance_score
        )
        
        return {
            'activation_value': activation_value,
            'new_timestamps': new_timestamps,
            'access_count_delta': access_times,
            'access_count': len(trimmed_history_dt),
            'last_access_time': current_time_iso,
        }

    async def _atomic_update(
        self,
        node_id: str,
        node_label: str,
        update_data: Dict[str, Any],
        end_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        原子性更新节点（使用 APOC 原子操作）
        
        使用 apoc.atomic.add 和 apoc.atomic.insert 保证并发安全，
        无需 version 字段和乐观锁，数据库层面保证原子性。
        
        Args:
            node_id: 节点ID
            node_label: 节点标签
            update_data: 更新数据
            end_user_id: 组ID（可选）
        
        Returns:
            Dict[str, Any]: 更新后的节点数据
        
        Raises:
            RuntimeError: 如果更新失败
        """
        content_field_map = {
            'Statement': 'n.statement as statement',
            'MemorySummary': 'n.content as content',
            'ExtractedEntity': 'null as content_placeholder',
            'Community': 'n.summary as summary'
        }
        
        if node_label not in content_field_map:
            raise ValueError(
                f"Unsupported node_label: {node_label}. "
                f"Supported labels are: {list(content_field_map.keys())}"
            )
        
        content_field = content_field_map[node_label]
        
        where_clause = ""
        if end_user_id:
            where_clause = " AND n.end_user_id = $end_user_id"
        
        query = f"""
        MATCH (n:{node_label} {{id: $node_id}})
        WHERE true{where_clause}
        CALL apoc.atomic.add(n, 'access_count', $access_count_delta, 5) YIELD oldValue AS old_count
        WITH n
        CALL (n) {{
            UNWIND $new_timestamps AS ts
            CALL apoc.atomic.insert(n, 'access_history', size(n.access_history), ts, 5) YIELD oldValue
            RETURN count(*) AS inserted
        }}
        SET n.activation_value = $activation_value,
            n.last_access_time = $last_access_time
        RETURN n.id as id,
               n.activation_value as activation_value,
               n.access_history as access_history,
               n.last_access_time as last_access_time,
               n.access_count as access_count,
               n.importance_score as importance_score,
               {content_field}
        """
        
        params = {
            'node_id': node_id,
            'access_count_delta': update_data['access_count_delta'],
            'new_timestamps': update_data['new_timestamps'],
            'activation_value': update_data['activation_value'],
            'last_access_time': update_data['last_access_time'],
        }
        if end_user_id:
            params['end_user_id'] = end_user_id
        
        try:
            results = await self.connector.execute_query(query, **params)
            
            if not results:
                raise RuntimeError(f"Node not found: {node_label}[{node_id}]")
            
            result_dict = dict(results[0])
            result_dict.pop('content_placeholder', None)
            
            return result_dict
        except Exception as e:
            logger.error(
                f"原子性更新失败: {node_label}[{node_id}], 错误: {str(e)}"
            )
            raise RuntimeError(
                f"Failed to atomically update node: {str(e)}"
            ) from e
