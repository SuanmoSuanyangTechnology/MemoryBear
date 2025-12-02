"""
自我反思引擎实现

该模块实现了记忆系统的自我反思功能，包括：
1. 基于时间的反思 - 根据时间周期触发反思
2. 基于事实的反思 - 检测记忆冲突并解决
3. 综合反思 - 整合多种反思策略
4. 反思结果应用 - 更新记忆库
"""

import os
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum
import uuid

from pydantic import BaseModel, Field


# 配置日志
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )
else:
    _root_logger.setLevel(logging.INFO)


class ReflectionRange(str, Enum):
    """反思范围枚举"""
    RETRIEVAL = "retrieval"  # 从检索结果中反思
    DATABASE = "database"    # 从整个数据库中反思


class ReflectionBaseline(str, Enum):
    """反思基线枚举"""
    TIME = "TIME"      # 基于时间的反思
    FACT = "FACT"      # 基于事实的反思
    HYBRID = "HYBRID"  # 混合反思


class ReflectionConfig(BaseModel):
    """反思引擎配置"""
    enabled: bool = False
    iteration_period: str = "3"  # 反思周期
    reflexion_range: ReflectionRange = ReflectionRange.RETRIEVAL
    baseline: ReflectionBaseline = ReflectionBaseline.TIME
    concurrency: int = Field(default=5, description="并发数量")

    class Config:
        use_enum_values = True


class ReflectionResult(BaseModel):
    """反思结果"""
    success: bool
    message: str
    conflicts_found: int = 0
    conflicts_resolved: int = 0
    memories_updated: int = 0
    execution_time: float = 0.0
    details: Optional[Dict[str, Any]] = None


class ReflectionEngine:
    """
    自我反思引擎

    负责执行记忆系统的自我反思，包括冲突检测、冲突解决和记忆更新。
    """

    def __init__(
        self,
        config: ReflectionConfig,
        neo4j_connector: Optional[Any] = None,
        llm_client: Optional[Any] = None,
        get_data_func: Optional[Any] = None,
        render_evaluate_prompt_func: Optional[Any] = None,
        render_reflexion_prompt_func: Optional[Any] = None,
        conflict_schema: Optional[Any] = None,
        reflexion_schema: Optional[Any] = None,
        update_query: Optional[str] = None
    ):
        """
        初始化反思引擎

        Args:
            config: 反思引擎配置
            neo4j_connector: Neo4j 连接器（可选）
            llm_client: LLM 客户端（可选）
            get_data_func: 获取数据的函数（可选）
            render_evaluate_prompt_func: 渲染评估提示词的函数（可选）
            render_reflexion_prompt_func: 渲染反思提示词的函数（可选）
            conflict_schema: 冲突结果 Schema（可选）
            reflexion_schema: 反思结果 Schema（可选）
            update_query: 更新查询语句（可选）
        """
        self.config = config
        self.neo4j_connector = neo4j_connector
        self.llm_client = llm_client
        self.get_data_func = get_data_func
        self.render_evaluate_prompt_func = render_evaluate_prompt_func
        self.render_reflexion_prompt_func = render_reflexion_prompt_func
        self.conflict_schema = conflict_schema
        self.reflexion_schema = reflexion_schema
        self.update_query = update_query
        self._semaphore = asyncio.Semaphore(config.concurrency)

        # 延迟导入以避免循环依赖
        self._lazy_init_done = False

    def _lazy_init(self):
        """延迟初始化，避免循环导入"""
        if self._lazy_init_done:
            return

        if self.neo4j_connector is None:
            from app.repositories.neo4j.neo4j_connector import Neo4jConnector
            self.neo4j_connector = Neo4jConnector()

        if self.llm_client is None:
            from app.core.memory.utils.llm.llm_utils import get_llm_client
            from app.core.memory.utils.config import definitions as config_defs
            self.llm_client = get_llm_client(config_defs.SELECTED_LLM_ID)

        if self.get_data_func is None:
            from app.core.memory.utils.config.get_data import get_data
            self.get_data_func = get_data

        if self.render_evaluate_prompt_func is None:
            from app.core.memory.utils.prompt.template_render import render_evaluate_prompt
            self.render_evaluate_prompt_func = render_evaluate_prompt

        if self.render_reflexion_prompt_func is None:
            from app.core.memory.utils.prompt.template_render import render_reflexion_prompt
            self.render_reflexion_prompt_func = render_reflexion_prompt

        if self.conflict_schema is None:
            from app.schemas.memory_storage_schema import ConflictResultSchema
            self.conflict_schema = ConflictResultSchema

        if self.reflexion_schema is None:
            from app.schemas.memory_storage_schema import ReflexionResultSchema
            self.reflexion_schema = ReflexionResultSchema

        if self.update_query is None:
            from app.repositories.neo4j.cypher_queries import UPDATE_STATEMENT_INVALID_AT
            self.update_query = UPDATE_STATEMENT_INVALID_AT

        self._lazy_init_done = True

    async def execute_reflection(self, host_id: uuid.UUID) -> ReflectionResult:
        """
        执行完整的反思流程

        Args:
            host_id: 主机ID

        Returns:
            ReflectionResult: 反思结果
        """
        # 延迟初始化
        self._lazy_init()

        if not self.config.enabled:
            return ReflectionResult(
                success=False,
                message="反思引擎未启用"
            )

        start_time = asyncio.get_event_loop().time()
        logging.info("====== 自我反思流程开始 ======")

        try:
            # 1. 获取反思数据
            reflexion_data = await self._get_reflexion_data(host_id)
            if not reflexion_data:
                return ReflectionResult(
                    success=True,
                    message="无反思数据，结束反思",
                    execution_time=asyncio.get_event_loop().time() - start_time
                )

            # 2. 检测冲突（基于事实的反思）
            conflict_data = await self._detect_conflicts(reflexion_data)
            if not conflict_data:
                return ReflectionResult(
                    success=True,
                    message="无冲突，无需反思",
                    execution_time=asyncio.get_event_loop().time() - start_time
                )

            conflicts_found = len(conflict_data)
            logging.info(f"发现 {conflicts_found} 个冲突")

            # 记录冲突数据
            await self._log_data("conflict", conflict_data)

            # 3. 解决冲突
            solved_data = await self._resolve_conflicts(conflict_data)
            if not solved_data:
                return ReflectionResult(
                    success=False,
                    message="反思失败，未解决冲突",
                    conflicts_found=conflicts_found,
                    execution_time=asyncio.get_event_loop().time() - start_time
                )

            conflicts_resolved = len(solved_data)
            logging.info(f"解决了 {conflicts_resolved} 个冲突")

            # 记录解决方案
            await self._log_data("solved_data", solved_data)

            # 4. 应用反思结果（更新记忆库）
            memories_updated = await self._apply_reflection_results(solved_data)

            execution_time = asyncio.get_event_loop().time() - start_time

            logging.info("====== 自我反思流程结束 ======")

            return ReflectionResult(
                success=True,
                message="反思完成",
                conflicts_found=conflicts_found,
                conflicts_resolved=conflicts_resolved,
                memories_updated=memories_updated,
                execution_time=execution_time
            )

        except Exception as e:
            logging.error(f"反思流程执行失败: {e}", exc_info=True)
            return ReflectionResult(
                success=False,
                message=f"反思流程执行失败: {str(e)}",
                execution_time=asyncio.get_event_loop().time() - start_time
            )

    async def _get_reflexion_data(self, host_id: uuid.UUID) -> List[Any]:
        """
        获取反思数据

        根据配置的反思范围获取需要反思的记忆数据。

        Args:
            host_id: 主机ID

        Returns:
            List[Any]: 反思数据列表
        """
        if self.config.reflexion_range == ReflectionRange.RETRIEVAL:
            # 从检索结果中获取数据
            return await self.get_data_func(host_id)
        elif self.config.reflexion_range == ReflectionRange.DATABASE:
            # 从整个数据库中获取数据（待实现）
            logging.warning("从数据库获取反思数据功能尚未实现")
            return []
        else:
            raise ValueError(f"未知的反思范围: {self.config.reflexion_range}")

    async def _detect_conflicts(self, data: List[Any]) -> List[Any]:
        """
        检测冲突（基于事实的反思）

        使用 LLM 分析记忆数据，检测其中的冲突。

        Args:
            data: 待检测的记忆数据

        Returns:
            List[Any]: 冲突记忆列表
        """
        if not data:
            return []

        logging.info("====== 冲突检测开始 ======")
        start_time = asyncio.get_event_loop().time()

        try:
            # 渲染冲突检测提示词
            rendered_prompt = await self.render_evaluate_prompt_func(
                data,
                self.conflict_schema
            )

            messages = [{"role": "user", "content": rendered_prompt}]
            logging.info(f"提示词长度: {len(rendered_prompt)}")

            # 调用 LLM 进行冲突检测
            response = await self.llm_client.response_structured(
                messages,
                self.conflict_schema
            )

            execution_time = asyncio.get_event_loop().time() - start_time
            logging.info(f"冲突检测耗时: {execution_time:.2f} 秒")

            if not response:
                logging.error("LLM 冲突检测输出解析失败")
                return []

            # 标准化返回格式
            if isinstance(response, BaseModel):
                return [response.model_dump()]
            elif hasattr(response, 'dict'):
                return [response.dict()]
            else:
                return [response]

        except Exception as e:
            logging.error(f"冲突检测失败: {e}", exc_info=True)
            return []

    async def _resolve_conflicts(self, conflicts: List[Any]) -> List[Any]:
        """
        解决冲突

        使用 LLM 对检测到的冲突进行反思和解决。

        Args:
            conflicts: 冲突列表

        Returns:
            List[Any]: 解决方案列表
        """
        if not conflicts:
            return []

        logging.info("====== 冲突解决开始 ======")

        # 并行处理每个冲突
        async def _resolve_one(conflict: Any) -> Optional[Dict[str, Any]]:
            """解决单个冲突"""
            async with self._semaphore:
                try:
                    # 渲染反思提示词
                    rendered_prompt = await self.render_reflexion_prompt_func(
                        [conflict],
                        self.reflexion_schema
                    )

                    messages = [{"role": "user", "content": rendered_prompt}]

                    # 调用 LLM 进行反思
                    response = await self.llm_client.response_structured(
                        messages,
                        self.reflexion_schema
                    )

                    if not response:
                        return None

                    # 标准化返回格式
                    if isinstance(response, BaseModel):
                        return response.model_dump()
                    elif hasattr(response, 'dict'):
                        return response.dict()
                    elif isinstance(response, dict):
                        return response
                    else:
                        return None

                except Exception as e:
                    logging.warning(f"解决单个冲突失败: {e}")
                    return None

        # 并发执行所有冲突解决任务
        tasks = [_resolve_one(conflict) for conflict in conflicts]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # 过滤掉失败的结果
        solved = [r for r in results if r is not None]

        logging.info(f"成功解决 {len(solved)}/{len(conflicts)} 个冲突")

        return solved

    async def _apply_reflection_results(
        self,
        solved_data: List[Dict[str, Any]]
    ) -> int:
        """
        应用反思结果（更新记忆库）

        将解决冲突后的记忆更新到 Neo4j 数据库中。

        Args:
            solved_data: 解决方案列表

        Returns:
            int: 成功更新的记忆数量
        """
        if not solved_data:
            logging.warning("无解决方案数据，跳过更新")
            return 0

        logging.info("====== 记忆更新开始 ======")

        success_count = 0

        async def _update_one(item: Dict[str, Any]) -> bool:
            """更新单条记忆"""
            async with self._semaphore:
                try:
                    if not isinstance(item, dict):
                        return False

                    # 提取更新参数
                    resolved = item.get("resolved", {})
                    resolved_mem = resolved.get("resolved_memory", {})
                    group_id = resolved_mem.get("group_id")
                    memory_id = resolved_mem.get("id")
                    new_invalid_at = resolved_mem.get("invalid_at")

                    if not all([group_id, memory_id, new_invalid_at]):
                        logging.warning(f"记忆更新参数缺失，跳过此项: {item}")
                        return False

                    # 执行更新
                    await self.neo4j_connector.execute_query(
                        self.update_query,
                        group_id=group_id,
                        id=memory_id,
                        new_invalid_at=new_invalid_at,
                    )

                    return True

                except Exception as e:
                    logging.error(f"更新单条记忆失败: {e}")
                    return False

        # 并发执行所有更新任务
        tasks = [
            _update_one(item)
            for item in solved_data
            if isinstance(item, dict)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        success_count = sum(1 for r in results if r)

        logging.info(f"成功更新 {success_count}/{len(solved_data)} 条记忆")

        return success_count

    async def _log_data(self, label: str, data: Any) -> None:
        """
        记录数据到文件

        Args:
            label: 数据标签
            data: 要记录的数据
        """
        def _write():
            try:
                with open("reflexion_data.json", "a", encoding="utf-8") as f:
                    f.write(f"### {label} ###\n")
                    json.dump(data, f, ensure_ascii=False, indent=4)
                    f.write("\n\n")
            except Exception as e:
                logging.warning(f"记录数据失败: {e}")

        # 在后台线程中执行写入，避免阻塞事件循环
        await asyncio.to_thread(_write)

    # 基于时间的反思方法
    async def time_based_reflection(
        self,
        host_id: uuid.UUID,
        time_period: Optional[str] = None
    ) -> ReflectionResult:
        """
        基于时间的反思

        根据时间周期触发反思，检查在指定时间段内的记忆。

        Args:
            host_id: 主机ID
            time_period: 时间周期（如"三小时"），如果不提供则使用配置中的值

        Returns:
            ReflectionResult: 反思结果
        """
        period = time_period or self.config.iteration_period
        logging.info(f"执行基于时间的反思，周期: {period}")

        # 使用标准反思流程
        return await self.execute_reflection(host_id)

    # 基于事实的反思方法
    async def fact_based_reflection(
        self,
        host_id: uuid.UUID
    ) -> ReflectionResult:
        """
        基于事实的反思

        检测记忆中的事实冲突并解决。

        Args:
            host_id: 主机ID

        Returns:
            ReflectionResult: 反思结果
        """
        logging.info("执行基于事实的反思")

        # 使用标准反思流程
        return await self.execute_reflection(host_id)

    # 综合反思方法
    async def comprehensive_reflection(
        self,
        host_id: uuid.UUID
    ) -> ReflectionResult:
        """
        综合反思

        整合基于时间和基于事实的反思策略。

        Args:
            host_id: 主机ID

        Returns:
            ReflectionResult: 反思结果
        """
        logging.info("执行综合反思")

        # 根据配置的基线选择反思策略
        if self.config.baseline == ReflectionBaseline.TIME:
            return await self.time_based_reflection(host_id)
        elif self.config.baseline == ReflectionBaseline.FACT:
            return await self.fact_based_reflection(host_id)
        elif self.config.baseline == ReflectionBaseline.HYBRID:
            # 混合策略：先执行基于时间的反思，再执行基于事实的反思
            time_result = await self.time_based_reflection(host_id)
            fact_result = await self.fact_based_reflection(host_id)

            # 合并结果
            return ReflectionResult(
                success=time_result.success and fact_result.success,
                message=f"时间反思: {time_result.message}; 事实反思: {fact_result.message}",
                conflicts_found=time_result.conflicts_found + fact_result.conflicts_found,
                conflicts_resolved=time_result.conflicts_resolved + fact_result.conflicts_resolved,
                memories_updated=time_result.memories_updated + fact_result.memories_updated,
                execution_time=time_result.execution_time + fact_result.execution_time
            )
        else:
            raise ValueError(f"未知的反思基线: {self.config.baseline}")


# 便捷函数：创建默认配置的反思引擎
def create_reflection_engine(
    enabled: bool = False,
    iteration_period: str = "3",
    reflexion_range: str = "retrieval",
    baseline: str = "TIME",
    concurrency: int = 5
) -> ReflectionEngine:
    """
    创建反思引擎实例

    Args:
        enabled: 是否启用反思
        iteration_period: 反思周期
        reflexion_range: 反思范围
        baseline: 反思基线
        concurrency: 并发数量

    Returns:
        ReflectionEngine: 反思引擎实例
    """
    config = ReflectionConfig(
        enabled=enabled,
        iteration_period=iteration_period,
        reflexion_range=reflexion_range,
        baseline=baseline,
        concurrency=concurrency
    )
    return ReflectionEngine(config)
