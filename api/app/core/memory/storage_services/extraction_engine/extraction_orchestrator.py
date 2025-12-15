"""
萃取引擎 - 流水线编排器

该模块提供了一个统一的流水线编排器，用于协调整个知识提取流程。
它整合了数据预处理、知识提取、去重消歧等模块，提供统一的执行接口。

主要功能：
1. 协调数据预处理、分块、陈述句提取、三元组提取、时间信息提取等步骤
2. 管理嵌入向量生成
3. 执行两阶段去重和消歧
4. 将提取结果转换为图数据库节点和边
5. 提供错误处理和日志记录
6. 支持试运行模式（不写入数据库）

作者：Memory Refactoring Team
日期：2025-11-21
"""

import asyncio
import logging
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime

from app.core.memory.models.message_models import DialogData
from app.core.memory.models.graph_models import (
    DialogueNode,
    ChunkNode,
    StatementNode,
    ExtractedEntityNode,
    StatementChunkEdge,
    StatementEntityEdge,
    EntityEntityEdge,
)
from app.core.memory.utils.data.ontology import TemporalInfo
from app.core.memory.models.variate_config import (
    ExtractionPipelineConfig,
    StatementExtractionConfig,
)
from app.core.memory.llm_tools.openai_client import LLMClient
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

# 导入各个提取模块
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.statement_extraction import (
    StatementExtractor,
)
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.triplet_extraction import (
    TripletExtractor,
)
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.temporal_extraction import (
    TemporalExtractor,
)
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.embedding_generation import (
    embedding_generation,
    embedding_generation_all,
    generate_entity_embeddings_from_triplets,
)
from app.core.memory.storage_services.extraction_engine.deduplication.two_stage_dedup import (
    dedup_layers_and_merge_and_return,
)
from app.core.memory.storage_services.extraction_engine.pipeline_help import (
    _write_extracted_result_summary,
    export_test_input_doc,
)

# 配置日志
logger = logging.getLogger(__name__)


class ExtractionOrchestrator:
    """
    知识提取流水线编排器

    该类负责协调整个知识提取流程，包括：
    1. 陈述句提取
    2. 三元组提取
    3. 时间信息提取
    4. 嵌入向量生成
    5. 数据赋值到语句
    6. 节点和边的创建
    7. 两阶段去重和消歧
    8. 结果汇总和输出

    Attributes:
        llm_client: LLM 客户端，用于调用大语言模型
        embedder_client: 嵌入模型客户端，用于生成向量嵌入
        connector: Neo4j 连接器，用于数据库操作
        config: 流水线配置
    """

    def __init__(
        self,
        llm_client: LLMClient,
        embedder_client: OpenAIEmbedderClient,
        connector: Neo4jConnector,
        config: Optional[ExtractionPipelineConfig] = None,
    ):
        """
        初始化流水线编排器

        Args:
            llm_client: LLM 客户端
            embedder_client: 嵌入模型客户端
            connector: Neo4j 连接器
            config: 流水线配置，如果为 None 则使用默认配置
        """
        self.llm_client = llm_client
        self.embedder_client = embedder_client
        self.connector = connector
        self.config = config or ExtractionPipelineConfig()
        self.is_pilot_run = False  # 默认非试运行模式

        # 初始化各个提取器
        self.statement_extractor = StatementExtractor(
            llm_client=llm_client,
            config=self.config.statement_extraction,
        )
        self.triplet_extractor = TripletExtractor(llm_client=llm_client)
        self.temporal_extractor = TemporalExtractor(llm_client=llm_client)

        logger.info("ExtractionOrchestrator 初始化完成")

    async def run(
        self,
        dialog_data_list: List[DialogData],
        is_pilot_run: bool = False,
    ) -> Tuple[
        Tuple[List[DialogueNode], List[ChunkNode], List[StatementNode]],
        Tuple[List[ExtractedEntityNode], List[StatementEntityEdge], List[EntityEntityEdge]],
        Tuple[List[ExtractedEntityNode], List[StatementEntityEdge], List[EntityEntityEdge]],
    ]:
        """
        运行完整的知识提取流水线（优化版：并行执行）

        该方法协调所有提取步骤，优化执行顺序：
        1. 陈述句提取
        2. 并行执行：三元组提取 + 时间信息提取 + 陈述句/分块嵌入生成
        3. 实体嵌入生成（依赖三元组）
        4. 数据赋值
        5. 节点和边创建
        6. 两阶段去重
        7. 结果汇总

        Args:
            dialog_data_list: 已分块的对话数据列表
            is_pilot_run: 是否为试运行模式（不写入数据库）

        Returns:
            包含三个元组的元组：
            - 第一个元组：(对话节点列表, 分块节点列表, 陈述句节点列表)
            - 第二个元组：去重前的 (实体节点列表, 陈述句-实体边列表, 实体-实体边列表)
            - 第三个元组：去重后的 (实体节点列表, 陈述句-实体边列表, 实体-实体边列表)
        """
        try:
            # 设置试运行模式标志
            self.is_pilot_run = is_pilot_run
            mode_str = "试运行模式" if is_pilot_run else "正式模式"
            logger.info(f"开始运行知识提取流水线（优化版 - {mode_str}），共 {len(dialog_data_list)} 个对话")

            # 步骤 1: 陈述句提取
            logger.info("步骤 1/6: 陈述句提取（全局分块级并行）")
            dialog_data_list = await self._extract_statements(dialog_data_list)

            # 步骤 2: 并行执行三元组提取、时间信息提取和基础嵌入生成
            logger.info("步骤 2/6: 并行执行三元组提取、时间信息提取和嵌入生成")
            (
                triplet_maps,
                temporal_maps,
                statement_embedding_maps,
                chunk_embedding_maps,
                dialog_embeddings,
            ) = await self._parallel_extract_and_embed(dialog_data_list)

            # 步骤 3: 生成实体嵌入（依赖三元组提取结果）
            logger.info("步骤 3/6: 生成实体嵌入")
            triplet_maps = await self._generate_entity_embeddings(triplet_maps)

            # 步骤 4: 将提取的数据赋值到语句
            logger.info("步骤 4/6: 数据赋值")
            dialog_data_list = await self._assign_extracted_data(
                dialog_data_list,
                temporal_maps,
                triplet_maps,
                statement_embedding_maps,
                chunk_embedding_maps,
                dialog_embeddings,
            )

            # 步骤 5: 创建节点和边
            logger.info("步骤 5/6: 创建节点和边")
            (
                dialogue_nodes,
                chunk_nodes,
                statement_nodes,
                entity_nodes,
                statement_chunk_edges,
                statement_entity_edges,
                entity_entity_edges,
            ) = await self._create_nodes_and_edges(dialog_data_list)

            # 导出去重前的测试输入文档（试运行和正式模式都需要，用于生成结果汇总）
            export_test_input_doc(entity_nodes, statement_entity_edges, entity_entity_edges)

            # 步骤 6: 两阶段去重和消歧
            if is_pilot_run:
                logger.info("步骤 6/6: 去重和消歧（试运行模式：仅第一层去重）")
            else:
                logger.info("步骤 6/6: 两阶段去重和消歧")
            
            result = await self._run_dedup_and_write_summary(
                dialogue_nodes,
                chunk_nodes,
                statement_nodes,
                entity_nodes,
                statement_chunk_edges,
                statement_entity_edges,
                entity_entity_edges,
                dialog_data_list,
            )

            logger.info(f"知识提取流水线运行完成（{mode_str}）")
            return result

        except Exception as e:
            logger.error(f"知识提取流水线运行失败: {e}", exc_info=True)
            raise

    async def _extract_statements(
        self, dialog_data_list: List[DialogData]
    ) -> List[DialogData]:
        """
        从对话中提取陈述句（优化版：全局分块级并行）

        Args:
            dialog_data_list: 对话数据列表

        Returns:
            更新后的对话数据列表（包含提取的陈述句）
        """
        logger.info("开始陈述句提取（全局分块级并行）")

        # 收集所有分块及其元数据
        all_chunks = []
        chunk_metadata = []  # (dialog_idx, chunk_idx)
        
        for d_idx, dialog in enumerate(dialog_data_list):
            dialogue_content = dialog.content if self.config.statement_extraction.include_dialogue_context else None
            for c_idx, chunk in enumerate(dialog.chunks):
                all_chunks.append((chunk, dialog.group_id, dialogue_content))
                chunk_metadata.append((d_idx, c_idx))

        logger.info(f"收集到 {len(all_chunks)} 个分块，开始全局并行提取")

        # 全局并行处理所有分块
        async def extract_for_chunk(chunk_data):
            chunk, group_id, dialogue_content = chunk_data
            try:
                return await self.statement_extractor._extract_statements(chunk, group_id, dialogue_content)
            except Exception as e:
                logger.error(f"分块 {chunk.id} 陈述句提取失败: {e}")
                return []

        tasks = [extract_for_chunk(chunk_data) for chunk_data in all_chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 将结果分配回对话
        for i, result in enumerate(results):
            d_idx, c_idx = chunk_metadata[i]
            if isinstance(result, Exception):
                logger.error(f"分块处理异常: {result}")
                dialog_data_list[d_idx].chunks[c_idx].statements = []
            elif isinstance(result, list):
                dialog_data_list[d_idx].chunks[c_idx].statements = result
            else:
                dialog_data_list[d_idx].chunks[c_idx].statements = []

        # 统计并保存（试运行和正式模式都需要保存，用于生成结果汇总）
        all_statements = []
        for dialog in dialog_data_list:
            for chunk in dialog.chunks:
                if chunk.statements:
                    all_statements.extend(chunk.statements)

        # 保存陈述句到文件（试运行和正式模式都需要）
        self.statement_extractor.save_statements(all_statements)
        
        logger.info(f"陈述句提取完成，共提取 {len(all_statements)} 条陈述句")

        return dialog_data_list

    async def _extract_triplets(
        self, dialog_data_list: List[DialogData]
    ) -> List[Dict[str, Any]]:
        """
        从对话中提取三元组（优化版：全局陈述句级并行）

        Args:
            dialog_data_list: 对话数据列表

        Returns:
            三元组映射列表，每个对话对应一个字典
        """
        logger.info("开始三元组提取（全局陈述句级并行）")

        # 收集所有陈述句及其元数据
        all_statements = []
        statement_metadata = []  # (dialog_idx, statement_id, chunk_content)
        
        for d_idx, dialog in enumerate(dialog_data_list):
            for chunk in dialog.chunks:
                for statement in chunk.statements:
                    all_statements.append((statement, chunk.content))
                    statement_metadata.append((d_idx, statement.id))

        logger.info(f"收集到 {len(all_statements)} 个陈述句，开始全局并行提取三元组")

        # 全局并行处理所有陈述句
        async def extract_for_statement(stmt_data):
            statement, chunk_content = stmt_data
            try:
                return await self.triplet_extractor._extract_triplets(statement, chunk_content)
            except Exception as e:
                logger.error(f"陈述句 {statement.id} 三元组提取失败: {e}")
                from app.core.memory.models.triplet_models import TripletExtractionResponse
                return TripletExtractionResponse(triplets=[], entities=[])

        tasks = [extract_for_statement(stmt_data) for stmt_data in all_statements]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 将结果组织成对话级别的映射
        triplet_maps = [{} for _ in dialog_data_list]
        all_responses = []
        
        for i, result in enumerate(results):
            d_idx, stmt_id = statement_metadata[i]
            if isinstance(result, Exception):
                logger.error(f"陈述句处理异常: {result}")
                from app.core.memory.models.triplet_models import TripletExtractionResponse
                triplet_maps[d_idx][stmt_id] = TripletExtractionResponse(triplets=[], entities=[])
            else:
                triplet_maps[d_idx][stmt_id] = result
                all_responses.append(result)

        # 统计提取结果
        total_triplets = sum(len(m) for m in triplet_maps)
        logger.info(f"三元组提取完成，共提取 {total_triplets} 个三元组")

        # 保存三元组到文件（试运行和正式模式都需要，用于生成结果汇总）
        if all_responses:
            try:
                self.triplet_extractor.save_triplets(all_responses)
                logger.info("三元组数据已保存到文件")
            except Exception as e:
                logger.error(f"保存三元组到文件失败: {e}", exc_info=True)

        return triplet_maps

    async def _extract_temporal(
        self, dialog_data_list: List[DialogData]
    ) -> List[Dict[str, Any]]:
        """
        从对话中提取时间信息（优化版：全局陈述句级并行）

        Args:
            dialog_data_list: 对话数据列表

        Returns:
            时间信息映射列表，每个对话对应一个字典
        """
        logger.info("开始时间信息提取（全局陈述句级并行）")

        # 收集所有需要提取时间的陈述句
        all_statements = []
        statement_metadata = []  # (dialog_idx, statement_id, ref_dates)
        
        for d_idx, dialog in enumerate(dialog_data_list):
            # 获取参考日期
            ref_dates = {}
            if hasattr(dialog, 'metadata') and dialog.metadata:
                if 'conversation_date' in dialog.metadata:
                    ref_dates['conversation_date'] = dialog.metadata['conversation_date']
                if 'publication_date' in dialog.metadata:
                    ref_dates['publication_date'] = dialog.metadata['publication_date']
            
            if not ref_dates:
                from datetime import datetime
                ref_dates = {"today": datetime.now().strftime("%Y-%m-%d")}
            
            for chunk in dialog.chunks:
                for statement in chunk.statements:
                    # 跳过 ATEMPORAL 类型的陈述句
                    from app.core.memory.utils.data.ontology import TemporalInfo
                    if statement.temporal_info != TemporalInfo.ATEMPORAL:
                        all_statements.append((statement, ref_dates))
                        statement_metadata.append((d_idx, statement.id))

        logger.info(f"收集到 {len(all_statements)} 个需要时间提取的陈述句，开始全局并行提取")

        # 全局并行处理所有陈述句
        async def extract_for_statement(stmt_data):
            statement, ref_dates = stmt_data
            try:
                return await self.temporal_extractor._extract_temporal_ranges(statement, ref_dates)
            except Exception as e:
                logger.error(f"陈述句 {statement.id} 时间信息提取失败: {e}")
                from app.core.memory.models.message_models import TemporalValidityRange
                return TemporalValidityRange(valid_at=None, invalid_at=None)

        tasks = [extract_for_statement(stmt_data) for stmt_data in all_statements]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 将结果组织成对话级别的映射
        temporal_maps = [{} for _ in dialog_data_list]
        
        for i, result in enumerate(results):
            d_idx, stmt_id = statement_metadata[i]
            if isinstance(result, Exception):
                logger.error(f"陈述句处理异常: {result}")
                from app.core.memory.models.message_models import TemporalValidityRange
                temporal_maps[d_idx][stmt_id] = TemporalValidityRange(valid_at=None, invalid_at=None)
            else:
                temporal_maps[d_idx][stmt_id] = result

        # 为 ATEMPORAL 陈述句添加空的时间范围
        from app.core.memory.utils.data.ontology import TemporalInfo
        from app.core.memory.models.message_models import TemporalValidityRange
        for d_idx, dialog in enumerate(dialog_data_list):
            for chunk in dialog.chunks:
                for statement in chunk.statements:
                    if statement.temporal_info == TemporalInfo.ATEMPORAL and statement.id not in temporal_maps[d_idx]:
                        temporal_maps[d_idx][statement.id] = TemporalValidityRange(valid_at=None, invalid_at=None)

        # 统计提取结果
        total_temporal = sum(len(m) for m in temporal_maps)
        logger.info(f"时间信息提取完成，共提取 {total_temporal} 个时间范围")

        return temporal_maps

    async def _parallel_extract_and_embed(
        self, dialog_data_list: List[DialogData]
    ) -> Tuple[
        List[Dict[str, Any]],
        List[Dict[str, Any]],
        List[Dict[str, List[float]]],
        List[Dict[str, List[float]]],
        List[List[float]],
    ]:
        """
        并行执行三元组提取、时间信息提取和基础嵌入生成

        这三个任务都依赖陈述句提取的结果，但彼此独立，可以并行执行：
        - 三元组提取：从陈述句中提取实体和关系
        - 时间信息提取：从陈述句中提取时间范围
        - 嵌入生成：为陈述句、分块和对话生成向量（不依赖三元组）

        Args:
            dialog_data_list: 对话数据列表

        Returns:
            五个列表的元组：
            - 三元组映射列表
            - 时间信息映射列表
            - 陈述句嵌入映射列表
            - 分块嵌入映射列表
            - 对话嵌入列表
        """
        logger.info("并行执行：三元组提取 + 时间信息提取 + 基础嵌入生成")

        # 创建三个并行任务
        triplet_task = self._extract_triplets(dialog_data_list)
        temporal_task = self._extract_temporal(dialog_data_list)
        embedding_task = self._generate_basic_embeddings(dialog_data_list)

        # 并行执行
        results = await asyncio.gather(
            triplet_task,
            temporal_task,
            embedding_task,
            return_exceptions=True
        )

        # 解包结果
        triplet_maps = results[0] if not isinstance(results[0], Exception) else [{} for _ in dialog_data_list]
        temporal_maps = results[1] if not isinstance(results[1], Exception) else [{} for _ in dialog_data_list]
        
        if isinstance(results[2], Exception):
            logger.error(f"基础嵌入生成失败: {results[2]}")
            statement_embedding_maps = [{} for _ in dialog_data_list]
            chunk_embedding_maps = [{} for _ in dialog_data_list]
            dialog_embeddings = [[] for _ in dialog_data_list]
        else:
            statement_embedding_maps, chunk_embedding_maps, dialog_embeddings = results[2]

        logger.info("并行任务执行完成")
        return (
            triplet_maps,
            temporal_maps,
            statement_embedding_maps,
            chunk_embedding_maps,
            dialog_embeddings,
        )

    async def _generate_basic_embeddings(
        self, dialog_data_list: List[DialogData]
    ) -> Tuple[List[Dict[str, List[float]]], List[Dict[str, List[float]]], List[List[float]]]:
        """
        生成基础嵌入向量（陈述句、分块、对话）

        这些嵌入不依赖三元组提取结果，可以提前生成
        在试运行模式下，跳过嵌入生成以节省时间

        Args:
            dialog_data_list: 对话数据列表

        Returns:
            三个列表的元组：
            - 陈述句嵌入映射列表
            - 分块嵌入映射列表
            - 对话嵌入列表
        """
        # 试运行模式：跳过嵌入生成
        if self.is_pilot_run:
            logger.info("试运行模式：跳过基础嵌入生成（节省约 20 秒）")
            return (
                [{} for _ in dialog_data_list],
                [{} for _ in dialog_data_list],
                [[] for _ in dialog_data_list],
            )

        logger.info("开始生成基础嵌入向量（陈述句、分块、对话）")

        try:
            # 从 runtime.json 获取嵌入模型配置ID
            from app.core.memory.utils.config import definitions as config_defs
            embedding_id = config_defs.SELECTED_EMBEDDING_ID
            
            if not embedding_id:
                logger.error("未在 runtime.json 中配置 embedding 模型 ID")
                raise ValueError("未配置嵌入模型ID")
            
            # 只生成陈述句、分块和对话的嵌入（不包括实体）
            statement_embedding_maps, chunk_embedding_maps, dialog_embeddings = await embedding_generation(
                dialog_data_list, embedding_id
            )

            # 统计生成结果
            total_statement_embeddings = sum(len(m) for m in statement_embedding_maps)
            total_chunk_embeddings = sum(len(m) for m in chunk_embedding_maps)
            logger.info(
                f"基础嵌入生成完成：{total_statement_embeddings} 个陈述句嵌入，"
                f"{total_chunk_embeddings} 个分块嵌入，{len(dialog_embeddings)} 个对话嵌入"
            )

            return statement_embedding_maps, chunk_embedding_maps, dialog_embeddings

        except Exception as e:
            logger.error(f"基础嵌入生成失败: {e}", exc_info=True)
            # 返回空结果
            return (
                [{} for _ in dialog_data_list],
                [{} for _ in dialog_data_list],
                [[] for _ in dialog_data_list],
            )

    async def _generate_entity_embeddings(
        self, triplet_maps: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        生成实体嵌入向量

        在试运行模式下，跳过实体嵌入生成以节省时间

        Args:
            triplet_maps: 三元组映射列表

        Returns:
            更新后的三元组映射列表（包含实体嵌入）
        """
        # 试运行模式：跳过实体嵌入生成
        if self.is_pilot_run:
            logger.info("试运行模式：跳过实体嵌入生成（节省约 5-8 秒）")
            return triplet_maps

        logger.info("开始生成实体嵌入向量")

        try:
            # 从 runtime.json 获取嵌入模型配置ID
            from app.core.memory.utils.config import definitions as config_defs
            embedding_id = config_defs.SELECTED_EMBEDDING_ID
            
            if not embedding_id:
                logger.error("未在 runtime.json 中配置 embedding 模型 ID")
                return triplet_maps
            
            # 生成实体嵌入
            updated_triplet_maps = await generate_entity_embeddings_from_triplets(
                triplet_maps, embedding_id
            )

            logger.info("实体嵌入生成完成")
            return updated_triplet_maps

        except Exception as e:
            logger.error(f"实体嵌入生成失败: {e}", exc_info=True)
            return triplet_maps



    async def _assign_extracted_data(
        self,
        dialog_data_list: List[DialogData],
        temporal_maps: List[Dict[str, Any]],
        triplet_maps: List[Dict[str, Any]],
        statement_embedding_maps: List[Dict[str, List[float]]],
        chunk_embedding_maps: List[Dict[str, List[float]]],
        dialog_embeddings: List[List[float]],
    ) -> List[DialogData]:
        """
        将提取的数据赋值到语句

        Args:
            dialog_data_list: 对话数据列表
            temporal_maps: 时间信息映射列表
            triplet_maps: 三元组映射列表
            statement_embedding_maps: 陈述句嵌入映射列表
            chunk_embedding_maps: 分块嵌入映射列表
            dialog_embeddings: 对话嵌入列表

        Returns:
            更新后的对话数据列表
        """
        logger.info("开始将提取数据赋值到语句")

        # 确保列表长度匹配
        expected_length = len(dialog_data_list)
        if (
            len(temporal_maps) != expected_length
            or len(triplet_maps) != expected_length
            or len(statement_embedding_maps) != expected_length
            or len(chunk_embedding_maps) != expected_length
            or len(dialog_embeddings) != expected_length
        ):
            logger.warning(
                f"数据大小不匹配 - 对话: {len(dialog_data_list)}, "
                f"时间映射: {len(temporal_maps)}, 三元组映射: {len(triplet_maps)}, "
                f"陈述句嵌入: {len(statement_embedding_maps)}, "
                f"分块嵌入: {len(chunk_embedding_maps)}, "
                f"对话嵌入: {len(dialog_embeddings)}"
            )

        total_statements = 0
        assigned_temporal = 0
        assigned_triplets = 0
        assigned_statement_embeddings = 0
        assigned_chunk_embeddings = 0
        assigned_dialog_embeddings = 0

        # 处理每个对话
        for i, dialog_data in enumerate(dialog_data_list):
            # 检查是否有缺失的数据
            if i >= len(temporal_maps) or i >= len(triplet_maps):
                logger.warning(f"对话 {dialog_data.id} 缺少提取数据，跳过赋值")
                continue

            temporal_map = temporal_maps[i]
            triplet_map = triplet_maps[i]
            statement_embedding_map = statement_embedding_maps[i] if i < len(statement_embedding_maps) else {}
            chunk_embedding_map = chunk_embedding_maps[i] if i < len(chunk_embedding_maps) else {}
            dialog_embedding = dialog_embeddings[i] if i < len(dialog_embeddings) else []

            # 赋值对话嵌入
            if dialog_embedding:
                dialog_data.dialog_embedding = dialog_embedding
                assigned_dialog_embeddings += 1

            # 处理每个分块
            for chunk in dialog_data.chunks:
                # 赋值分块嵌入
                if chunk.id in chunk_embedding_map:
                    chunk.chunk_embedding = chunk_embedding_map[chunk.id]
                    assigned_chunk_embeddings += 1

                # 处理每个陈述句
                for statement in chunk.statements:
                    total_statements += 1

                    # 赋值时间信息
                    if statement.id in temporal_map:
                        statement.temporal_validity = temporal_map[statement.id]
                        assigned_temporal += 1

                    # 赋值三元组
                    if statement.id in triplet_map:
                        statement.triplet_extraction_info = triplet_map[statement.id]
                        assigned_triplets += 1

                    # 赋值陈述句嵌入
                    if statement.id in statement_embedding_map:
                        statement.statement_embedding = statement_embedding_map[statement.id]
                        assigned_statement_embeddings += 1

        logger.info(
            f"数据赋值完成 - 总陈述句: {total_statements}, "
            f"时间信息: {assigned_temporal}, 三元组: {assigned_triplets}, "
            f"陈述句嵌入: {assigned_statement_embeddings}, "
            f"分块嵌入: {assigned_chunk_embeddings}, "
            f"对话嵌入: {assigned_dialog_embeddings}"
        )

        return dialog_data_list

    async def _create_nodes_and_edges(
        self, dialog_data_list: List[DialogData]
    ) -> Tuple[
        List[DialogueNode],
        List[ChunkNode],
        List[StatementNode],
        List[ExtractedEntityNode],
        List[StatementChunkEdge],
        List[StatementEntityEdge],
        List[EntityEntityEdge],
    ]:
        """
        创建图数据库节点和边

        将对话数据转换为图数据库的节点和边结构

        Args:
            dialog_data_list: 对话数据列表

        Returns:
            包含所有节点和边的元组
        """
        logger.info("开始创建节点和边")

        dialogue_nodes = []
        chunk_nodes = []
        statement_nodes = []
        entity_nodes = []
        statement_chunk_edges = []
        statement_entity_edges = []
        entity_entity_edges = []

        # 用于去重的集合
        entity_id_set = set()

        for dialog_data in dialog_data_list:
            # 创建对话节点
            dialogue_node = DialogueNode(
                id=dialog_data.id,
                name=f"Dialog_{dialog_data.id}",  # 添加必需的 name 字段
                ref_id=dialog_data.ref_id,
                group_id=dialog_data.group_id,
                user_id=dialog_data.user_id,
                apply_id=dialog_data.apply_id,
                run_id=dialog_data.run_id,  # 使用 dialog_data 的 run_id
                content=dialog_data.context.content if dialog_data.context else "",
                dialog_embedding=dialog_data.dialog_embedding if hasattr(dialog_data, 'dialog_embedding') else None,
                created_at=dialog_data.created_at,
                expired_at=dialog_data.expired_at,
                metadata=dialog_data.metadata,
                config_id=dialog_data.config_id if hasattr(dialog_data, 'config_id') else None,
            )
            dialogue_nodes.append(dialogue_node)

            # 处理每个分块
            for chunk_idx, chunk in enumerate(dialog_data.chunks):
                # 创建分块节点
                chunk_node = ChunkNode(
                    id=chunk.id,
                    name=f"Chunk_{chunk.id}",  # 添加必需的 name 字段
                    dialog_id=dialog_data.id,
                    group_id=dialog_data.group_id,
                    user_id=dialog_data.user_id,
                    apply_id=dialog_data.apply_id,
                    run_id=dialog_data.run_id,  # 使用 dialog_data 的 run_id
                    content=chunk.content,
                    chunk_embedding=chunk.chunk_embedding,
                    sequence_number=chunk_idx,  # 添加必需的 sequence_number 字段
                    created_at=dialog_data.created_at,
                    expired_at=dialog_data.expired_at,
                    metadata=chunk.metadata,
                )
                chunk_nodes.append(chunk_node)

                # 处理每个陈述句
                for statement in chunk.statements:
                    # 创建陈述句节点
                    statement_node = StatementNode(
                        id=statement.id,
                        name=f"Statement_{statement.id}",  # 添加必需的 name 字段
                        chunk_id=chunk.id,
                        stmt_type=getattr(statement, 'stmt_type', 'general'),  # 添加必需的 stmt_type 字段
                        temporal_info=getattr(statement, 'temporal_info', TemporalInfo.ATEMPORAL),  # 添加必需的 temporal_info 字段
                        connect_strength=statement.connect_strength if statement.connect_strength is not None else 'Strong',  # 添加必需的 connect_strength 字段
                        group_id=dialog_data.group_id,
                        user_id=dialog_data.user_id,
                        apply_id=dialog_data.apply_id,
                        run_id=dialog_data.run_id,  # 使用 dialog_data 的 run_id
                        statement=statement.statement,
                        statement_embedding=statement.statement_embedding,
                        valid_at=statement.temporal_validity.valid_at if hasattr(statement, 'temporal_validity') and statement.temporal_validity else None,
                        invalid_at=statement.temporal_validity.invalid_at if hasattr(statement, 'temporal_validity') and statement.temporal_validity else None,
                        created_at=dialog_data.created_at,
                        expired_at=dialog_data.expired_at,
                        config_id=dialog_data.config_id if hasattr(dialog_data, 'config_id') else None,
                    )
                    statement_nodes.append(statement_node)

                    # 创建陈述句-分块边
                    statement_chunk_edge = StatementChunkEdge(
                        source=statement.id,
                        target=chunk.id,
                        group_id=dialog_data.group_id,
                        user_id=dialog_data.user_id,
                        apply_id=dialog_data.apply_id,
                        run_id=dialog_data.run_id,  # 使用 dialog_data 的 run_id
                        created_at=dialog_data.created_at,
                    )
                    statement_chunk_edges.append(statement_chunk_edge)

                    # 处理三元组信息
                    if statement.triplet_extraction_info:
                        triplet_info = statement.triplet_extraction_info

                        # 创建实体索引到ID的映射
                        entity_idx_to_id = {}
                        
                        # 创建实体节点
                        for entity_idx, entity in enumerate(triplet_info.entities):
                            # 映射实体索引到实体ID
                            entity_idx_to_id[entity.entity_idx] = entity.id
                            
                            if entity.id not in entity_id_set:
                                entity_connect_strength = getattr(entity, 'connect_strength', 'Strong')
                                entity_node = ExtractedEntityNode(
                                    id=entity.id,
                                    name=getattr(entity, 'name', f"Entity_{entity.id}"),  # 使用 name 而不是 entity_name
                                    entity_idx=entity.entity_idx,  # 使用实体自己的 entity_idx
                                    statement_id=statement.id,  # 添加必需的 statement_id 字段
                                    entity_type=getattr(entity, 'type', 'unknown'),  # 使用 type 而不是 entity_type
                                    description=getattr(entity, 'description', ''),  # 添加必需的 description 字段
                                    fact_summary=getattr(entity, 'fact_summary', ''),  # 添加必需的 fact_summary 字段
                                    connect_strength=entity_connect_strength if entity_connect_strength is not None else 'Strong',  # 添加必需的 connect_strength 字段
                                    aliases=getattr(entity, 'aliases', []) or [],  # 传递从三元组提取阶段获取的aliases
                                    name_embedding=getattr(entity, 'name_embedding', None),
                                    group_id=dialog_data.group_id,
                                    user_id=dialog_data.user_id,
                                    apply_id=dialog_data.apply_id,
                                    run_id=dialog_data.run_id,  # 使用 dialog_data 的 run_id
                                    created_at=dialog_data.created_at,
                                    expired_at=dialog_data.expired_at,
                                    config_id=dialog_data.config_id if hasattr(dialog_data, 'config_id') else None,
                                )
                                entity_nodes.append(entity_node)
                                entity_id_set.add(entity.id)

                            # 创建陈述句-实体边
                            entity_connect_strength = getattr(entity, 'connect_strength', 'Strong')
                            statement_entity_edge = StatementEntityEdge(
                                source=statement.id,
                                target=entity.id,
                                connect_strength=entity_connect_strength if entity_connect_strength is not None else 'Strong',
                                group_id=dialog_data.group_id,
                                user_id=dialog_data.user_id,
                                apply_id=dialog_data.apply_id,
                                run_id=dialog_data.run_id,  # 使用 dialog_data 的 run_id
                                created_at=dialog_data.created_at,
                            )
                            statement_entity_edges.append(statement_entity_edge)

                        # 创建实体-实体边（从三元组）
                        for triplet in triplet_info.triplets:
                            # 将三元组中的整数索引映射到实体ID
                            subject_entity_id = entity_idx_to_id.get(triplet.subject_id)
                            object_entity_id = entity_idx_to_id.get(triplet.object_id)
                            
                            # 只有当两个实体ID都存在时才创建边
                            if subject_entity_id and object_entity_id:
                                entity_entity_edge = EntityEntityEdge(
                                    source=subject_entity_id,
                                    target=object_entity_id,
                                    relation_type=triplet.predicate,
                                    statement=statement.statement,
                                    source_statement_id=statement.id,
                                    group_id=dialog_data.group_id,
                                    user_id=dialog_data.user_id,
                                    apply_id=dialog_data.apply_id,
                                    run_id=dialog_data.run_id,  # 使用 dialog_data 的 run_id
                                    created_at=dialog_data.created_at,
                                    expired_at=dialog_data.expired_at,
                                )
                                entity_entity_edges.append(entity_entity_edge)
                            else:
                                logger.warning(
                                    f"跳过三元组 - 无法找到实体ID: subject_id={triplet.subject_id}, "
                                    f"object_id={triplet.object_id}, statement_id={statement.id}"
                                )

        logger.info(
            f"节点和边创建完成 - 对话节点: {len(dialogue_nodes)}, "
            f"分块节点: {len(chunk_nodes)}, 陈述句节点: {len(statement_nodes)}, "
            f"实体节点: {len(entity_nodes)}, 陈述句-分块边: {len(statement_chunk_edges)}, "
            f"陈述句-实体边: {len(statement_entity_edges)}, "
            f"实体-实体边: {len(entity_entity_edges)}"
        )

        return (
            dialogue_nodes,
            chunk_nodes,
            statement_nodes,
            entity_nodes,
            statement_chunk_edges,
            statement_entity_edges,
            entity_entity_edges,
        )

    async def _run_dedup_and_write_summary(
        self,
        dialogue_nodes: List[DialogueNode],
        chunk_nodes: List[ChunkNode],
        statement_nodes: List[StatementNode],
        entity_nodes: List[ExtractedEntityNode],
        statement_chunk_edges: List[StatementChunkEdge],
        statement_entity_edges: List[StatementEntityEdge],
        entity_entity_edges: List[EntityEntityEdge],
        dialog_data_list: List[DialogData],
    ) -> Tuple[
        Tuple[List[DialogueNode], List[ChunkNode], List[StatementNode]],
        Tuple[List[ExtractedEntityNode], List[StatementEntityEdge], List[EntityEntityEdge]],
        Tuple[List[ExtractedEntityNode], List[StatementEntityEdge], List[EntityEntityEdge]],
    ]:
        """
        执行两阶段去重并写入汇总

        Args:
            dialogue_nodes: 对话节点列表
            chunk_nodes: 分块节点列表
            statement_nodes: 陈述句节点列表
            entity_nodes: 实体节点列表
            statement_chunk_edges: 陈述句-分块边列表
            statement_entity_edges: 陈述句-实体边列表
            entity_entity_edges: 实体-实体边列表
            dialog_data_list: 对话数据列表

        Returns:
            包含三个元组的元组：
            - 第一个元组：(对话节点列表, 分块节点列表, 陈述句节点列表)
            - 第二个元组：去重前的 (实体节点列表, 陈述句-实体边列表, 实体-实体边列表)
            - 第三个元组：去重后的 (实体节点列表, 陈述句-实体边列表, 实体-实体边列表)
        """
        logger.info("开始两阶段实体去重和消歧")
        logger.info(
            f"去重前: {len(entity_nodes)} 个实体节点, "
            f"{len(statement_entity_edges)} 条陈述句-实体边, "
            f"{len(entity_entity_edges)} 条实体-实体边"
        )

        try:
            # 在试运行模式下，跳过第二层去重（不查询数据库）
            if self.is_pilot_run:
                logger.info("试运行模式：仅执行第一层去重，跳过第二层数据库去重")
                # 只执行第一层去重
                from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import deduplicate_entities_and_edges
                
                dedup_entity_nodes, dedup_statement_entity_edges, dedup_entity_entity_edges = await deduplicate_entities_and_edges(
                    entity_nodes,
                    statement_entity_edges,
                    entity_entity_edges,
                    report_stage="第一层去重消歧（试运行）",
                    report_append=False,
                    dedup_config=self.config.deduplication,
                )
                
                result_tuple = (
                    dialogue_nodes,
                    chunk_nodes,
                    statement_nodes,
                    dedup_entity_nodes,
                    statement_chunk_edges,
                    dedup_statement_entity_edges,
                    dedup_entity_entity_edges,
                )
                
                final_entity_nodes = dedup_entity_nodes
                final_statement_entity_edges = dedup_statement_entity_edges
                final_entity_entity_edges = dedup_entity_entity_edges
            else:
                # 正式模式：执行完整的两阶段去重
                result_tuple = await dedup_layers_and_merge_and_return(
                    dialogue_nodes,
                    chunk_nodes,
                    statement_nodes,
                    entity_nodes,
                    statement_chunk_edges,
                    statement_entity_edges,
                    entity_entity_edges,
                    dialog_data_list,
                    self.config,
                    self.connector,
                )

                # 解包返回值
                (
                    _,
                    _,
                    _,
                    final_entity_nodes,
                    _,
                    final_statement_entity_edges,
                    final_entity_entity_edges,
                ) = result_tuple

            logger.info(
                f"去重后: {len(final_entity_nodes)} 个实体节点, "
                f"{len(final_statement_entity_edges)} 条陈述句-实体边, "
                f"{len(final_entity_entity_edges)} 条实体-实体边"
            )
            logger.info(
                f"去重效果: 实体减少 {len(entity_nodes) - len(final_entity_nodes)}, "
                f"陈述句-实体边减少 {len(statement_entity_edges) - len(final_statement_entity_edges)}, "
                f"实体-实体边减少 {len(entity_entity_edges) - len(final_entity_entity_edges)}"
            )

            # 写入提取结果汇总（试运行和正式模式都需要生成）
            try:
                from app.core.config import settings
                settings.ensure_memory_output_dir()
                _write_extracted_result_summary(
                    chunk_nodes=chunk_nodes,
                    pipeline_output_dir=settings.MEMORY_OUTPUT_DIR,
                )
                mode_str = "试运行" if self.is_pilot_run else "正式"
                logger.info(f"提取结果汇总已写入（{mode_str}模式）")
            except Exception as e:
                logger.warning(f"写入提取结果汇总失败: {e}")

            return result_tuple

        except Exception as e:
            logger.error(f"两阶段去重失败: {e}", exc_info=True)
            raise


# ============================================================================
# 数据加载和预处理函数
# ============================================================================
# 以下函数从 extraction_pipeline.py 迁移而来，用于数据加载和预处理


async def get_chunked_dialogs(
    chunker_strategy: str = "RecursiveChunker",
    group_id: str = "group_1",
    indices: Optional[List[int]] = None,
) -> List[DialogData]:
    """从测试数据生成分块对话
    
    Args:
        chunker_strategy: 分块策略（默认: RecursiveChunker）
        group_id: 组ID
        indices: 要处理的数据索引列表（可选）
        
    Returns:
        包含分块的 DialogData 对象列表
    """
    import json
    import re
    import os
    
    # 加载测试数据
    testdata_path = os.path.join(os.path.dirname(__file__), "../../data", "testdata.json")
    with open(testdata_path, "r", encoding="utf-8") as f:
        test_data = [json.loads(line) for line in f]

    dialog_data_list = []

    if indices is not None:
        # 选择特定索引
        selected_data = [test_data[i] for i in indices if 0 <= i < len(test_data)]
    else:
        # 默认使用所有数据
        selected_data = test_data
        
    for data in selected_data:
        # 解析对话上下文
        context_text = data["context"]

        # 从context文本中解析日期
        conv_date: Optional[str] = None
        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", context_text)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            conv_date = f"{y:04d}-{mo:02d}-{d:02d}"
        else:
            m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", context_text)
            if m:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                conv_date = f"{y:04d}-{mo:02d}-{d:02d}"
                
        dialog_metadata: Dict[str, Any] = {}
        if conv_date:
            dialog_metadata["conversation_date"] = conv_date
            dialog_metadata["publication_date"] = conv_date

        # 分割对话为消息
        lines = context_text.split("\n")
        messages = []

        # 解析对话行
        for raw_line in lines:
            line = raw_line.strip()
            match = re.match(r'^[""]?(用户|AI)\s*[：:]\s*(.*)$', line)
            if match:
                role = match.group(1)
                msg = match.group(2).strip().rstrip('""')
                from app.core.memory.models.message_models import ConversationMessage
                messages.append(ConversationMessage(role=role, msg=msg))

        # 创建 DialogData
        from app.core.memory.models.message_models import ConversationContext
        conversation_context = ConversationContext(msgs=messages)
        dialog_data = DialogData(
            context=conversation_context,
            ref_id=data['id'],
            group_id=group_id,
            metadata=dialog_metadata,
        )
        
        # 创建分块器并处理对话
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
        chunker = DialogueChunker(chunker_strategy)
        extracted_chunks = await chunker.process_dialogue(dialog_data)
        dialog_data.chunks = extracted_chunks

        dialog_data_list.append(dialog_data)

    # 保存输出
    def serialize_datetime(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(
            f"Object of type {obj.__class__.__name__} is not JSON serializable"
        )

    combined_output = [dd.model_dump() for dd in dialog_data_list]
    from app.core.config import settings
    settings.ensure_memory_output_dir()
    output_path = settings.get_memory_output_path("chunker_test_output.txt")
    
    import json
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            combined_output, f, ensure_ascii=False, indent=4, default=serialize_datetime
        )

    return dialog_data_list


def preprocess_data(
    input_path: Optional[str] = None, 
    output_path: Optional[str] = None,
    skip_cleaning: bool = True,
    indices: Optional[List[int]] = None
) -> List[DialogData]:
    """数据预处理
    
    Args:
        input_path: 原始数据路径
        output_path: 预处理后数据保存路径
        skip_cleaning: 是否跳过数据清洗步骤（默认False）
        indices: 要处理的数据索引列表
        
    Returns:
        经过清洗转换后的 DialogData 列表
    """
    print("\n=== 数据预处理 ===")
    from app.core.memory.storage_services.extraction_engine.data_preprocessing.data_preprocessor import DataPreprocessor
    preprocessor = DataPreprocessor()
    try:
        cleaned_data = preprocessor.preprocess(input_path=input_path, output_path=output_path, skip_cleaning=skip_cleaning, indices=indices)
        print(f"数据预处理完成！共处理了 {len(cleaned_data)} 条对话数据")
        return cleaned_data
    except Exception as e:
        print(f"数据预处理过程中出现错误: {e}")
        raise


async def get_chunked_dialogs_from_preprocessed(
    data: List[DialogData],
    chunker_strategy: str = "RecursiveChunker",
    llm_client: Optional[Any] = None,
) -> List[DialogData]:
    """从预处理后的数据中生成分块
    
    Args:
        data: 预处理后的 DialogData 列表
        chunker_strategy: 分块策略
        llm_client: LLM 客户端（用于 LLMChunker）
        
    Returns:
        带 chunks 的 DialogData 列表
    """
    print(f"\n=== 批量对话分块处理 (使用 {chunker_strategy}) ===")
    if not data:
        raise ValueError("预处理数据为空，无法进行分块")
        
    all_chunked_dialogs: List[DialogData] = []
    from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
    
    for dialog_data in data:
        chunker = DialogueChunker(chunker_strategy, llm_client=llm_client)
        chunks = await chunker.process_dialogue(dialog_data)
        dialog_data.chunks = chunks
        all_chunked_dialogs.append(dialog_data)
        
    return all_chunked_dialogs


async def get_chunked_dialogs_with_preprocessing(
    chunker_strategy: str = "RecursiveChunker",
    group_id: str = "default",
    user_id: str = "default",
    apply_id: str = "default",
    indices: Optional[List[int]] = None,
    input_data_path: Optional[str] = None,
    llm_client: Optional[Any] = None,
    skip_cleaning: bool = True,
) -> List[DialogData]:
    """包含数据预处理步骤的完整分块流程
    
    Args:
        chunker_strategy: 分块策略
        group_id: 组ID
        user_id: 用户ID
        apply_id: 应用ID
        indices: 要处理的数据索引列表
        input_data_path: 输入数据路径
        llm_client: LLM 客户端
        skip_cleaning: 是否跳过数据清洗步骤（默认False）
        
    Returns:
        带 chunks 的 DialogData 列表
    """
    import os
    print("\n=== 完整数据处理流程（包含预处理）===")

    if input_data_path is None:
        input_data_path = os.path.join(
            os.path.dirname(__file__), "../../data", "testdata.json"
        )
        
    # 步骤1: 数据预处理（包含索引筛选）
    from app.core.config import settings
    settings.ensure_memory_output_dir()
    preprocessed_data = preprocess_data(
        input_path=input_data_path,
        output_path=settings.get_memory_output_path("preprocessed_data.json"),
        skip_cleaning=skip_cleaning,
        indices=indices,
    )
            
    # 设置 group_id, user_id, apply_id
    for dd in preprocessed_data:
        dd.group_id = group_id
        dd.user_id = user_id
        dd.apply_id = apply_id
        
    # 步骤2: 语义剪枝
    try:
        from app.core.memory.storage_services.extraction_engine.data_preprocessing.data_pruning import SemanticPruner
        pruner = SemanticPruner(llm_client=llm_client)
        
        # 记录单对话场景下剪枝前的消息数量
        single_dialog_original_msgs = None
        if len(preprocessed_data) == 1 and preprocessed_data[0].context:
            single_dialog_original_msgs = len(preprocessed_data[0].context.msgs)

        preprocessed_data = await pruner.prune_dataset(preprocessed_data)
        
        # 单对话：打印清洗与剪枝信息
        if len(preprocessed_data) == 1 and single_dialog_original_msgs is not None:
            remaining_msgs = len(preprocessed_data[0].context.msgs) if preprocessed_data[0].context else 0
            deleted_msgs = max(0, single_dialog_original_msgs - remaining_msgs)
            print(
                f"语义剪枝完成！剩余 1 条对话！原始消息数：{single_dialog_original_msgs}，"
                f"保留消息数：{remaining_msgs}，删除 {deleted_msgs} 条。"
            )
        else:
            print(f"语义剪枝完成！剩余 {len(preprocessed_data)} 条对话")
            
        # 保存剪枝后的数据
        try:
            from app.core.memory.storage_services.extraction_engine.data_preprocessing.data_preprocessor import DataPreprocessor
            pruned_output_path = settings.get_memory_output_path("pruned_data.json")
            dp = DataPreprocessor(output_file_path=pruned_output_path)
            dp.save_data(preprocessed_data, output_path=pruned_output_path)
        except Exception as se:
            print(f"保存剪枝结果失败：{se}")
    except Exception as e:
        print(f"语义剪枝过程中出现错误，跳过剪枝: {e}")
        
    # 步骤3: 对话分块
    return await get_chunked_dialogs_from_preprocessed(
        preprocessed_data,
        chunker_strategy=chunker_strategy,
        llm_client=llm_client,
    )
