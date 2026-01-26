"""
LoCoMo Benchmark Script

This module provides the main entry point for running LoCoMo benchmark evaluations.
It orchestrates data loading, ingestion, retrieval, LLM inference, and metric calculation
in a clean, maintainable way.

Usage:
    python locomo_benchmark.py --sample_size 20 --search_type hybrid
"""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from dotenv import load_dotenv

# Load evaluation config
eval_config_path = Path(__file__).resolve().parent.parent / ".env.evaluation"
if eval_config_path.exists():
    load_dotenv(eval_config_path, override=True)
    print(f"✅ 加载评估配置: {eval_config_path}")

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.models.base import RedBearModelConfig
from app.core.memory.utils.config.config_utils import get_embedder_config
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.memory.evaluation.common.metrics import (
    f1_score,
    bleu1,
    jaccard,
    latency_stats,
    avg_context_tokens
)
from app.core.memory.evaluation.locomo.locomo_metrics import (
    locomo_f1_score,
    locomo_multi_f1,
    get_category_name
)
from app.core.memory.evaluation.locomo.locomo_utils import (
    load_locomo_data,
    extract_conversations,
    resolve_temporal_references,
    select_and_format_information,
    retrieve_relevant_information,
)
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.services.memory_config_service import MemoryConfigService

# Get configuration from environment variables
PROJECT_ROOT = str(Path(__file__).resolve().parents[5])  # api directory
SELECTED_EMBEDDING_ID = os.getenv("EVAL_EMBEDDING_ID", "e2a6392d-ca63-4d59-a523-647420b59cb2")
SELECTED_end_user_id = os.getenv("LOCOMO_END_USER_ID") or os.getenv("EVAL_END_USER_ID", "locomo_benchmark")
SELECTED_LLM_ID = os.getenv("EVAL_LLM_ID", "2c9b0782-7a85-4740-ba84-4baf77f256c4")


# ============================================================================
# Step 1: Data Loading
# ============================================================================

def step_load_data(data_path: str, sample_size: int) -> List[Dict[str, Any]]:
    """
    Load QA pairs from LoCoMo dataset.
    
    Args:
        data_path: Path to locomo10.json file
        sample_size: Number of QA pairs to load (0 for all)
        
    Returns:
        List of QA items from the first conversation
    """
    print("📂 Loading LoCoMo data...")
    
    # Load the dataset
    qa_items = load_locomo_data(data_path, sample_size)
    
    print(f"✅ Loaded {len(qa_items)} QA pairs from first conversation\n")
    return qa_items


# ============================================================================
# Step 2: Data Ingestion
# ============================================================================

async def ingest_conversations_if_needed(
    conversations: List[str],
    end_user_id: str,
    reset: bool = False
) -> bool:
    """
    Ingest conversations into Neo4j database.
    
    Args:
        conversations: List of conversation strings (already formatted)
        end_user_id: Database end_user ID
        reset: Whether to reset the group before ingestion
        
    Returns:
        True if successful, False otherwise
    """
    try:
        from app.core.memory.evaluation.extraction_utils import ingest_contexts_via_full_pipeline
        
        # Conversations are already formatted as strings, use them directly
        await ingest_contexts_via_full_pipeline(conversations, end_user_id)
        return True
        
    except Exception as e:
        print(f"⚠️  Ingestion error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def step_ingest_data(
    data_path: str,
    end_user_id: str,
    skip_ingest: bool,
    reset_group: bool,
    max_messages: Optional[int] = None
) -> bool:
    """
    Ingest conversations into Neo4j database if needed.
    
    Args:
        data_path: Path to locomo10.json file
        end_user_id: Database end_user ID
        skip_ingest: Whether to skip ingestion
        reset_group: Whether to reset the group before ingestion
        max_messages: Maximum messages per dialogue to ingest (for testing)
        
    Returns:
        True if ingestion succeeded or was skipped, False otherwise
    """
    if skip_ingest:
        print("⏭️  Skipping data ingestion (using existing data in Neo4j)")
        print(f"   End User ID: {end_user_id}\n")
    else:
        print("💾 Checking database ingestion...")
        try:
            # Extract conversations with optional message limit
            conversations = extract_conversations(
                data_path, 
                max_dialogues=1,
                max_messages_per_dialogue=max_messages
            )
            print(f"📝 Extracted {len(conversations)} conversations")
            
            # Always ingest for now (ingestion check not implemented)
            print(f"🔄 Ingesting conversations into end_user '{end_user_id}'...")
            success = await ingest_conversations_if_needed(
                conversations=conversations,
                end_user_id=end_user_id,
                reset=reset_group
            )
            
            if success:
                print("✅ Ingestion completed successfully\n")
            else:
                print("⚠️  Ingestion may have failed, continuing anyway\n")
        
        except Exception as e:
            print(f"❌ Ingestion failed: {e}")
            import traceback
            traceback.print_exc()
            print("⚠️  Continuing with evaluation (database may be empty)\n")
    
    return True


# ============================================================================
# Step 3: Initialize Clients
# ============================================================================

def step_initialize_clients(llm_id: str, embedding_id: str):
    """
    Initialize Neo4j connector, LLM client, and embedder.
    
    Args:
        llm_id: LLM model ID
        embedding_id: Embedding model ID
        
    Returns:
        Tuple of (connector, llm_client, embedder)
    """
    print("🔧 Initializing clients...")
    
    connector = Neo4jConnector()
    
    # Get database session
    from app.db import get_db
    db = next(get_db())
    try:
        llm_client = get_llm_client(llm_id, db)
        cfg_dict = get_embedder_config(embedding_id, db)
        embedder = OpenAIEmbedderClient(
            model_config=RedBearModelConfig.model_validate(cfg_dict)
        )
    finally:
        db.close()
    
    print("✅ Clients initialized\n")
    return connector, llm_client, embedder


# ============================================================================
# Step 4: Process Questions
# ============================================================================

async def step_process_all_questions(
    qa_items: List[Dict[str, Any]],
    end_user_id: str,
    search_type: str,
    search_limit: int,
    context_char_budget: int,
    connector: Neo4jConnector,
    embedder: OpenAIEmbedderClient,
    llm_client: Any
) -> List[Dict[str, Any]]:
    """Process all QA items: retrieve, generate, and calculate metrics."""
    print(f"🔍 Processing {len(qa_items)} questions...")
    print(f"{'='*60}\n")
    
    samples: List[Dict[str, Any]] = []
    anchor_date = datetime(2023, 5, 8)
    
    for idx, item in enumerate(qa_items, 1):
        question = item.get("question", "")
        ground_truth = item.get("answer", "")
        category = get_category_name(item)
        ground_truth_str = str(ground_truth) if ground_truth is not None else ""
        
        print(f"[{idx}/{len(qa_items)}] Category: {category}")
        print(f"❓ Question: {question}")
        print(f"✅ Ground Truth: {ground_truth_str}")
        
        # Retrieve
        t_search_start = time.time()
        try:
            retrieved_info = await retrieve_relevant_information(
                question=question,
                end_user_id=end_user_id,
                search_type=search_type,
                search_limit=search_limit,
                connector=connector,
                embedder=embedder
            )
            search_latency = (time.time() - t_search_start) * 1000
            print(f"🔍 Retrieved {len(retrieved_info)} documents ({search_latency:.1f}ms)")
        except Exception as e:
            print(f"❌ Retrieval failed: {e}")
            retrieved_info = []
            search_latency = 0.0
        
        # Format context
        context_text = select_and_format_information(
            retrieved_info=retrieved_info,
            question=question,
            max_chars=context_char_budget
        )
        context_text = resolve_temporal_references(context_text, anchor_date)
        if context_text:
            context_text = f"Reference date: {anchor_date.date().isoformat()}\n\n{context_text}"
        else:
            context_text = "No relevant context found."
        
        print(f"📝 Context: {len(context_text)} chars, {len(retrieved_info)} docs")
        
        # Generate answer
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a precise QA assistant. Answer following these rules:\n"
                    "1) Extract the EXACT information mentioned in the context\n"
                    "2) For time questions: calculate actual dates from relative times\n"
                    "3) Return ONLY the answer text in simplest form\n"
                    "4) For dates, use format 'DD Month YYYY' (e.g., '7 May 2023')\n"
                    "5) If no clear answer found, respond with 'Unknown'"
                )
            },
            {
                "role": "user",
                "content": f"Question: {question}\n\nContext:\n{context_text}"
            }
        ]
        
        t_llm_start = time.time()
        try:
            response = await llm_client.chat(messages=messages)
            llm_latency = (time.time() - t_llm_start) * 1000
            if hasattr(response, 'content'):
                prediction = response.content.strip()
            elif isinstance(response, dict):
                prediction = response["choices"][0]["message"]["content"].strip()
            else:
                prediction = "Unknown"
            print(f"🤖 Prediction: {prediction} ({llm_latency:.1f}ms)")
        except Exception as e:
            print(f"❌ LLM failed: {e}")
            prediction = "Unknown"
            llm_latency = 0.0
        
        # Calculate metrics
        f1_val = f1_score(prediction, ground_truth_str)
        bleu1_val = bleu1(prediction, ground_truth_str)
        jaccard_val = jaccard(prediction, ground_truth_str)
        if item.get("category") == 1:
            locomo_f1_val = locomo_multi_f1(prediction, ground_truth_str)
        else:
            locomo_f1_val = locomo_f1_score(prediction, ground_truth_str)
        
        print(f"📊 Metrics - F1: {f1_val:.3f}, BLEU-1: {bleu1_val:.3f}, "
              f"Jaccard: {jaccard_val:.3f}, LoCoMo F1: {locomo_f1_val:.3f}")
        print()
        
        samples.append({
            "question": question,
            "ground_truth": ground_truth_str,
            "prediction": prediction,
            "category": category,
            "metrics": {
                "f1": f1_val,
                "bleu1": bleu1_val,
                "jaccard": jaccard_val,
                "locomo_f1": locomo_f1_val
            },
            "retrieval": {
                "num_docs": len(retrieved_info),
                "context_length": len(context_text)
            },
            "context_tokens": len(context_text.split()),
            "timing": {
                "search_ms": search_latency,
                "llm_ms": llm_latency
            }
        })
    
    return samples


# ============================================================================
# Step 5: Aggregate Results
# ============================================================================

def step_aggregate_results(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics from all samples."""
    print(f"\n{'='*60}")
    print("📊 Aggregating Results")
    print(f"{'='*60}\n")
    
    if not samples:
        return {
            "overall_metrics": {},
            "by_category": {},
            "latency": {},
            "context_stats": {}
        }
    
    # Extract metrics
    f1_scores = [s["metrics"]["f1"] for s in samples]
    bleu1_scores = [s["metrics"]["bleu1"] for s in samples]
    jaccard_scores = [s["metrics"]["jaccard"] for s in samples]
    locomo_f1_scores = [s["metrics"]["locomo_f1"] for s in samples]
    
    # Extract timing
    latencies_search = [s["timing"]["search_ms"] for s in samples]
    latencies_llm = [s["timing"]["llm_ms"] for s in samples]
    
    # Extract context stats
    context_counts = [s["retrieval"]["num_docs"] for s in samples]
    context_chars = [s["retrieval"]["context_length"] for s in samples]
    context_tokens = [s["context_tokens"] for s in samples]
    
    # Overall metrics
    overall_metrics = {
        "f1": sum(f1_scores) / len(f1_scores) if f1_scores else 0.0,
        "bleu1": sum(bleu1_scores) / len(bleu1_scores) if bleu1_scores else 0.0,
        "jaccard": sum(jaccard_scores) / len(jaccard_scores) if jaccard_scores else 0.0,
        "locomo_f1": sum(locomo_f1_scores) / len(locomo_f1_scores) if locomo_f1_scores else 0.0
    }
    
    # Per-category metrics
    category_data: Dict[str, Dict[str, List[float]]] = {}
    for sample in samples:
        cat = sample["category"]
        if cat not in category_data:
            category_data[cat] = {
                "f1": [],
                "bleu1": [],
                "jaccard": [],
                "locomo_f1": []
            }
        category_data[cat]["f1"].append(sample["metrics"]["f1"])
        category_data[cat]["bleu1"].append(sample["metrics"]["bleu1"])
        category_data[cat]["jaccard"].append(sample["metrics"]["jaccard"])
        category_data[cat]["locomo_f1"].append(sample["metrics"]["locomo_f1"])
    
    by_category: Dict[str, Dict[str, Any]] = {}
    for cat, metrics_lists in category_data.items():
        by_category[cat] = {
            "count": len(metrics_lists["f1"]),
            "f1": sum(metrics_lists["f1"]) / len(metrics_lists["f1"]),
            "bleu1": sum(metrics_lists["bleu1"]) / len(metrics_lists["bleu1"]),
            "jaccard": sum(metrics_lists["jaccard"]) / len(metrics_lists["jaccard"]),
            "locomo_f1": sum(metrics_lists["locomo_f1"]) / len(metrics_lists["locomo_f1"])
        }
    
    # Latency statistics
    latency = {
        "search": latency_stats(latencies_search),
        "llm": latency_stats(latencies_llm)
    }
    
    # Context statistics
    context_stats = {
        "avg_retrieved_docs": sum(context_counts) / len(context_counts) if context_counts else 0.0,
        "avg_context_chars": sum(context_chars) / len(context_chars) if context_chars else 0.0,
        "avg_context_tokens": sum(context_tokens) / len(context_tokens) if context_tokens else 0.0
    }
    
    return {
        "overall_metrics": overall_metrics,
        "by_category": by_category,
        "latency": latency,
        "context_stats": context_stats
    }


# ============================================================================
# Step 6: Result Saving
# ============================================================================

def step_save_results(
    result: Dict[str, Any],
    output_dir: Optional[str]
) -> str:
    """
    Save evaluation results to JSON file.
    
    Args:
        result: Complete result dictionary
        output_dir: Directory to save results (uses default if None)
        
    Returns:
        Path to saved file
    """
    if output_dir is None:
        # Use absolute path to ensure results are saved in the correct location
        script_dir = Path(__file__).resolve().parent
        output_dir = script_dir / "results"
    else:
        # Convert to Path object
        output_dir = Path(output_dir)
        # If relative path, make it relative to script directory
        if not output_dir.is_absolute():
            script_dir = Path(__file__).resolve().parent
            output_dir = script_dir / output_dir
    
    # Create directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"locomo_{timestamp_str}.json"
    
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"✅ Results saved to: {output_path}\n")
        return str(output_path)
    except Exception as e:
        print(f"❌ Failed to save results: {e}")
        print("📊 Printing results to console instead:\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return ""


# ============================================================================
# Main Orchestration Function
# ============================================================================


async def run_locomo_benchmark(
    sample_size: int = 20,
    end_user_id: Optional[str] = None,
    search_type: str = "hybrid",
    search_limit: int = 12,
    context_char_budget: int = 8000,
    reset_group: bool = False,
    skip_ingest: bool = False,
    output_dir: Optional[str] = None,
    max_ingest_messages: Optional[int] = None
) -> Dict[str, Any]:
    """
    Run LoCoMo benchmark evaluation.
    
    This function orchestrates the complete evaluation pipeline by calling
    well-defined step functions:
    1. Load LoCoMo dataset (only QA pairs from first conversation)
    2. Ingest conversations into database (unless skip_ingest=True)
    3. Initialize clients (Neo4j, LLM, Embedder)
    4. Process all questions (retrieve, generate, calculate metrics)
    5. Aggregate results
    6. Save results to file
    
    Note: By default, only the first conversation is ingested into the database,
    and only QA pairs from that conversation are evaluated. This ensures that
    all questions have corresponding memory in the database for retrieval.
    
    Args:
        sample_size: Number of QA pairs to evaluate (from first conversation)
        end_user_id: Database end_user ID for retrieval (uses default if None)
        search_type: "keyword", "embedding", or "hybrid"
        search_limit: Max documents to retrieve per query
        context_char_budget: Max characters for context
        reset_group: Whether to clear and re-ingest data
        skip_ingest: If True, skip data ingestion and use existing data in Neo4j
        output_dir: Directory to save results (uses default if None)
        max_ingest_messages: Max messages per dialogue to ingest (for testing, None = all)
        
    Returns:
        Dictionary with evaluation results including metrics, timing, and samples
    """
    # Use default end_user_id if not provided
    # 优先级：命令行参数 > LOCOMO_END_USER_ID > EVAL_END_USER_ID > 默认值
    if end_user_id is None:
        end_user_id = os.getenv("LOCOMO_END_USER_ID") or os.getenv("EVAL_END_USER_ID", "locomo_benchmark")
    
    # Get model IDs from config
    llm_id = os.getenv("EVAL_LLM_ID", "6dc52e1b-9cec-4194-af66-a74c6307fc3f")
    embedding_id = os.getenv("EVAL_EMBEDDING_ID", "e2a6392d-ca63-4d59-a523-647420b59cb2")
    
    # Determine data path
    dataset_dir = Path(__file__).resolve().parent.parent / "dataset"
    data_path = dataset_dir / "locomo10.json"
    if not os.path.exists(data_path):
        raise FileNotFoundError(
            f"数据集文件不存在: {data_path}\n"
            f"请将 locomo10.json 放置在: {dataset_dir}"
        )
    
    # Print configuration
    print(f"\n{'='*60}")
    print("🚀 Starting LoCoMo Benchmark Evaluation")
    print(f"{'='*60}")
    print("📊 Configuration:")
    print(f"   Sample size: {sample_size}")
    print(f"   End User ID: {end_user_id}")
    print(f"   Search type: {search_type}")
    print(f"   Search limit: {search_limit}")
    print(f"   Context budget: {context_char_budget} chars")
    print(f"   Data path: {data_path}")
    if max_ingest_messages:
        print(f"   Max ingest messages: {max_ingest_messages} (testing mode)")
    print(f"{'='*60}\n")
    
    # Step 1: Load LoCoMo data （加载数据）
    try:
        qa_items = step_load_data(data_path, sample_size)
    except Exception as e:
        print(f"❌ Failed to load data: {e}")
        return {
            "error": f"Data loading failed: {e}",
            "timestamp": datetime.now().isoformat()
        }
    
    # Step 2: Ingest data if needed（数据摄入）
    await step_ingest_data(data_path, end_user_id, skip_ingest, reset_group, max_ingest_messages)
    
    # Step 3: Initialize clients （初始化客户端）
    connector, llm_client, embedder = step_initialize_clients(llm_id, embedding_id)
    
    # Step 4: Process all questions （处理所有问题）
    try:
        samples = await step_process_all_questions(
            qa_items=qa_items,
            end_user_id=end_user_id,
            search_type=search_type,
            search_limit=search_limit,
            context_char_budget=context_char_budget,
            connector=connector,
            embedder=embedder,
            llm_client=llm_client
        )
    finally:
        await connector.close()
    
    # Step 5: Aggregate results （聚合答案）
    aggregated = step_aggregate_results(samples)
    
    # Build final result dictionary 
    result = {
        "dataset": "locomo",
        "sample_size": len(qa_items),
        "timestamp": datetime.now().isoformat(),
        "params": {
            "end_user_id": end_user_id,
            "search_type": search_type,
            "search_limit": search_limit,
            "context_char_budget": context_char_budget,
            "llm_id": llm_id,
            "embedding_id": embedding_id
        },
        "overall_metrics": aggregated["overall_metrics"],
        "by_category": aggregated["by_category"],
        "latency": aggregated["latency"],
        "context_stats": aggregated["context_stats"],
        "samples": samples
    }
    
    # Step 6: Save results （保存结果）
    step_save_results(result, output_dir)
    
    return result


def main():
    """
    Parse command-line arguments and run benchmark.
    
    This function provides a CLI interface for running LoCoMo benchmarks
    with configurable parameters.
    
    Configuration priority: Command-line args > Environment variables > Code defaults
    """
    # Load environment variables first
    load_dotenv()
    
    # Get defaults from environment variables
    env_sample_size = os.getenv("LOCOMO_SAMPLE_SIZE")
    env_search_limit = os.getenv("LOCOMO_SEARCH_LIMIT")
    env_context_budget = os.getenv("LOCOMO_CONTEXT_CHAR_BUDGET")
    env_output_dir = os.getenv("LOCOMO_OUTPUT_DIR")
    env_skip_ingest = os.getenv("LOCOMO_SKIP_INGEST", "false").lower() in ("true", "1", "yes")
    
    # Convert to appropriate types with fallback to code defaults
    default_sample_size = int(env_sample_size) if env_sample_size else 20
    default_search_limit = int(env_search_limit) if env_search_limit else 12
    default_context_budget = int(env_context_budget) if env_context_budget else 8000
    default_output_dir = env_output_dir if env_output_dir else None
    
    parser = argparse.ArgumentParser(
        description="Run LoCoMo benchmark evaluation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        "--sample_size",
        type=int,
        default=default_sample_size,
        help=f"Number of QA pairs to evaluate (env: LOCOMO_SAMPLE_SIZE={env_sample_size or 'not set'}, 0 for all)"
    )
    parser.add_argument(
        "--end_user_id",
        type=str,
        default=None,
        help="Database end user ID for retrieval (uses LOCOMO_END_USER_ID or EVAL_END_USER_ID if not specified)"
    )
    parser.add_argument(
        "--search_type",
        type=str,
        default="hybrid",
        choices=["keyword", "embedding", "hybrid"],
        help="Search strategy to use"
    )
    parser.add_argument(
        "--search_limit",
        type=int,
        default=default_search_limit,
        help=f"Maximum number of documents to retrieve per query (env: LOCOMO_SEARCH_LIMIT={env_search_limit or 'not set'})"
    )
    parser.add_argument(
        "--context_char_budget",
        type=int,
        default=default_context_budget,
        help=f"Maximum characters for context (env: LOCOMO_CONTEXT_CHAR_BUDGET={env_context_budget or 'not set'})"
    )
    parser.add_argument(
        "--reset_group",
        action="store_true",
        help="Clear and re-ingest data (not implemented)"
    )
    parser.add_argument(
        "--skip_ingest",
        action="store_true",
        default=env_skip_ingest,
        help=f"Skip data ingestion and use existing data in Neo4j (env: LOCOMO_SKIP_INGEST={os.getenv('LOCOMO_SKIP_INGEST', 'false')})"
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=default_output_dir,
        help=f"Directory to save results (env: LOCOMO_OUTPUT_DIR={env_output_dir or 'not set'})"
    )
    parser.add_argument(
        "--max_ingest_messages",
        type=int,
        default=None,
        help="Maximum messages per dialogue to ingest (for testing, default: all messages)"
    )
    
    args = parser.parse_args()
    
    # Run benchmark
    result = asyncio.run(run_locomo_benchmark(
        sample_size=args.sample_size,
        end_user_id=args.end_user_id,
        search_type=args.search_type,
        search_limit=args.search_limit,
        context_char_budget=args.context_char_budget,
        reset_group=args.reset_group,
        skip_ingest=args.skip_ingest,
        output_dir=args.output_dir,
        max_ingest_messages=args.max_ingest_messages
    ))
    
    # Print summary
    print(f"\n{'='*60}")
    
    # Check if there was an error
    if 'error' in result:
        print("❌ Benchmark Failed!")
        print(f"{'='*60}")
        print(f"Error: {result['error']}")
        return
    
    print("🎉 Benchmark Complete!")
    print(f"{'='*60}")
    print("📊 Final Results:")
    print(f"   Sample size: {result.get('sample_size', 0)}")
    print(f"   F1: {result['overall_metrics']['f1']:.3f}")
    print(f"   BLEU-1: {result['overall_metrics']['bleu1']:.3f}")
    print(f"   Jaccard: {result['overall_metrics']['jaccard']:.3f}")
    print(f"   LoCoMo F1: {result['overall_metrics']['locomo_f1']:.3f}")
    
    if result.get('context_stats'):
        print("\n📈 Context Statistics:")
        print(f"   Avg retrieved docs: {result['context_stats']['avg_retrieved_docs']:.1f}")
        print(f"   Avg context chars: {result['context_stats']['avg_context_chars']:.0f}")
        print(f"   Avg context tokens: {result['context_stats']['avg_context_tokens']:.0f}")
    
    if result.get('latency'):
        print("\n⏱️  Latency Statistics:")
        print(f"   Search - Mean: {result['latency']['search']['mean']:.1f}ms, "
              f"P50: {result['latency']['search']['p50']:.1f}ms, "
              f"P95: {result['latency']['search']['p95']:.1f}ms")
        print(f"   LLM - Mean: {result['latency']['llm']['mean']:.1f}ms, "
              f"P50: {result['latency']['llm']['p50']:.1f}ms, "
              f"P95: {result['latency']['llm']['p95']:.1f}ms")
    
    if result.get('by_category'):
        print("\n📂 Results by Category:")
        for cat, metrics in result['by_category'].items():
            print(f"   {cat}:")
            print(f"     Count: {metrics['count']}")
            print(f"     F1: {metrics['f1']:.3f}")
            print(f"     LoCoMo F1: {metrics['locomo_f1']:.3f}")
            print(f"     Jaccard: {metrics['jaccard']:.3f}")
    
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
