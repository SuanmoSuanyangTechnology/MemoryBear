"""
遗忘策略执行器模块

本模块实现基于 ACT-R 激活值的遗忘策略，负责：
1. 识别低激活值的节点对（Statement-Entity）
2. 将低激活值节点融合为 MemorySummary 节点
3. 使用 LLM 生成高质量摘要（可选）
4. 保留溯源信息并删除原始节点

Classes:
    ForgettingStrategy: 遗忘策略执行器，提供节点识别和融合功能
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.storage_services.forgetting_engine.actr_calculator import ACTRCalculator


logger = logging.getLogger(__name__)


class ForgettingStrategy:
    """
    遗忘策略执行器
    
    基于 ACT-R 激活值识别和融合低价值记忆节点。
    实现了完整的遗忘周期：识别 → 融合 → 删除。
    
    核心功能：
    1. 识别可遗忘节点：激活值低于阈值且长期未访问的 Statement-Entity 对
    2. 节点融合：创建 MemorySummary 节点，继承较高的激活值和重要性
    3. LLM 摘要生成：使用 LLM 生成语义摘要（可降级到简单拼接）
    4. 溯源保留：记录原始节点 ID，保持可追溯性
    
    Attributes:
        connector: Neo4j 连接器实例
        actr_calculator: ACT-R 激活值计算器实例
        forgetting_threshold: 遗忘阈值（激活值低于此值的节点可被遗忘）
    """
    
    def __init__(
        self,
        connector: Neo4jConnector,
        actr_calculator: ACTRCalculator,
        forgetting_threshold: float = 0.3,
        enable_llm_summary: bool = True
    ):
        """
        初始化遗忘策略执行器
        
        Args:
            connector: Neo4j 连接器实例
            actr_calculator: ACT-R 激活值计算器实例
            forgetting_threshold: 遗忘阈值（默认 0.3）
            enable_llm_summary: 是否启用 LLM 摘要生成（默认 True）
        """
        self.connector = connector
        self.actr_calculator = actr_calculator
        self.forgetting_threshold = forgetting_threshold
        self.enable_llm_summary = enable_llm_summary
        
        logger.info(
            f"初始化遗忘策略执行器: threshold={forgetting_threshold}, "
            f"enable_llm_summary={enable_llm_summary}"
        )
    
    async def calculate_forgetting_score(
        self,
        activation_value: float
    ) -> float:
        """
        计算遗忘分数
        
        遗忘分数 = 1 - 激活值
        分数越高，越容易被遗忘。
        
        注意：激活值已经包含了 importance_score 的权重，
        因此不需要单独考虑重要性分数。
        
        Args:
            activation_value: 节点的激活值（0-1）
        
        Returns:
            float: 遗忘分数（0-1），值越高越容易被遗忘
        """
        return 1.0 - activation_value
    
    async def find_forgettable_nodes(
        self,
        end_user_id: Optional[str] = None,
        min_days_since_access: int = 30
    ) -> List[Dict[str, Any]]:
        """
        识别可遗忘的节点对
        
        查找满足以下条件的 Statement-Entity 节点对：
        1. 两个节点的激活值都低于遗忘阈值
        2. 两个节点都至少 min_days_since_access 天未被访问
        3. Statement 和 Entity 之间存在关系边
        
        Args:
            end_user_id: 组 ID（可选，用于过滤特定组的节点）
            min_days_since_access: 最小未访问天数（默认 30 天）
        
        Returns:
            List[Dict[str, Any]]: 可遗忘节点对列表，每个元素包含：
                - statement_id: Statement 节点 ID
                - statement_text: Statement 文本内容
                - statement_activation: Statement 激活值
                - statement_importance: Statement 重要性分数
                - statement_last_access: Statement 最后访问时间
                - entity_id: Entity 节点 ID
                - entity_name: Entity 名称
                - entity_type: Entity 类型
                - entity_activation: Entity 激活值
                - entity_importance: Entity 重要性分数
                - entity_last_access: Entity 最后访问时间
                - avg_activation: 平均激活值（用于排序）
        """
        # 计算时间阈值
        cutoff_time = datetime.now() - timedelta(days=min_days_since_access)
        cutoff_time_iso = cutoff_time.isoformat()
        
        # 构建查询
        query = """
        MATCH (s:Statement)-[r]-(e:ExtractedEntity)
        WHERE s.activation_value IS NOT NULL
          AND e.activation_value IS NOT NULL
          AND s.activation_value < $threshold
          AND e.activation_value < $threshold
          AND s.last_access_time < $cutoff_time
          AND e.last_access_time < $cutoff_time
          AND (e.entity_type IS NULL OR e.entity_type <> 'Person')
        """
        
        if end_user_id:
            query += " AND s.end_user_id = $end_user_id AND e.end_user_id = $end_user_id"
        
        query += """
        RETURN s.id as statement_id,
               s.statement as statement_text,
               s.activation_value as statement_activation,
               s.importance_score as statement_importance,
               s.last_access_time as statement_last_access,
               e.id as entity_id,
               e.name as entity_name,
               e.entity_type as entity_type,
               e.activation_value as entity_activation,
               e.importance_score as entity_importance,
               e.last_access_time as entity_last_access,
               (s.activation_value + e.activation_value) / 2.0 as avg_activation
        ORDER BY avg_activation ASC
        """
        
        params = {
            'threshold': self.forgetting_threshold,
            'cutoff_time': cutoff_time_iso
        }
        if end_user_id:
            params['end_user_id'] = end_user_id
        
        results = await self.connector.execute_query(query, **params)
        
        logger.info(
            f"识别到 {len(results)} 个可遗忘节点对 "
            f"(threshold={self.forgetting_threshold}, "
            f"min_days={min_days_since_access})"
        )
        
        return results
    
    async def merge_nodes_to_summary(
        self,
        statement_node: Dict[str, Any],
        entity_node: Dict[str, Any],
        config_id: Optional[int] = None,
        db = None
    ) -> str:
        """
        将 Statement 和 Entity 节点融合为 MemorySummary 节点
        
        融合过程：
        1. 生成摘要内容（使用 LLM 或简单拼接）
        2. 创建 MemorySummary 节点，继承较高的激活值和重要性分数
        3. 删除原始 Statement 和 Entity 节点
        4. 保留溯源信息（original_statement_id, original_entity_id）
        
        Args:
            statement_node: Statement 节点数据，必须包含：
                - statement_id: 节点 ID
                - statement_text: 文本内容
                - statement_activation: 激活值
                - statement_importance: 重要性分数
            entity_node: Entity 节点数据，必须包含：
                - entity_id: 节点 ID
                - entity_name: 实体名称
                - entity_type: 实体类型
                - entity_activation: 激活值
                - entity_importance: 重要性分数
            config_id: 配置ID（可选，用于获取 llm_id）
            db: 数据库会话（可选，用于获取 llm_id）
        
        Returns:
            str: 创建的 MemorySummary 节点 ID
        
        Raises:
            ValueError: 如果节点数据不完整
            RuntimeError: 如果融合操作失败
        """
        # 验证输入数据
        required_statement_keys = [
            'statement_id', 'statement_text', 
            'statement_activation', 'statement_importance'
        ]
        required_entity_keys = [
            'entity_id', 'entity_name', 'entity_type',
            'entity_activation', 'entity_importance'
        ]
        
        for key in required_statement_keys:
            if key not in statement_node:
                raise ValueError(f"Statement 节点缺少必需字段: {key}")
        
        for key in required_entity_keys:
            if key not in entity_node:
                raise ValueError(f"Entity 节点缺少必需字段: {key}")
        
        # 验证实体类型：不允许融合 Person 类型的实体
        if entity_node.get('entity_type') == 'Person':
            raise ValueError(
                f"不允许融合 Person 类型的实体: entity_id={entity_node.get('entity_id')}, "
                f"entity_name={entity_node.get('entity_name')}"
            )
        
        # 提取节点信息
        statement_id = statement_node['statement_id']
        statement_text = statement_node['statement_text']
        statement_activation = statement_node['statement_activation']
        statement_importance = statement_node['statement_importance']
        
        entity_id = entity_node['entity_id']
        entity_name = entity_node['entity_name']
        entity_type = entity_node['entity_type']
        entity_activation = entity_node['entity_activation']
        entity_importance = entity_node['entity_importance']
        
        # 获取 end_user_id（从 statement 或 entity 节点）
        end_user_id = statement_node.get('end_user_id') or entity_node.get('end_user_id')
        
        # 生成摘要内容
        summary_text = await self._generate_summary(
            statement_text=statement_text,
            entity_name=entity_name,
            entity_type=entity_type,
            config_id=config_id,
            db=db
        )
        
        # 生成标题和类型（使用LLM）
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import generate_title_and_type_for_summary
        
        # 获取 LLM 客户端
        llm_client = None
        if config_id is not None and db is not None:
            try:
                llm_client = await self._get_llm_client(db, config_id)
            except Exception as e:
                logger.warning(f"获取 LLM 客户端失败: {str(e)}")
        
        # 生成标题和类型
        try:
            if llm_client is not None:
                title, episodic_type = await generate_title_and_type_for_summary(
                    content=summary_text,
                    llm_client=llm_client
                )
                logger.info(f"成功为MemorySummary生成标题和类型: title={title}, type={episodic_type}")
            else:
                logger.warning("LLM 客户端不可用，使用默认标题和类型")
                title = "未命名"
                episodic_type = "conversation"
        except Exception as e:
            logger.error(f"生成标题和类型失败，使用默认值: {str(e)}")
            title = "未命名"
            episodic_type = "conversation"
        
        # 计算继承的激活值和重要性（取较高值）
        inherited_activation = max(statement_activation, entity_activation)
        inherited_importance = max(statement_importance, entity_importance)
        
        # 创建 MemorySummary 节点
        current_time = datetime.now()
        current_time_iso = current_time.isoformat()
        
        # 生成新的 MemorySummary ID
        import uuid
        summary_id = f"summary_{uuid.uuid4().hex[:16]}"
        
        # 使用事务创建 MemorySummary 并删除原节点
        async def merge_transaction(tx, **params):
            """事务函数：创建摘要节点并删除原节点"""
            query = """
            // 首先检查节点是否存在
            OPTIONAL MATCH (s:Statement {id: $statement_id})
            OPTIONAL MATCH (e:ExtractedEntity {id: $entity_id})
            
            // 如果任一节点不存在，直接返回 null（不执行后续操作）
            WITH s, e
            WHERE s IS NOT NULL AND e IS NOT NULL
            
            // 创建 MemorySummary 节点
            CREATE (ms:MemorySummary {
                id: $summary_id,
                summary: $summary_text,
                name: $title,
                memory_type: $episodic_type,
                original_statement_id: $statement_id,
                original_entity_id: $entity_id,
                activation_value: $inherited_activation,
                importance_score: $inherited_importance,
                access_history: [$current_time],
                last_access_time: $current_time,
                access_count: 1,
                version: 1,
                end_user_id: $end_user_id,
                created_at: datetime($current_time),
                merged_at: datetime($current_time)
            })
            
            // 转移 Statement 的出边到 MemorySummary（只转移目标节点仍存在的边）
            WITH ms, s, e
            CALL (ms, s, e) {
                OPTIONAL MATCH (s)-[r_out]->(target)
                WHERE target <> e AND r_out IS NOT NULL AND target IS NOT NULL
                FOREACH (_ IN CASE WHEN target IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (ms)-[new_rel:DERIVED_FROM]->(target)
                    ON CREATE SET 
                        new_rel = properties(r_out),
                        new_rel.original_relationship_type = type(r_out),
                        new_rel.merged_from_statement = true,
                        new_rel.merge_count = 1
                    ON MATCH SET
                        new_rel.merge_count = coalesce(new_rel.merge_count, 0) + 1
                )
            }
            
            // 转移 Statement 的入边到 MemorySummary（只转移源节点仍存在的边）
            WITH ms, s, e
            CALL (ms, s, e) {
                OPTIONAL MATCH (source)-[r_in]->(s)
                WHERE r_in IS NOT NULL AND source IS NOT NULL
                FOREACH (_ IN CASE WHEN source IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (source)-[new_rel:DERIVED_FROM]->(ms)
                    ON CREATE SET 
                        new_rel = properties(r_in),
                        new_rel.original_relationship_type = type(r_in),
                        new_rel.merged_from_statement = true,
                        new_rel.merge_count = 1
                    ON MATCH SET
                        new_rel.merge_count = coalesce(new_rel.merge_count, 0) + 1
                )
            }
            
            // 转移 Entity 的出边到 MemorySummary（只转移目标节点仍存在的边）
            WITH ms, s, e
            CALL (ms, s, e) {
                OPTIONAL MATCH (e)-[r_out]->(target)
                WHERE target <> s AND r_out IS NOT NULL AND target IS NOT NULL
                FOREACH (_ IN CASE WHEN target IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (ms)-[new_rel:DERIVED_FROM]->(target)
                    ON CREATE SET 
                        new_rel = properties(r_out),
                        new_rel.original_relationship_type = type(r_out),
                        new_rel.merged_from_entity = true,
                        new_rel.merge_count = 1
                    ON MATCH SET
                        new_rel.merge_count = coalesce(new_rel.merge_count, 0) + 1
                )
            }
            
            // 转移 Entity 的入边到 MemorySummary（只转移源节点仍存在的边）
            WITH ms, s, e
            CALL (ms, s, e) {
                OPTIONAL MATCH (source)-[r_in]->(e)
                WHERE source <> s AND r_in IS NOT NULL AND source IS NOT NULL
                FOREACH (_ IN CASE WHEN source IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (source)-[new_rel:DERIVED_FROM]->(ms)
                    ON CREATE SET 
                        new_rel = properties(r_in),
                        new_rel.original_relationship_type = type(r_in),
                        new_rel.merged_from_entity = true,
                        new_rel.merge_count = 1
                    ON MATCH SET
                        new_rel.merge_count = coalesce(new_rel.merge_count, 0) + 1
                )
            }
            
            // 删除原始节点
            WITH ms, s, e
            DETACH DELETE s, e
            
            RETURN ms.id as summary_id
            """
            
            result = await tx.run(query, **params)
            record = await result.single()
            
            if not record:
                raise RuntimeError("Failed to create MemorySummary node - nodes may not exist")
            
            return record['summary_id']
        
        params = {
            'summary_id': summary_id,
            'summary_text': summary_text,
            'title': title,
            'episodic_type': episodic_type,
            'statement_id': statement_id,
            'entity_id': entity_id,
            'inherited_activation': inherited_activation,
            'inherited_importance': inherited_importance,
            'current_time': current_time_iso,
            'end_user_id': end_user_id
        }
        
        try:
            created_summary_id = await self.connector.execute_write_transaction(
                merge_transaction,
                **params
            )
            
            logger.info(
                f"成功融合节点: Statement[{statement_id}] + Entity[{entity_id}] "
                f"-> MemorySummary[{created_summary_id}], "
                f"activation={inherited_activation:.4f}, "
                f"importance={inherited_importance:.4f}"
            )
            
            return created_summary_id
            
        except Exception as e:
            # 记录详细的错误信息，包括异常类型和堆栈
            import traceback
            error_details = traceback.format_exc()
            logger.error(
                f"融合节点失败: Statement[{statement_id}] + Entity[{entity_id}], "
                f"错误类型: {type(e).__name__}, "
                f"错误信息: {str(e)}, "
                f"详细堆栈:\n{error_details}"
            )
            raise RuntimeError(
                f"融合节点失败: {str(e)}"
            ) from e
    
    # ==================== 私有辅助方法 ====================
    
    async def _generate_summary(
        self,
        statement_text: str,
        entity_name: str,
        entity_type: str,
        config_id: Optional[int] = None,
        db = None
    ) -> str:
        """
        生成摘要内容
        
        优先使用 LLM 生成高质量摘要，如果 LLM 不可用或失败，
        则降级到简单文本拼接。
        
        Args:
            statement_text: Statement 文本内容
            entity_name: Entity 名称
            entity_type: Entity 类型
            config_id: 配置ID（可选，用于获取 llm_id）
            db: 数据库会话（可选，用于获取 llm_id）
        
        Returns:
            str: 生成的摘要文本（最多 200 个字符）
        """
        # 如果配置禁用 LLM 摘要，直接使用简单拼接
        if not self.enable_llm_summary:
            logger.info("LLM 摘要生成已禁用，使用简单拼接")
            return self._simple_concatenation(
                statement_text, entity_name, entity_type
            )
        
        # 尝试获取 LLM 客户端
        llm_client = None
        if config_id is not None and db is not None:
            try:
                llm_client = await self._get_llm_client(db, config_id)
            except Exception as e:
                logger.warning(f"获取 LLM 客户端失败: {str(e)}")
        
        # 如果没有 LLM 客户端，直接使用简单拼接
        if llm_client is None:
            logger.info("未能获取 LLM 客户端，使用简单拼接")
            return self._simple_concatenation(
                statement_text, entity_name, entity_type
            )
        
        # 尝试使用 LLM 生成摘要
        try:
            summary = await self._generate_llm_summary(
                statement_text=statement_text,
                entity_name=entity_name,
                entity_type=entity_type,
                llm_client=llm_client
            )
            
            # 限制长度为 200 个字符
            if len(summary) > 200:
                summary = f"{summary[:197]}..."
            
            logger.info(f"使用 LLM 生成摘要: {summary}")
            return summary
            
        except Exception as e:
            logger.warning(
                f"LLM 摘要生成失败，降级到简单拼接: {str(e)}"
            )
            return self._simple_concatenation(
                statement_text, entity_name, entity_type
            )
    
    async def _get_llm_client(self, db, config_id: int):
        """
        从数据库获取 LLM 客户端
        
        Args:
            db: 数据库会话
            config_id: 配置ID
        
        Returns:
            LLM 客户端实例，如果无法获取则返回 None
        """
        try:
            from app.repositories.memory_config_repository import MemoryConfigRepository
            from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
            
            # 从数据库读取配置
            repository = MemoryConfigRepository()
            db_config = repository.get_by_id(db, config_id)
            
            if db_config is None or db_config.llm_id is None:
                logger.warning(f"配置 {config_id} 不存在或未设置 llm_id")
                return None
            
            # 创建 LLM 客户端
            factory = MemoryClientFactory(db)
            llm_client = factory.get_llm_client(str(db_config.llm_id))
            
            logger.info(f"成功获取 LLM 客户端: config_id={config_id}, llm_id={db_config.llm_id}")
            return llm_client
            
        except Exception as e:
            logger.error(f"获取 LLM 客户端失败: {str(e)}")
            return None
    
    async def _generate_llm_summary(
        self,
        statement_text: str,
        entity_name: str,
        entity_type: str,
        llm_client
    ) -> str:
        """
        使用 LLM 生成高质量摘要
        
        Args:
            statement_text: Statement 文本内容
            entity_name: Entity 名称
            entity_type: Entity 类型
            llm_client: LLM 客户端实例
        
        Returns:
            str: LLM 生成的摘要文本
        
        Raises:
            Exception: 如果 LLM 调用失败
        """
        # 构建提示词
        prompt = f"""请为以下记忆片段生成一个简洁的摘要（不超过200个字符）：

实体名称: {entity_name}
实体类型: {entity_type}
陈述内容: {statement_text}

要求：
1. 摘要应该保留核心语义信息
2. 长度不超过200个字符
3. 使用简洁、自然的中文表达
4. 只返回摘要文本，不要包含其他内容

摘要："""
        
        # 调用 LLM（直接传递 prompt 字符串）
        response = await llm_client.chat(prompt)
        
        # 提取摘要文本
        if isinstance(response, str):
            summary = response.strip()
        elif hasattr(response, 'content'):
            summary = response.content.strip()
        else:
            summary = str(response).strip()
        
        return summary
    
    def _simple_concatenation(
        self,
        statement_text: str,
        entity_name: str,
        entity_type: str
    ) -> str:
        """
        简单文本拼接生成摘要
        
        降级策略：当 LLM 不可用时使用。
        格式：[实体类型]实体名称: 陈述内容
        
        Args:
            statement_text: Statement 文本内容
            entity_name: Entity 名称
            entity_type: Entity 类型
        
        Returns:
            str: 拼接的摘要文本（最多 200 个字符）
        """
        # 构建简单摘要
        summary = f"[{entity_type}]{entity_name}: {statement_text}"
        
        # 限制长度为 200 个字符（注意：这里的长度是字符数，不是字节数）
        if len(summary) > 200:
            # 截断并添加省略号
            summary = f"{summary[:197]}..."
        
        return summary

