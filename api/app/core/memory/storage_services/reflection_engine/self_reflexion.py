"""
自我反思引擎实现

该模块实现了记忆系统的自我反思功能，包括：
1. 基于时间的反思 - 根据时间周期触发反思
2. 基于事实的反思 - 检测记忆冲突并解决
3. 综合反思 - 整合多种反思策略
4. 反思结果应用 - 更新记忆库
"""

import json
import logging
import asyncio
import os
import time
from typing import List, Dict, Any, Optional
from enum import Enum
import uuid

from pydantic import BaseModel


from app.core.response_utils import success
from app.repositories.neo4j.cypher_queries import neo4j_query_part, neo4j_statement_part, neo4j_query_all,  neo4j_statement_all
from app.repositories.neo4j.neo4j_update import neo4j_data

from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.utils.config import definitions as config_defs
from app.core.memory.utils.config import get_model_config
from app.core.memory.utils.config.get_data import get_data
from app.core.memory.utils.config.get_data import get_data_statement
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.memory.utils.prompt.template_render import render_evaluate_prompt
from app.core.memory.utils.prompt.template_render import render_reflexion_prompt
from app.core.models.base import RedBearModelConfig
from app.repositories.neo4j.cypher_queries import (
    neo4j_query_all,
    neo4j_query_part,
    neo4j_statement_all,
    neo4j_statement_part,
)
from app.repositories.neo4j.cypher_queries import UPDATE_STATEMENT_INVALID_AT
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.neo4j_update import neo4j_data
from app.schemas.memory_storage_schema import ConflictResultSchema
from app.schemas.memory_storage_schema import ReflexionResultSchema


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
    PARTIAL = "partial"  # 从检索结果中反思
    ALL = "all"  # 从整个数据库中反思


class ReflectionBaseline(str, Enum):
    """反思基线枚举"""
    TIME = "TIME"  # 基于时间的反思
    FACT = "FACT"  # 基于事实的反思
    HYBRID = "HYBRID"  # 混合反思


class ReflectionConfig(BaseModel):
    """反思引擎配置"""
    enabled: bool = False
    iteration_period: str = "3"  # 反思周期
    reflexion_range: ReflectionRange = ReflectionRange.PARTIAL
    baseline: ReflectionBaseline = ReflectionBaseline.TIME
    model_id: Optional[str] = None  # 模型ID
    end_user_id: Optional[str] = None
    output_example: Optional[str] = None  # 输出示例

    # 评估相关字段
    memory_verify: bool = True  # 记忆验证
    quality_assessment: bool = True  # 质量评估
    violation_handling_strategy: str = "warn"  # 违规处理策略

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
        self._semaphore = asyncio.Semaphore(5)  # 默认并发数为5


        # 延迟导入以避免循环依赖
        self._lazy_init_done = False

    def _lazy_init(self):
        """延迟初始化，避免循环导入"""
        if self._lazy_init_done:
            return

        if self.neo4j_connector is None:
            self.neo4j_connector = Neo4jConnector()

        if self.llm_client is None:
            self.llm_client = get_llm_client(config_defs.SELECTED_LLM_ID)
        elif isinstance(self.llm_client, str):
            # 如果 llm_client 是字符串（model_id），则用它初始化客户端
            # from app.core.memory.utils.llm.llm_utils import get_llm_client
            # model_id = self.llm_client
            # self.llm_client = get_llm_client(model_id)
            extra_params={
                    "temperature": 0.2,  # 降低温度提高响应速度和一致性
                    "max_tokens": 600,  # 限制最大token数
                    "top_p": 0.8,  # 优化采样参数
                    "stream": False,  # 确保非流式输出以获得最快响应
                }

            model_config = get_model_config(self.llm_client)
            self.llm_client  = OpenAIClient(RedBearModelConfig(
                model_name=model_config.get("model_name"),
                provider=model_config.get("provider"),
                api_key=model_config.get("api_key"),
                base_url=model_config.get("base_url"),
                timeout=model_config.get("timeout", 30),
                max_retries=model_config.get("max_retries", 2),
                extra_params=extra_params
            ), type_=model_config.get("type"))

        if self.get_data_func is None:
            self.get_data_func = get_data

        # 导入get_data_statement函数
        if not hasattr(self, 'get_data_statement'):
            self.get_data_statement = get_data_statement

        if self.render_evaluate_prompt_func is None:
            self.render_evaluate_prompt_func = render_evaluate_prompt

        if self.render_reflexion_prompt_func is None:
            self.render_reflexion_prompt_func = render_reflexion_prompt

        if self.conflict_schema is None:
            self.conflict_schema = ConflictResultSchema

        if self.reflexion_schema is None:
            self.reflexion_schema = ReflexionResultSchema

        if self.update_query is None:
            self.update_query = UPDATE_STATEMENT_INVALID_AT

        self._lazy_init_done = True

    async def execute_reflection(self, host_id) -> ReflectionResult:
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

        print(self.config.baseline, self.config.memory_verify, self.config.quality_assessment)
        try:
            # 1. 获取反思数据
            reflexion_data, statement_databasets = await self._get_reflexion_data(host_id)
            if not reflexion_data:
                return ReflectionResult(
                    success=True,
                    message="无反思数据，结束反思",
                    execution_time=asyncio.get_event_loop().time() - start_time
                )

            # 2. 检测冲突（基于事实的反思）
            conflict_data = await self._detect_conflicts(reflexion_data, statement_databasets)
            print(100 * '-')
            print(conflict_data)
            print(100 * '-')

            # 检查是否真的有冲突
            has_conflict = conflict_data[0].get('conflict', False)
            conflicts_found = len(conflict_data[0]['data']) if has_conflict else 0
            logging.info(f"冲突状态: {has_conflict}, 发现 {conflicts_found} 个冲突")

            # 记录冲突数据
            await self._log_data("conflict", conflict_data)

            # 3. 解决冲突
            solved_data = await self._resolve_conflicts(conflict_data, statement_databasets)
            if not solved_data:
                return ReflectionResult(
                    success=False,
                    message="反思失败，未解决冲突",
                    conflicts_found=conflicts_found,
                    execution_time=asyncio.get_event_loop().time() - start_time
                )
            print(100 * '*')
            print(solved_data)
            print(100 * '*')

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
                execution_time=execution_time,

            )

        except Exception as e:
            logging.error(f"反思流程执行失败: {e}", exc_info=True)
            return ReflectionResult(
                success=False,
                message=f"反思流程执行失败: {str(e)}",
                execution_time=asyncio.get_event_loop().time() - start_time
            )

    async def reflection_run(self):
        self._lazy_init()
        start_time = time.time()

        asyncio.get_event_loop().time()
        logging.info("====== 自我反思流程开始 ======")

        result_data = {}

        source_data, databasets = await self.extract_fields_from_json()
        result_data['baseline'] = self.config.baseline
        result_data[
            'source_data'] = "我是 2023 年春天去北京工作的，后来基本一直都在北京上班，也没怎么换过城市。不过后来公司调整，2024 年上半年我被调到上海待了差不多半年，那段时间每天都是在上海办公室打卡。当时入职资料用的还是我之前的身份信息，身份证号是 11010119950308123X，银行卡是 6222023847595898，这些一直没变。对了，其实我 从 2023 年开始就一直在北京生活，从来没有长期离开过北京，上海那段更多算是远程配合"

        # 2. 检测冲突（基于事实的反思）
        conflict_data = await self._detect_conflicts(databasets, source_data)
        # 遍历数据提取字段
        quality_assessments = []
        memory_verifies = []
        for item in conflict_data:
            quality_assessments.append(item['quality_assessment'])
            memory_verifies.append(item['memory_verify'])
        result_data['quality_assessments'] = quality_assessments
        result_data['memory_verifies'] = memory_verifies

        # 检查是否真的有冲突
        has_conflict = conflict_data[0].get('conflict', False)
        conflicts_found = len(conflict_data[0]['data']) if has_conflict else 0
        logging.info(f"冲突状态: {has_conflict}, 发现 {conflicts_found} 个冲突")

        # 记录冲突数据
        await self._log_data("conflict", conflict_data)

        # Clearn conflict_data，And memory_verify和quality_assessment
        cleaned_conflict_data = []
        for item in conflict_data:
            cleaned_item = {
                'data': item['data'],
                'conflict': item['conflict']
            }
            cleaned_conflict_data.append(cleaned_item)
        print(cleaned_conflict_data)

        # 3. 解决冲突
        solved_data = await self._resolve_conflicts(cleaned_conflict_data, source_data)
        if not solved_data:
            return ReflectionResult(
                success=False,
                message="反思失败，未解决冲突",
                conflicts_found=conflicts_found,
                execution_time=asyncio.get_event_loop().time() - start_time
            )
        reflexion_data = []

        # 遍历数据提取reflexion字段
        for item in solved_data:
            if 'results' in item:
                for result in item['results']:
                    reflexion_data.append(result['reflexion'])
        result_data['reflexion_data'] = reflexion_data
        return result_data


    async def extract_fields_from_json(self):
        """从example.json中提取source_data和databasets字段"""

        prompt_dir = os.path.join(os.path.dirname(__file__), "example")
        try:
            # 读取JSON文件
            with open(prompt_dir + '/example.json', 'r', encoding='utf-8') as f:
                data = json.loads(f.read())

            # 提取memory_verify下的字段
            memory_verify = data.get("memory_verify", {})
            source_data = memory_verify.get("source_data", [])
            databasets = memory_verify.get("databasets", [])

            return source_data, databasets

        except Exception as e:
            return [], []

    async def _get_reflexion_data(self, host_id: uuid.UUID) -> List[Any]:
        """
        获取反思数据

        根据配置的反思范围获取需要反思的记忆数据。

        Args:
            host_id: 主机ID

        Returns:
            List[Any]: 反思数据列表
        """



        if self.config.reflexion_range == ReflectionRange.PARTIAL:
            neo4j_query = neo4j_query_part.format(host_id)
            neo4j_statement = neo4j_statement_part.format(host_id)
        elif self.config.reflexion_range == ReflectionRange.ALL:
            neo4j_query = neo4j_query_all.format(host_id)
            neo4j_statement = neo4j_statement_all.format(host_id)
        try:
            result = await self.neo4j_connector.execute_query(neo4j_query)
            result_statement = await self.neo4j_connector.execute_query(neo4j_statement)
            neo4j_databasets = await  self.get_data_func(result)
            neo4j_state = await  self.get_data_statement(result_statement)
            return neo4j_databasets, neo4j_state


        except Exception as e:
            logging.error(f"Neo4j查询失败: {e}")
            return [], []

    async def _detect_conflicts(self, data: List[Any], statement_databasets: List[Any]) -> List[Any]:
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

        # 数据预处理：如果数据量太少，直接返回无冲突
        if len(data) < 2:
            logging.info("数据量不足，无需检测冲突")
            return []

        # 使用转换后的数据
        # print("转换后的数据:", data[:2] if len(data) > 2 else data)  # 只打印前2条避免日志过长
        memory_verify = self.config.memory_verify

        logging.info("====== 冲突检测开始 ======")
        start_time = asyncio.get_event_loop().time()
        quality_assessment = self.config.quality_assessment

        try:
            # 渲染冲突检测提示词
            rendered_prompt = await self.render_evaluate_prompt_func(
                data,
                self.conflict_schema,
                self.config.baseline,
                memory_verify,
                quality_assessment,
                statement_databasets
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

    async def _resolve_conflicts(self, conflicts: List[Any], statement_databasets: List[Any]) -> List[Any]:
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
        baseline = self.config.baseline
        memory_verify = self.config.memory_verify

        # 并行处理每个冲突
        async def _resolve_one(conflict: Any) -> Optional[Dict[str, Any]]:
            """解决单个冲突"""
            async with self._semaphore:
                try:
                    # 渲染反思提示词
                    rendered_prompt = await self.render_reflexion_prompt_func(
                        [conflict],
                        self.reflexion_schema,
                        baseline,
                        memory_verify,
                        statement_databasets
                    )
                    logging.info(f"提示词长度: {len(rendered_prompt)}")

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
        success_count = await neo4j_data(solved_data)
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
