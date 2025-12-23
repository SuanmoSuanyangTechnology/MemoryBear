import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.memory.llm_tools.openai_client import LLMClient
from app.core.memory.models.message_models import (
    ConversationContext,
    ConversationMessage,
    DialogData,
)

# 使用新的模块化架构
from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import (
    ExtractionOrchestrator,
)
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import (
    DialogueChunker,
)
from app.core.memory.utils.config.definitions import (
    SELECTED_CHUNKER_STRATEGY,
    SELECTED_EMBEDDING_ID,
)
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context

# Import from database module
from app.repositories.neo4j.graph_saver import save_dialog_and_statements_to_neo4j
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

# Cypher queries for evaluation
# Note: Entity, chunk, and dialogue search queries have been moved to evaluation/dialogue_queries.py


async def ingest_contexts_via_full_pipeline(
    contexts: List[str],
    group_id: str,
    chunker_strategy: str | None = None,
    embedding_name: str | None = None,
    save_chunk_output: bool = False,
    save_chunk_output_path: str | None = None,
) -> bool:
    """DEPRECATED: 此函数使用旧的流水线架构，建议使用新的 ExtractionOrchestrator
    
    Run the full extraction pipeline on provided dialogue contexts and save to Neo4j.
    This function mirrors the steps in main(), but starts from raw text contexts.
    Args:
        contexts: List of dialogue texts, each containing lines like "role: message".
        group_id: Group ID to assign to generated DialogData and graph nodes.
        chunker_strategy: Optional chunker strategy; defaults to SELECTED_CHUNKER_STRATEGY.
        embedding_name: Optional embedding model ID; defaults to SELECTED_EMBEDDING_ID.
        save_chunk_output: If True, write chunked DialogData list to a JSON file for debugging.
        save_chunk_output_path: Optional output path; defaults to src/chunker_test_output.txt.
    Returns:
        True if data saved successfully, False otherwise.
    """
    chunker_strategy = chunker_strategy or SELECTED_CHUNKER_STRATEGY
    embedding_name = embedding_name or SELECTED_EMBEDDING_ID

    # Initialize llm client with graceful fallback
    llm_client = None
    llm_available = True
    try:
        from app.core.memory.utils.config import definitions as config_defs
        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            llm_client = factory.get_llm_client(config_defs.SELECTED_LLM_ID)
    except Exception as e:
        print(f"[Ingestion] LLM client unavailable, will skip LLM-dependent steps: {e}")
        llm_available = False

    # Step A: Build DialogData list from contexts with robust parsing
    chunker = DialogueChunker(chunker_strategy)
    dialog_data_list: List[DialogData] = []

    for idx, ctx in enumerate(contexts):
        messages: List[ConversationMessage] = []

        # Improved parsing: capture multi-line message blocks, normalize roles
        pattern = r"^\s*(用户|AI|assistant|user)\s*[：:]\s*(.+?)(?=\n\s*(?:用户|AI|assistant|user)\s*[：:]|\Z)"
        matches = list(re.finditer(pattern, ctx, flags=re.MULTILINE | re.DOTALL))

        if matches:
            for m in matches:
                raw_role = m.group(1).strip()
                content = m.group(2).strip()
                norm_role = "AI" if raw_role.lower() in ("ai", "assistant") else "用户"
                messages.append(ConversationMessage(role=norm_role, msg=content))
        else:
            # Fallback: line-by-line parsing
            for raw in ctx.split("\n"):
                line = raw.strip()
                if not line:
                    continue
                m = re.match(r'^\s*([^:：]+)\s*[：:]\s*(.+)$', line)
                if m:
                    role = m.group(1).strip()
                    msg = m.group(2).strip()
                    norm_role = "AI" if role.lower() in ("ai", "assistant") else "用户"
                    messages.append(ConversationMessage(role=norm_role, msg=msg))
                else:
                    # Final fallback: treat as user message
                    default_role = "AI" if re.match(r'^\s*(assistant|AI)\b', line, flags=re.IGNORECASE) else "用户"
                    messages.append(ConversationMessage(role=default_role, msg=line))

        context_model = ConversationContext(msgs=messages)
        dialog = DialogData(
            context=context_model,
            ref_id=f"pipeline_item_{idx}",
            group_id=group_id,
            user_id="default_user",
            apply_id="default_application",
        )
        # Generate chunks
        dialog.chunks = await chunker.process_dialogue(dialog)
        dialog_data_list.append(dialog)

    if not dialog_data_list:
        print("No dialogs to process for ingestion.")
        return False

    # Optionally save chunking outputs for debugging
    if save_chunk_output:
        try:
            def _serialize_datetime(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

            from app.core.config import settings
            settings.ensure_memory_output_dir()
            default_path = settings.get_memory_output_path("chunker_test_output.txt")
            out_path = save_chunk_output_path or default_path

            combined_output = [dd.model_dump() for dd in dialog_data_list]
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(combined_output, f, ensure_ascii=False, indent=4, default=_serialize_datetime)
            print(f"Saved chunking results to: {out_path}")
        except Exception as e:
            print(f"Failed to save chunking results: {e}")

    # Step B-G: 使用新的 ExtractionOrchestrator 执行完整的提取流水线
    if not llm_available:
        print("[Ingestion] Skipping extraction pipeline (no LLM).")
        return False
    
    # 初始化 embedder 客户端
    from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
    from app.core.models.base import RedBearModelConfig
    from app.services.memory_config_service import MemoryConfigService
    
    try:
        with get_db_context() as db:
            embedder_config_dict = MemoryConfigService(db).get_embedder_config(embedding_name or SELECTED_EMBEDDING_ID)
        embedder_config = RedBearModelConfig(**embedder_config_dict)
        embedder_client = OpenAIEmbedderClient(embedder_config)
    except Exception as e:
        print(f"[Ingestion] Failed to initialize embedder client: {e}")
        print("[Ingestion] Skipping extraction pipeline (embedder initialization failed).")
        return False
    
    connector = Neo4jConnector()
    
    # 初始化并运行 ExtractionOrchestrator
    from app.core.memory.utils.config.config_utils import get_pipeline_config
    config = get_pipeline_config()
    
    orchestrator = ExtractionOrchestrator(
        llm_client=llm_client,
        embedder_client=embedder_client,
        connector=connector,
        config=config,
    )
    
    # 创建一个包装的 orchestrator 来修复时间提取器的输出
    # 保存原始的 _assign_extracted_data 方法
    original_assign = orchestrator._assign_extracted_data
    
    def clean_temporal_value(value):
        """清理 temporal_validity 字段的值，将无效值转换为 None"""
        if value is None:
            return None
        if isinstance(value, str):
            # 处理字符串形式的 'null', 'None', 空字符串等
            if value.lower() in ('null', 'none', '') or value.strip() == '':
                return None
        return value
    
    async def patched_assign_extracted_data(*args, **kwargs):
        """包装方法：在赋值后清理 temporal_validity 中的无效字符串"""
        result = await original_assign(*args, **kwargs)
        
        # 清理返回的 dialog_data_list 中的 temporal_validity
        for dialog in result:
            if hasattr(dialog, 'chunks') and dialog.chunks:
                for chunk in dialog.chunks:
                    if hasattr(chunk, 'statements') and chunk.statements:
                        for statement in chunk.statements:
                            if hasattr(statement, 'temporal_validity') and statement.temporal_validity:
                                tv = statement.temporal_validity
                                # 清理 valid_at 和 invalid_at
                                if hasattr(tv, 'valid_at'):
                                    tv.valid_at = clean_temporal_value(tv.valid_at)
                                if hasattr(tv, 'invalid_at'):
                                    tv.invalid_at = clean_temporal_value(tv.invalid_at)
        return result
    
    # 替换方法
    orchestrator._assign_extracted_data = patched_assign_extracted_data
    
    # 同时包装 _create_nodes_and_edges 方法，在创建节点前再次清理
    original_create = orchestrator._create_nodes_and_edges
    
    async def patched_create_nodes_and_edges(dialog_data_list_arg):
        """包装方法：在创建节点前再次清理 temporal_validity"""
        # 最后一次清理，确保万无一失
        for dialog in dialog_data_list_arg:
            if hasattr(dialog, 'chunks') and dialog.chunks:
                for chunk in dialog.chunks:
                    if hasattr(chunk, 'statements') and chunk.statements:
                        for statement in chunk.statements:
                            if hasattr(statement, 'temporal_validity') and statement.temporal_validity:
                                tv = statement.temporal_validity
                                if hasattr(tv, 'valid_at'):
                                    tv.valid_at = clean_temporal_value(tv.valid_at)
                                if hasattr(tv, 'invalid_at'):
                                    tv.invalid_at = clean_temporal_value(tv.invalid_at)
        
        return await original_create(dialog_data_list_arg)
    
    orchestrator._create_nodes_and_edges = patched_create_nodes_and_edges
    
    # 运行完整的提取流水线
    # orchestrator.run 返回 7 个元素的元组
    result = await orchestrator.run(dialog_data_list, is_pilot_run=False)
    (
        dialogue_nodes,
        chunk_nodes,
        statement_nodes,
        entity_nodes,
        statement_chunk_edges,
        statement_entity_edges,
        entity_entity_edges,
    ) = result
    
    # statement_chunk_edges 已经由 orchestrator 创建，无需重复创建

    # Step G: 生成记忆摘要
    print("[Ingestion] Generating memory summaries...")
    try:
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import (
            memory_summary_generation,
        )
        from app.repositories.neo4j.add_edges import add_memory_summary_statement_edges
        from app.repositories.neo4j.add_nodes import add_memory_summary_nodes
        
        summaries = await memory_summary_generation(
            chunked_dialogs=dialog_data_list,
            llm_client=llm_client,
            embedder_client=embedder_client
        )
        print(f"[Ingestion] Generated {len(summaries)} memory summaries")
    except Exception as e:
        print(f"[Ingestion] Warning: Failed to generate memory summaries: {e}")
        summaries = []

    # Step H: Save to Neo4j
    try:
        success = await save_dialog_and_statements_to_neo4j(
            dialogue_nodes=dialogue_nodes,
            chunk_nodes=chunk_nodes,
            statement_nodes=statement_nodes,
            entity_nodes=entity_nodes,
            entity_edges=entity_entity_edges,
            statement_chunk_edges=statement_chunk_edges,
            statement_entity_edges=statement_entity_edges,
            connector=connector
        )
        
        # Save memory summaries separately
        if summaries:
            try:
                await add_memory_summary_nodes(summaries, connector)
                await add_memory_summary_statement_edges(summaries, connector)
                print(f"Successfully saved {len(summaries)} memory summary nodes to Neo4j")
            except Exception as e:
                print(f"Warning: Failed to save summary nodes: {e}")
        
        await connector.close()
        if success:
            print("Successfully saved extracted data to Neo4j!")
        else:
            print("Failed to save data to Neo4j")
        return success
    except Exception as e:
        print(f"Failed to save data to Neo4j: {e}")
        return False


async def handle_context_processing(args):
    """Handle context-based processing from command line arguments."""
    contexts = []

    if args.contexts:
        contexts.extend(args.contexts)

    if args.context_file:
        try:
            with open(args.context_file, 'r', encoding='utf-8') as f:
                contexts.extend(line.strip() for line in f if line.strip())
        except Exception as e:
            print(f"Error reading context file: {e}")
            return False

    if not contexts:
        print("No contexts provided for processing.")
        return False

    return await main_from_contexts(contexts, args.context_group_id)


async def main_from_contexts(contexts: List[str], group_id: str):
    """Run the pipeline from provided dialogue contexts instead of test data."""
    print("=== Running pipeline from provided contexts ===")

    success = await ingest_contexts_via_full_pipeline(
        contexts=contexts,
        group_id=group_id,
        chunker_strategy=SELECTED_CHUNKER_STRATEGY,
        embedding_name=SELECTED_EMBEDDING_ID,
        save_chunk_output=True
    )

    if success:
        print("Successfully processed and saved contexts to Neo4j!")
    else:
        print("Failed to process contexts.")

    return success
