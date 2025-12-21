"""
MemSci 记忆系统主入口 - 重构版本

该模块是重构后的记忆系统主入口，使用新的模块化架构。
旧版本入口（app/core/memory/src/main.py）已删除。

主要功能：
1. 协调整个知识提取流水线
2. 支持试运行模式和正常运行模式
3. 使用重构后的 storage_services 模块
4. 提供统一的配置管理和日志记录

作者：Lance77
日期：2025-11-22
"""

# 必须在最开始禁用 LangSmith 追踪，避免速率限制错误
import os
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGCHAIN_TRACING"] = "false"
import asyncio
import time
from datetime import datetime
from typing import Optional, Callable, Awaitable
from dotenv import load_dotenv

# 导入重构后的模块
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import ExtractionOrchestrator
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.memory.utils.config.config_utils import get_embedder_config
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.memory.models.message_models import ConversationMessage, ConversationContext, DialogData
from app.core.memory.models.variate_config import ExtractionPipelineConfig

# 导入数据加载函数
from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import (
    get_chunked_dialogs_with_preprocessing,
    get_chunked_dialogs_from_preprocessed,
)
# 导入配置模块（而不是直接导入变量）
from app.core.memory.utils.config import definitions as config_defs
from app.core.logging_config import get_memory_logger, log_time

load_dotenv()

logger = get_memory_logger(__name__)





async def main(
    # Required configuration parameters (no longer from global variables)
    chunker_strategy: str,
    group_id: str,
    user_id: str,
    apply_id: str,
    llm_model_id: str,
    embedding_model_id: str,
    # Optional parameters
    dialogue_text: Optional[str] = None, 
    is_pilot_run: bool = False,
    progress_callback: Optional[Callable[[str, str, Optional[dict]], Awaitable[None]]] = None
):
    """
    记忆系统主流程 - 重构版本 (Updated to eliminate global variables)

    该函数是重构后的主入口，使用新的模块化架构。
    Global variables have been eliminated in favor of explicit parameters.

    Args:
        chunker_strategy: Chunking strategy to use (required)
        group_id: Group ID for the operation (required)
        user_id: User ID for the operation (required)
        apply_id: Application ID for the operation (required)
        llm_model_id: LLM model ID to use (required)
        embedding_model_id: Embedding model ID to use (required)
        dialogue_text: 输入的对话文本（可选，用于试运行模式）
        is_pilot_run: 是否为试运行模式
            - True: 试运行模式，不保存到 Neo4j
            - False: 正常运行模式，保存到 Neo4j
        progress_callback: 可选的进度回调函数
            - 类型: Callable[[str, str, Optional[dict]], Awaitable[None]]
            - 参数1 (stage): 当前处理阶段标识符
            - 参数2 (message): 人类可读的进度消息
            - 参数3 (data): 可选的附加数据字典，包含详细的进度信息或结果
            - 在管线关键点调用以报告进度和结果数据

    工作流程：
        1. 初始化客户端和配置
        2. 加载或准备数据
        3. 执行知识提取流水线
        4. 保存结果（正常模式）或输出结果（试运行模式）
    """
    print("=" * 60)
    print("MemSci 知识提取流水线 - 重构版本")
    print("=" * 60)
    print(f"运行模式: {'试运行（不保存到Neo4j）' if is_pilot_run else '正常运行（保存到Neo4j）'}")
    print("Using chunker strategy:", chunker_strategy)
    print("Using group ID:", group_id)
    print("Using model ID:", llm_model_id)
    print("Using embedding model ID:", embedding_model_id)
    print("=" * 60)

    # 初始化日志
    log_file = "logs/time.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n=== Pipeline Run Started: {timestamp} ({'Pilot Run' if is_pilot_run else 'Normal Run'}) ===\n")
    
    pipeline_start = time.time()

    try:
        # 步骤 1: 初始化客户端
        logger.info("Initializing clients...")
        step_start = time.time()
        
        llm_client = get_llm_client(llm_model_id)
        
        # 获取 embedder 配置并转换为 RedBearModelConfig 对象
        from app.core.models.base import RedBearModelConfig
        embedder_config_dict = get_embedder_config(embedding_model_id)
        embedder_config = RedBearModelConfig(**embedder_config_dict)
        embedder_client = OpenAIEmbedderClient(embedder_config)
        
        neo4j_connector = Neo4jConnector()
        
        log_time("Client Initialization", time.time() - step_start, log_file)

        # 步骤 2: 加载或准备数据
        logger.info("Loading data...")
        logger.info(f"[MAIN] dialogue_text type={type(dialogue_text)}, length={len(dialogue_text) if dialogue_text else 0}, is_pilot_run={is_pilot_run}")
        logger.info(f"[MAIN] dialogue_text preview: {repr(dialogue_text)[:200] if dialogue_text else 'None'}")
        logger.info(f"[MAIN] Condition check: dialogue_text={bool(dialogue_text)}, isinstance={isinstance(dialogue_text, str) if dialogue_text else False}, strip={bool(dialogue_text.strip()) if dialogue_text and isinstance(dialogue_text, str) else False}")
        step_start = time.time()
        
        if dialogue_text and isinstance(dialogue_text, str) and dialogue_text.strip():
            # 试运行模式：处理前端传入的对话文本
            logger.info("[MAIN] ✓ Using frontend dialogue text (pilot run mode)")
            import re
            
            # 解析对话文本，支持 "用户:" 和 "AI:" 格式
            pattern = r"(用户|AI)[：:]\s*([^\n]+(?:\n(?!(?:用户|AI)[：:])[^\n]*)*?)"
            matches = re.findall(pattern, dialogue_text, re.MULTILINE | re.DOTALL)
            messages = [
                ConversationMessage(role=r, msg=c.strip())
                for r, c in matches if c.strip()
            ]
            
            # 如果没有匹配到格式化的对话，将整个文本作为用户消息
            if not messages:
                messages = [ConversationMessage(role="用户", msg=dialogue_text.strip())]
            
            # 创建对话上下文和对话数据
            context = ConversationContext(msgs=messages)
            dialog = DialogData(
                context=context,
                ref_id="pilot_dialog_1",
                group_id=group_id,
                user_id=user_id,
                apply_id=apply_id,
                metadata={"source": "pilot_run", "input_type": "frontend_text"}
            )
            
            # 进度回调：开始预处理文本
            if progress_callback:
                await progress_callback("text_preprocessing", "开始预处理文本...")
            
            # 对前端传入的对话进行分块处理
            chunked_dialogs = await get_chunked_dialogs_from_preprocessed(
                data=[dialog],
                chunker_strategy=chunker_strategy,
                llm_client=llm_client,
            )
            logger.info(f"Processed frontend dialogue text: {len(messages)} messages")
            
            # 进度回调：输出每个分块的结果
            if progress_callback:
                for dialog in chunked_dialogs:
                    for i, chunk in enumerate(dialog.chunks):
                        chunk_result = {
                            "chunk_index": i + 1,
                            "content": chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content,
                            "full_length": len(chunk.content),
                            "dialog_id": dialog.id,
                            "chunker_strategy": chunker_strategy
                        }
                        await progress_callback("text_preprocessing_result", f"分块 {i + 1} 处理完成", chunk_result)
                
                # 进度回调：预处理文本完成
                preprocessing_summary = {
                    "total_chunks": sum(len(dialog.chunks) for dialog in chunked_dialogs),
                    "total_dialogs": len(chunked_dialogs),
                    "chunker_strategy": chunker_strategy
                }
                await progress_callback("text_preprocessing_complete", "预处理文本完成", preprocessing_summary)
        else:
            # 正常运行模式：从 testdata.json 文件加载
            logger.warning("[MAIN] ✗ Falling back to testdata.json (dialogue_text not provided or empty)")
            logger.info("Loading data from testdata.json...")
            test_data_path = os.path.join(
                os.path.dirname(__file__), "data", "testdata.json"
            )
            
            if not os.path.exists(test_data_path):
                raise FileNotFoundError(f"Test data file not found: {test_data_path}")
            
            # 进度回调：开始预处理文本
            if progress_callback:
                await progress_callback("text_preprocessing", "开始预处理文本...")
            
            chunked_dialogs = await get_chunked_dialogs_with_preprocessing(
                chunker_strategy=chunker_strategy,
                group_id=group_id,
                user_id=user_id,
                apply_id=apply_id,
                indices=None,
                input_data_path=test_data_path,
                llm_client=llm_client,
                skip_cleaning=True,
            )
            logger.info(f"Loaded {len(chunked_dialogs)} dialogues from testdata.json")
            
            # 进度回调：输出每个分块的结果
            if progress_callback:
                for dialog in chunked_dialogs:
                    for i, chunk in enumerate(dialog.chunks):
                        chunk_result = {
                            "chunk_index": i + 1,
                            "content": chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content,
                            "full_length": len(chunk.content),
                            "dialog_id": dialog.id,
                            "chunker_strategy": chunker_strategy
                        }
                        await progress_callback("text_preprocessing_result", f"分块 {i + 1} 处理完成", chunk_result)
                
                # 进度回调：预处理文本完成
                preprocessing_summary = {
                    "total_chunks": sum(len(dialog.chunks) for dialog in chunked_dialogs),
                    "total_dialogs": len(chunked_dialogs),
                    "chunker_strategy": chunker_strategy
                }
                await progress_callback("text_preprocessing_complete", "预处理文本完成", preprocessing_summary)
        
        log_time("Data Loading & Chunking", time.time() - step_start, log_file)

        # 步骤 3: 初始化流水线编排器
        logger.info("Initializing extraction orchestrator...")
        step_start = time.time()
        
        # 从 runtime.json 加载配置（已经过数据库覆写）
        from app.core.memory.utils.config.config_utils import get_pipeline_config
        config = get_pipeline_config()
        
        logger.info(f"Pipeline config loaded: enable_llm_dedup_blockwise={config.deduplication.enable_llm_dedup_blockwise}, enable_llm_disambiguation={config.deduplication.enable_llm_disambiguation}")
        
        orchestrator = ExtractionOrchestrator(
            llm_client=llm_client,
            embedder_client=embedder_client,
            connector=neo4j_connector,
            config=config,
            progress_callback=progress_callback,  # 传递进度回调
            embedding_id=embedding_model_id,  # 传递嵌入模型ID
        )
        
        log_time("Orchestrator Initialization", time.time() - step_start, log_file)

        # 步骤 4: 执行知识提取流水线
        logger.info("Running extraction pipeline...")
        step_start = time.time()
        
        
        # 进度回调：正在知识抽取
        if progress_callback:
            await progress_callback("knowledge_extraction", "正在知识抽取...")
        
        extraction_result = await orchestrator.run(
            dialog_data_list=chunked_dialogs,
            is_pilot_run=is_pilot_run,  # 传递试运行模式标志
        )
        
        # 解包 extraction_result tuple
        # extraction_result 是一个包含 7 个元素的 tuple:
        # (dialogue_nodes, chunk_nodes, statement_nodes, entity_nodes, 
        #  statement_chunk_edges, statement_entity_edges, entity_edges)
        (
            dialogue_nodes,
            chunk_nodes,
            statement_nodes,
            entity_nodes,
            statement_chunk_edges,
            statement_entity_edges,
            entity_edges,
        ) = extraction_result
        
        log_time("Extraction Pipeline", time.time() - step_start, log_file)
        
        # 进度回调：生成结果
        if progress_callback:
            await progress_callback("generating_results", "正在生成结果...")
        

        # 步骤 5: 保存结果或输出结果
        if is_pilot_run:
            logger.info("Pilot run mode: Skipping Neo4j save")
            print("\n试运行模式：跳过 Neo4j 保存，流水线处理完成。")
            print("提取结果已生成，可在相关输出中查看。")
        else:
            logger.info("Normal mode: Saving to Neo4j...")
            step_start = time.time()
            
            # 创建索引和约束
            try:
                from app.repositories.neo4j.create_indexes import (
                    create_fulltext_indexes,
                    create_unique_constraints,
                )
                await create_fulltext_indexes()
                await create_unique_constraints()
                logger.info("Successfully created indexes and constraints")
            except Exception as e:
                logger.error(f"Error creating indexes/constraints: {e}")
            
            # 保存数据到 Neo4j
            try:
                from app.repositories.neo4j.graph_saver import (
                    save_dialog_and_statements_to_neo4j,
                )
                
                success = await save_dialog_and_statements_to_neo4j(
                    dialogue_nodes=dialogue_nodes,
                    chunk_nodes=chunk_nodes,
                    statement_nodes=statement_nodes,
                    entity_nodes=entity_nodes,
                    statement_chunk_edges=statement_chunk_edges,
                    statement_entity_edges=statement_entity_edges,
                    entity_edges=entity_edges,
                    connector=neo4j_connector,
                )
                
                if success:
                    logger.info("Successfully saved all data to Neo4j")
                    print("\n✓ 成功保存所有数据到 Neo4j")
                else:
                    logger.warning("Failed to save some data to Neo4j")
                    print("\n⚠ 部分数据保存到 Neo4j 失败")
            except Exception as e:
                logger.error(f"Error saving to Neo4j: {e}", exc_info=True)
                print(f"\n✗ 保存到 Neo4j 失败: {e}")
            
            log_time("Neo4j Database Save", time.time() - step_start, log_file)

        # 步骤 6: 生成记忆摘要（可选）
        try:
            logger.info("Generating memory summaries...")
            step_start = time.time()
            
            from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import (
                Memory_summary_generation,
            )
            from app.repositories.neo4j.add_nodes import add_memory_summary_nodes
            from app.repositories.neo4j.add_edges import (
                add_memory_summary_statement_edges,
            )
            
            summaries = await Memory_summary_generation(
                chunked_dialogs, llm_client=llm_client, embedding_id=embedding_model_id
            )
            
            if not is_pilot_run:
                # 保存记忆摘要到 Neo4j
                ms_connector = Neo4jConnector()
                try:
                    await add_memory_summary_nodes(summaries, ms_connector)
                    await add_memory_summary_statement_edges(summaries, ms_connector)
                finally:
                    await ms_connector.close()
            
            log_time("Memory Summary Generation", time.time() - step_start, log_file)
        except Exception as e:
            logger.error(f"Memory summary step failed: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}", exc_info=True)
        print(f"\n✗ 流水线执行失败: {e}")
        raise
    finally:
        # 清理资源
        try:
            await neo4j_connector.close()
        except Exception:
            pass

    # 记录总时间
    total_time = time.time() - pipeline_start
    log_time("TOTAL PIPELINE TIME", total_time, log_file)

    # 添加完成标记
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"=== Pipeline Run Completed: {timestamp} ===\n\n")

    logger.info("=== Pipeline Complete ===")
    logger.info(f"Total execution time: {total_time:.2f} seconds")
    logger.info(f"Timing details saved to: {log_file}")
    
    print("\n" + "=" * 60)
    print("✓ 流水线执行完成")
    print(f"✓ 总耗时: {total_time:.2f} 秒")
    print(f"✓ 详细日志: {log_file}")
    print("=" * 60)


if __name__ == "__main__":
    print("⚠️  Warning: This script now requires explicit configuration parameters.")
    print("Global variables have been removed. Please provide configuration parameters.")
    print("Example usage:")
    print("  asyncio.run(main(")
    print("    chunker_strategy='RecursiveChunker',")
    print("    group_id='your_group_id',")
    print("    user_id='your_user_id',")
    print("    apply_id='your_apply_id',")
    print("    llm_model_id='your_llm_id',")
    print("    embedding_model_id='your_embedding_id'")
    print("  ))")
    
    # This will fail because global variables are removed
    raise RuntimeError("Global variables removed. Please provide explicit configuration parameters.")
