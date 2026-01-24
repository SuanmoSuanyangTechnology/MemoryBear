import os
import asyncio
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import UUID
import re

from app.core.memory.llm_tools.openai_client import LLMClient
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
from app.core.memory.models.message_models import DialogData, ConversationContext, ConversationMessage
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load evaluation config
eval_config_path = Path(__file__).resolve().parent / "app" / "core" / "memory" / "evaluation" / ".env.evaluation"
if eval_config_path.exists():
    load_dotenv(eval_config_path, override=True)
    print(f"✅ 加载评估配置: {eval_config_path}")

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.utils.llm.llm_utils import get_llm_client

# 使用新的模块化架构
from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import ExtractionOrchestrator

# Import from database module
from app.repositories.neo4j.graph_saver import save_dialog_and_statements_to_neo4j

# Cypher queries for evaluation
# Note: Entity, chunk, and dialogue search queries have been moved to evaluation/dialogue_queries.py


async def ingest_contexts_via_full_pipeline(
    contexts: List[str],
    end_user_id: str,
    chunker_strategy: str | None = None,
    embedding_name: str | None = None,
    save_chunk_output: bool = False,
    save_chunk_output_path: str | None = None,
) -> bool:
    """
    使用新的 ExtractionOrchestrator 运行完整的提取流水线
    
    Run the full extraction pipeline on provided dialogue contexts and save to Neo4j.
    This function uses the new ExtractionOrchestrator architecture for better maintainability.
    
    Args:
        contexts: List of dialogue texts, each containing lines like "role: message".
        end_user_id: Group ID to assign to generated DialogData and graph nodes.
        chunker_strategy: Optional chunker strategy; defaults to SELECTED_CHUNKER_STRATEGY.
        embedding_name: Optional embedding model ID; defaults to SELECTED_EMBEDDING_ID.
        save_chunk_output: If True, write chunked DialogData list to a JSON file for debugging.
        save_chunk_output_path: Optional output path; defaults to src/chunker_test_output.txt.
    Returns:
        True if data saved successfully, False otherwise.
    """
    chunker_strategy = chunker_strategy or os.getenv("EVAL_CHUNKER_STRATEGY", "RecursiveChunker")
    embedding_name = embedding_name or os.getenv("EVAL_EMBEDDING_ID")

    # Step 1: Initialize LLM client
    llm_client = None
    try:
        # 使用评估配置中的 LLM ID
        llm_id = os.getenv("EVAL_LLM_ID")
        if not llm_id:
            print("[Ingestion] ❌ EVAL_LLM_ID not set in .env.evaluation")
            return False
            
        from app.db import get_db
        
        db = next(get_db())
        try:
            llm_client = get_llm_client(llm_id, db)
        finally:
            db.close()
    except Exception as e:
        print(f"[Ingestion] LLM client unavailable: {e}")
        return False

    # Step 2: Parse contexts and create DialogData with chunks
    print(f"[Ingestion] Parsing {len(contexts)} contexts...")
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
                m = re.match(r'^\s*([^:：]+)\s*[：:]\s*(.+)', line)
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
            end_user_id=end_user_id,
            user_id="default_user",
            apply_id="default_application",
        )
        # Generate chunks
        dialog.chunks = await chunker.process_dialogue(dialog)
        dialog_data_list.append(dialog)

    if not dialog_data_list:
        print("[Ingestion] No dialogs to process.")
        return False

    print(f"[Ingestion] Parsed {len(dialog_data_list)} dialogs with chunks")

    # Step 3: Optionally save chunking outputs for debugging
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
            print(f"[Ingestion] Saved chunking results to: {out_path}")
        except Exception as e:
            print(f"[Ingestion] Failed to save chunking results: {e}")

    # Step 4: Initialize embedder client
    from app.core.models.base import RedBearModelConfig
    from app.core.memory.utils.config.config_utils import get_embedder_config
    from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
    from app.db import get_db
    
    try:
        db = next(get_db())
        try:
            embedder_config_dict = get_embedder_config(embedding_name, db)
            embedder_config = RedBearModelConfig(**embedder_config_dict)
            embedder_client = OpenAIEmbedderClient(embedder_config)
        finally:
            db.close()
    except Exception as e:
        print(f"[Ingestion] Failed to initialize embedder client: {e}")
        return False
    
    # Step 5: Initialize Neo4j connector
    connector = Neo4jConnector()
    
    # Step 6: 构建 MemoryConfig（从环境变量直接构建，不依赖数据库）
    print("[Ingestion] 构建 MemoryConfig from environment variables...")
    from app.schemas.memory_config_schema import MemoryConfig
    
    try:
        # 从环境变量获取配置参数
        llm_id = os.getenv("EVAL_LLM_ID")
        embedding_id = os.getenv("EVAL_EMBEDDING_ID")
        chunker_strategy_env = os.getenv("EVAL_CHUNKER_STRATEGY", "RecursiveChunker")
        
        if not llm_id or not embedding_id:
            print("[Ingestion] ❌ EVAL_LLM_ID or EVAL_EMBEDDING_ID is not set in .env.evaluation")
            print("[Ingestion] Please set both EVAL_LLM_ID and EVAL_EMBEDDING_ID")
            await connector.close()
            return False
        
        # 从数据库获取模型信息（仅用于显示名称）
        from app.db import get_db
        db = next(get_db())
        try:
            from sqlalchemy import text
            # 获取 LLM 模型信息（从 model_configs 表）
            llm_result = db.execute(
                text("SELECT name FROM model_configs WHERE id = :id"),
                {"id": llm_id}
            ).fetchone()
            llm_model_name = llm_result[0] if llm_result else "Unknown LLM"
            
            # 获取 Embedding 模型信息（从 model_configs 表）
            emb_result = db.execute(
                text("SELECT name FROM model_configs WHERE id = :id"),
                {"id": embedding_id}
            ).fetchone()
            embedding_model_name = emb_result[0] if emb_result else "Unknown Embedding"
        except Exception as e:
            # 如果查询失败，使用默认名称
            print(f"[Ingestion] Warning: Failed to query model names from database: {e}")
            llm_model_name = f"LLM ({llm_id[:8]}...)"
            embedding_model_name = f"Embedding ({embedding_id[:8]}...)"
        finally:
            db.close()
        
        # 构建 MemoryConfig 对象（使用最小必需配置）
        from uuid import uuid4
        memory_config = MemoryConfig(
            config_id=0,  # 评估环境不需要真实的 config_id
            config_name="evaluation_config",
            workspace_id=uuid4(),  # 临时 workspace_id
            workspace_name="evaluation_workspace",
            tenant_id=uuid4(),  # 临时 tenant_id
            llm_model_id=UUID(llm_id),
            llm_model_name=llm_model_name,
            embedding_model_id=UUID(embedding_id),
            embedding_model_name=embedding_model_name,
            storage_type="neo4j",
            chunker_strategy=chunker_strategy_env,
            reflexion_enabled=False,
            reflexion_iteration_period=3,
            reflexion_range="partial",
            reflexion_baseline="TIME",
            loaded_at=datetime.now(),
            # 可选字段使用默认值
            rerank_model_id=None,
            rerank_model_name=None,
            llm_params={},
            embedding_params={},
            config_version="2.0",
        )
        
        print(f"[Ingestion] ✅ 构建 MemoryConfig 成功")
        print(f"[Ingestion]    LLM: {llm_model_name}")
        print(f"[Ingestion]    Embedding: {embedding_model_name}")
        print(f"[Ingestion]    Chunker: {chunker_strategy_env}")
        
    except Exception as e:
        print(f"[Ingestion] ❌ Failed to build MemoryConfig: {e}")
        print(f"[Ingestion] Please check:")
        print(f"[Ingestion]   1. EVAL_LLM_ID and EVAL_EMBEDDING_ID are set in .env.evaluation")
        print(f"[Ingestion]   2. Model IDs exist in the models table")
        print(f"[Ingestion]   3. Database connection is working")
        await connector.close()
        return False
    
    # Step 7: Initialize and run ExtractionOrchestrator
    print("[Ingestion] Running extraction pipeline with ExtractionOrchestrator...")
    from app.services.memory_config_service import MemoryConfigService
    config = MemoryConfigService.get_pipeline_config(memory_config)
    
    orchestrator = ExtractionOrchestrator(
        llm_client=llm_client,
        embedder_client=embedder_client,
        connector=connector,
        config=config,
        embedding_id=str(memory_config.embedding_model_id),  # 传递 embedding_id
    )
    
    try:
        # Run the complete extraction pipeline
        result = await orchestrator.run(dialog_data_list, is_pilot_run=False)
        
        # Handle different return formats:
        # - Pilot mode: 7 values (without dedup_details)
        # - Normal mode: 8 values (with dedup_details at the end)
        if len(result) == 8:
            # Normal mode: includes dedup_details
            (
                dialogue_nodes,
                chunk_nodes,
                statement_nodes,
                entity_nodes,
                statement_chunk_edges,
                statement_entity_edges,
                entity_entity_edges,
                _,  # dedup_details - not needed here
            ) = result
        elif len(result) == 7:
            # Pilot mode or older version: no dedup_details
            (
                dialogue_nodes,
                chunk_nodes,
                statement_nodes,
                entity_nodes,
                statement_chunk_edges,
                statement_entity_edges,
                entity_entity_edges,
            ) = result
        else:
            raise ValueError(f"Unexpected number of return values: {len(result)}")
        
        print(f"[Ingestion] Extraction completed: {len(statement_nodes)} statements, {len(entity_nodes)} entities")
        
    except ValueError as e:
        # If unpacking fails, provide helpful error message
        print(f"[Ingestion] Extraction pipeline result unpacking failed: {e}")
        print(f"[Ingestion] Result type: {type(result)}, length: {len(result) if hasattr(result, '__len__') else 'N/A'}")
        if hasattr(result, '__len__') and len(result) > 0:
            print(f"[Ingestion] First element type: {type(result[0])}")
        await connector.close()
        return False
    except Exception as e:
        print(f"[Ingestion] Extraction pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        await connector.close()
        return False

    # Step 7: Generate memory summaries
    print("[Ingestion] Generating memory summaries...")
    try:
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import (
            memory_summary_generation,
        )
        from app.repositories.neo4j.add_nodes import add_memory_summary_nodes
        from app.repositories.neo4j.add_edges import add_memory_summary_statement_edges
        
        summaries = await memory_summary_generation(
            chunked_dialogs=dialog_data_list,
            llm_client=llm_client,
            embedder_client=embedder_client
        )
        print(f"[Ingestion] Generated {len(summaries)} memory summaries")
    except Exception as e:
        print(f"[Ingestion] Warning: Failed to generate memory summaries: {e}")
        summaries = []

    # Step 8: Save to Neo4j
    print("[Ingestion] Saving to Neo4j...")
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
                print(f"[Ingestion] Saved {len(summaries)} memory summary nodes to Neo4j")
            except Exception as e:
                print(f"[Ingestion] Warning: Failed to save summary nodes: {e}")
        
        await connector.close()
        
        if success:
            print("[Ingestion] Successfully saved all data to Neo4j!")
        else:
            print("[Ingestion] Failed to save data to Neo4j")
        return success
        
    except Exception as e:
        print(f"[Ingestion] Failed to save data to Neo4j: {e}")
        await connector.close()
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

    return await main_from_contexts(contexts, args.context_end_user_id)


async def main_from_contexts(contexts: List[str], end_user_id: str):
    """Run the pipeline from provided dialogue contexts instead of test data."""
    print("=== Running pipeline from provided contexts ===")

    success = await ingest_contexts_via_full_pipeline(
        contexts=contexts,
        end_user_id=end_user_id,
        chunker_strategy=SELECTED_CHUNKER_STRATEGY,
        embedding_name=SELECTED_EMBEDDING_ID,
        save_chunk_output=True
    )

    if success:
        print("Successfully processed and saved contexts to Neo4j!")
    else:
        print("Failed to process contexts.")

    return success
