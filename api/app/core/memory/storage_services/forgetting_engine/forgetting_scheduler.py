"""
遗忘调度器模块

本模块实现遗忘周期的调度和管理，负责：
1. 手动触发遗忘周期
2. 批量处理可遗忘节点（限制批量大小）
3. 按激活值优先级排序（激活值最低的优先）
4. 进度跟踪和日志记录
5. 生成遗忘报告

注意：定期调度功能已迁移到 Celery Beat，见 app/tasks.py 中的 run_forgetting_cycle_task

Classes:
    ForgettingScheduler: 遗忘调度器，提供遗忘周期管理功能
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from app.core.memory.storage_services.forgetting_engine.forgetting_strategy import ForgettingStrategy
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


logger = logging.getLogger(__name__)


class ForgettingScheduler:
    """
    遗忘调度器
    
    管理遗忘周期的执行，实现批量处理、优先级排序和进度跟踪功能。
    
    核心功能：
    1. 运行遗忘周期：识别可遗忘节点并批量融合
    2. 优先级排序：优先处理激活值最低的节点对
    3. 批量限制：限制单次处理的节点对数量
    4. 进度跟踪：每完成 10% 记录一次日志
    5. 遗忘报告：生成详细的执行报告
    
    注意：定期调度功能已迁移到 Celery Beat 定时任务
    
    Attributes:
        forgetting_strategy: 遗忘策略执行器实例
        connector: Neo4j 连接器实例
        is_running: 是否正在运行遗忘周期
    """
    
    def __init__(
        self,
        forgetting_strategy: ForgettingStrategy,
        connector: Neo4jConnector
    ):
        """
        初始化遗忘调度器
        
        Args:
            forgetting_strategy: 遗忘策略执行器实例
            connector: Neo4j 连接器实例
        """
        self.forgetting_strategy = forgetting_strategy
        self.connector = connector
        self.is_running = False
        
        logger.info("初始化遗忘调度器")
    
    async def run_forgetting_cycle(
        self,
        end_user_id: Optional[str] = None,
        max_merge_batch_size: int = 100,
        min_days_since_access: int = 30,
        config_id: Optional[int] = None,
        db = None
    ) -> Dict[str, Any]:
        """
        运行一次完整的遗忘周期
        
        
        Args:
            end_user_id: 组 ID（可选，用于过滤特定组的节点）
            max_merge_batch_size: 单次最大融合节点对数（默认 100）
            min_days_since_access: 最小未访问天数（默认 30 天）
            config_id: 配置ID（可选，用于获取 llm_id）
            db: 数据库会话（可选，用于获取 llm_id）
        
        Returns:
            Dict[str, Any]: 遗忘报告，包含：
                - merged_count: 融合的节点对数量
                - nodes_before: 遗忘前的节点总数
                - nodes_after: 遗忘后的节点总数
                - reduction_rate: 节点减少率（0-1）
                - duration_seconds: 执行耗时（秒）
                - start_time: 开始时间（ISO 格式）
                - end_time: 结束时间（ISO 格式）
                - failed_count: 失败的融合数量
                - success_rate: 成功率（0-1）
        
        Raises:
            RuntimeError: 如果已有遗忘周期正在运行
        """
        # 检查是否已有遗忘周期在运行
        if self.is_running:
            raise RuntimeError("遗忘周期已在运行中，请等待当前周期完成")
        
        self.is_running = True
        start_time = datetime.now()
        start_time_iso = start_time.isoformat()
        
        logger.info(
            f"开始遗忘周期: end_user_id={end_user_id}, "
            f"max_batch={max_merge_batch_size}, "
            f"min_days={min_days_since_access}"
        )
        
        try:
            # 步骤1：统计遗忘前的节点数量
            nodes_before = await self._count_knowledge_nodes(end_user_id)
            logger.info(f"遗忘前节点总数: {nodes_before}")
            
            # 步骤2：识别可遗忘的节点对
            forgettable_pairs = await self.forgetting_strategy.find_forgettable_nodes(
                end_user_id=end_user_id,
                min_days_since_access=min_days_since_access
            )
            
            total_forgettable = len(forgettable_pairs)
            logger.info(f"识别到 {total_forgettable} 个可遗忘节点对")
            
            if total_forgettable == 0:
                logger.info("没有可遗忘的节点对，遗忘周期结束")
                end_time = datetime.now()
                duration = (end_time - start_time).total_seconds()
                
                report = {
                    'merged_count': 0,
                    'nodes_before': nodes_before,
                    'nodes_after': nodes_before,
                    'reduction_rate': 0.0,
                    'duration_seconds': duration,
                    'start_time': start_time_iso,
                    'end_time': end_time.isoformat(),
                    'failed_count': 0,
                    'success_rate': 1.0
                }
                
                logger.info("没有可遗忘的节点对，遗忘周期结束")
                
                return report
            
            # 步骤3：按激活值排序（激活值最低的优先）
            # avg_activation 已经在 find_forgettable_nodes 中计算并排序
            # 这里只需要确认排序是正确的（升序）
            sorted_pairs = sorted(
                forgettable_pairs,
                key=lambda x: x['avg_activation']
            )
            
            # 步骤4：限制批量大小
            pairs_to_process = sorted_pairs[:max_merge_batch_size]
            actual_batch_size = len(pairs_to_process)
            
            logger.info(
                f"将处理 {actual_batch_size} 个节点对 "
                f"(限制: {max_merge_batch_size})"
            )
            
            # 步骤5：批量融合节点，每 10% 记录进度
            merged_count = 0
            failed_count = 0
            skipped_count = 0  # 跳过的节点对数量（节点已被处理）
            progress_interval = max(1, actual_batch_size // 10)  # 每 10% 记录一次
            
            # 跟踪已处理的节点 ID，避免重复处理
            processed_statement_ids = set()
            processed_entity_ids = set()
            
            # 预先过滤掉重复的节点对
            unique_pairs = []
            for pair in pairs_to_process:
                statement_id = pair['statement_id']
                entity_id = pair['entity_id']
                
                # 如果节点已被标记为处理，跳过
                if statement_id in processed_statement_ids or entity_id in processed_entity_ids:
                    skipped_count += 1
                    logger.debug(
                        f"预过滤：跳过重复节点对 Statement[{statement_id}] + Entity[{entity_id}]"
                    )
                    continue
                
                # 标记节点为已处理
                processed_statement_ids.add(statement_id)
                processed_entity_ids.add(entity_id)
                unique_pairs.append(pair)
            
            logger.info(
                f"预过滤完成：原始 {actual_batch_size} 对，去重后 {len(unique_pairs)} 对，"
                f"跳过 {skipped_count} 对重复节点"
            )
            
            # 更新实际处理的批次大小
            actual_batch_size = len(unique_pairs)
            progress_interval = max(1, actual_batch_size // 10)  # 重新计算进度间隔
            
            for idx, pair in enumerate(unique_pairs, start=1):
                statement_id = pair['statement_id']
                entity_id = pair['entity_id']
                
                try:
                    # 准备节点数据
                    statement_node = {
                        'statement_id': statement_id,
                        'statement_text': pair['statement_text'],
                        'statement_activation': pair['statement_activation'],
                        'statement_importance': pair['statement_importance'],
                        'end_user_id': end_user_id
                    }
                    
                    entity_node = {
                        'entity_id': entity_id,
                        'entity_name': pair['entity_name'],
                        'entity_type': pair['entity_type'],
                        'entity_activation': pair['entity_activation'],
                        'entity_importance': pair['entity_importance'],
                        'end_user_id': end_user_id
                    }
                    
                    # 融合节点
                    await self.forgetting_strategy.merge_nodes_to_summary(
                        statement_node=statement_node,
                        entity_node=entity_node,
                        config_id=config_id,
                        db=db
                    )
                    
                    merged_count += 1
                    
                    # 进度跟踪：每 10% 记录一次
                    if actual_batch_size > 0 and (idx % progress_interval == 0 or idx == actual_batch_size):
                        progress_pct = (idx / actual_batch_size) * 100
                        logger.info(
                            f"遗忘进度: {idx}/{actual_batch_size} "
                            f"({progress_pct:.1f}%), "
                            f"已融合: {merged_count}, 失败: {failed_count}"
                        )
                
                except Exception as e:
                    failed_count += 1
                    # 检查是否是节点不存在的错误
                    if "nodes may not exist" in str(e):
                        logger.warning(
                            f"节点对 ({idx}/{actual_batch_size}) 的节点不存在（可能已被其他操作删除）: "
                            f"Statement[{statement_id}] + Entity[{entity_id}]"
                        )
                    else:
                        logger.error(
                            f"融合节点对失败 ({idx}/{actual_batch_size}): "
                            f"Statement[{statement_id}] + Entity[{entity_id}], "
                            f"错误: {str(e)}"
                        )
                    # 继续处理剩余节点
                    continue
            
            # 步骤6：统计遗忘后的节点数量
            nodes_after = await self._count_knowledge_nodes(end_user_id)
            logger.info(f"遗忘后节点总数: {nodes_after}")
            
            # 步骤7：生成遗忘报告
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            # 计算节点减少率
            if nodes_before > 0:
                reduction_rate = (nodes_before - nodes_after) / nodes_before
            else:
                reduction_rate = 0.0
            
            # 计算成功率
            if actual_batch_size > 0:
                success_rate = merged_count / actual_batch_size
            else:
                success_rate = 1.0
            
            report = {
                'merged_count': merged_count,
                'nodes_before': nodes_before,
                'nodes_after': nodes_after,
                'reduction_rate': reduction_rate,
                'duration_seconds': duration,
                'start_time': start_time_iso,
                'end_time': end_time.isoformat(),
                'failed_count': failed_count,
                'success_rate': success_rate
            }
            
            logger.info(
                f"遗忘周期完成: "
                f"融合 {merged_count} 对节点, "
                f"失败 {failed_count} 对, "
                f"节点减少 {nodes_before - nodes_after} 个 "
                f"({reduction_rate:.2%}), "
                f"耗时 {duration:.2f} 秒"
            )
            
            return report
        
        except Exception as e:
            logger.error(f"遗忘周期执行失败: {str(e)}")
            raise
        
        finally:
            self.is_running = False
    
    # ==================== 私有辅助方法 ====================
    
    async def _count_knowledge_nodes(
        self,
        end_user_id: Optional[str] = None
    ) -> int:
        """
        统计知识层节点总数
        
        统计 Statement、ExtractedEntity 和 MemorySummary 节点的总数。
        
        Args:
            end_user_id: 组 ID（可选，用于过滤特定组的节点）
        
        Returns:
            int: 知识层节点总数
        """
        query = """
        MATCH (n)
        WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary)
        """
        
        if end_user_id:
            query += " AND n.end_user_id = $end_user_id"
        
        query += """
        RETURN count(n) as total
        """
        
        params = {}
        if end_user_id:
            end_user_id['end_user_id'] = end_user_id
        
        results = await self.connector.execute_query(query, **params)
        
        if results:
            return results[0]['total']
        return 0
