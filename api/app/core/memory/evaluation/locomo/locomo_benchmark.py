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
from dotenv import load_dotenv

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.models.base import RedBearModelConfig
from app.core.memory.utils.config.config_utils import get_embedder_config
from app.core.memory.evaluation.config import (
    DATASET_DIR,
    SELECTED_GROUP_ID,
    SELECTED_LLM_ID,
    SELECTED_EMBEDDING_ID
)
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
    select_and_format_information,
)
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.memory.utils.definitions import (
    PROJECT_ROOT,
    SELECTED_EMBEDDING_ID,
    SELECTED_end_user_id,
    SELECTED_LLM_ID,
)
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.core.models.base import RedBearModelConfig
from app.db import get_db_context
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services.memory_config_service import MemoryConfigService


async def run_locomo_benchmark(
    sample_size: int = 20,
    end_user_id: Optional[str] = None,
    search_type: str = "hybrid",
    search_limit: int = 12,
    context_char_budget: int = 8000,
    reset_group: bool = False,
    skip_ingest: bool = False,
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Load LoCoMo QA pairs from the first conversation.
    
    Args:
        sample_size: Number of QA pairs to evaluate (from first conversation)
        end_user_id: Database group ID for retrieval (uses default if None)
        search_type: "keyword", "embedding", or "hybrid"
        search_limit: Max documents to retrieve per query
        context_char_budget: Max characters for context
        reset_group: Whether to clear and re-ingest data (not implemented)
        skip_ingest: If True, skip data ingestion and use existing data in Neo4j
        output_dir: Directory to save results (uses default if None)
        
    Returns:
        Dictionary with evaluation results including metrics, timing, and samples
    """
    # Use default end_user_id if not provided
    end_user_id = end_user_id or SELECTED_end_user_id
    
    # Determine data path
    data_path = os.path.join(PROJECT_ROOT, "data", "locomo10.json")
    if not os.path.exists(data_path):
        # Fallback to current directory
        data_path = os.path.join(os.getcwd(), "data", "locomo10.json")
    
    print(f"\n{'='*60}")
    print("🚀 Starting LoCoMo Benchmark Evaluation")
    print(f"{'='*60}")
    print("📊 Configuration:")
    print(f"   Sample size: {sample_size}")
    print(f"   Group ID: {end_user_id}")
    print(f"   Search type: {search_type}")
    print(f"   Search limit: {search_limit}")
    print(f"   Context budget: {context_char_budget} chars")
    print(f"   Data path: {data_path}")
    print(f"{'='*60}\n")
    
    # Step 1: Load LoCoMo data
    print("📂 Loading LoCoMo dataset...")
    qa_items = load_locomo_data(data_path, sample_size, conversation_index=0)
    print(f"✅ Loaded {len(qa_items)} QA pairs from conversation 0\n")
    return qa_items


# ============================================================================
# Step 2: Data Ingestion
# ============================================================================

async def step_ingest_data(
    data_path: str,
    group_id: str,
    skip_ingest: bool,
    reset_group: bool
) -> bool:
    """
    Ingest conversations into Neo4j database if needed.
    
    Args:
        data_path: Path to locomo10.json file
        group_id: Database group ID
        skip_ingest: Whether to skip ingestion
        reset_group: Whether to reset the group before ingestion
        
    Returns:
        True if ingestion succeeded or was skipped, False otherwise
    """
    if skip_ingest:
        print("⏭️  Skipping data ingestion (using existing data in Neo4j)")
        print(f"   Group ID: {end_user_id}\n")
    else:
        print("💾 Checking database ingestion...")
        try:
            conversations = extract_conversations(data_path, max_dialogues=1)
            print(f"📝 Extracted {len(conversations)} conversations")
            
            # Always ingest for now (ingestion check not implemented)
            print(f"🔄 Ingesting conversations into group '{end_user_id}'...")
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
            print("⚠️  Continuing with evaluation (database may be empty)\n")
    
    # Step 3: Initialize clients
    print("🔧 Initializing clients...")
    
    connector = Neo4jConnector()
    llm_client = get_llm_client(SELECTED_LLM_ID)
    cfg_dict = get_embedder_config(SELECTED_EMBEDDING_ID)
    embedder = OpenAIEmbedderClient(
        model_config=RedBearModelConfig.model_validate(cfg_dict)
    )
    
    print("✅ Clients initialized\n")
    
    # Step 4: Process questions
    print(f"🔍 Processing {len(qa_items)} questions...")
    print(f"{'='*60}\n")
    
    # Tracking variables
    latencies_search: List[float] = []
    latencies_llm: List[float] = []
    context_counts: List[int] = []
    context_chars: List[int] = []
    context_tokens: List[int] = []
    
    # Metric lists
    f1_scores: List[float] = []
    bleu1_scores: List[float] = []
    jaccard_scores: List[float] = []
    locomo_f1_scores: List[float] = []
    
    # Per-category tracking
    category_counts: Dict[str, int] = {}
    category_f1: Dict[str, List[float]] = {}
    category_bleu1: Dict[str, List[float]] = {}
    category_jaccard: Dict[str, List[float]] = {}
    category_locomo_f1: Dict[str, List[float]] = {}
    
    # Detailed samples
    samples: List[Dict[str, Any]] = []
    
    # Fixed anchor date for temporal resolution
    anchor_date = datetime(2023, 5, 8)
    
    try:
        for idx, item in enumerate(qa_items, 1):
            question = item.get("question", "")
            ground_truth = item.get("answer", "")
            category = get_category_name(item)
            
            # Ensure ground truth is a string
            ground_truth_str = str(ground_truth) if ground_truth is not None else ""
            
            print(f"[{idx}/{len(qa_items)}] Category: {category}")
            print(f"❓ Question: {question}")
            print(f"✅ Ground Truth: {ground_truth_str}")
            
            # Step 4a: Retrieve relevant information
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
                t_search_end = time.time()
                search_latency = (t_search_end - t_search_start) * 1000
                latencies_search.append(search_latency)
                
                print(f"🔍 Retrieved {len(retrieved_info)} documents ({search_latency:.1f}ms)")
                
            except Exception as e:
                print(f"❌ Retrieval failed: {e}")
                retrieved_info = []
                search_latency = 0.0
                latencies_search.append(search_latency)
            
            # Step 4b: Select and format context
            context_text = select_and_format_information(
                retrieved_info=retrieved_info,
                question=question,
                max_chars=context_char_budget
            )
            
            # Resolve temporal references
            context_text = resolve_temporal_references(context_text, anchor_date)
            
            # Add reference date to context
            if context_text:
                context_text = f"Reference date: {anchor_date.date().isoformat()}\n\n{context_text}"
            else:
                context_text = "No relevant context found."
            
            # Track context statistics
            context_counts.append(len(retrieved_info))
            context_chars.append(len(context_text))
            context_tokens.append(len(context_text.split()))
            
            print(f"📝 Context: {len(context_text)} chars, {len(retrieved_info)} docs")
            
            # Step 4c: Generate answer with LLM
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
                t_llm_end = time.time()
                llm_latency = (t_llm_end - t_llm_start) * 1000
                latencies_llm.append(llm_latency)
                
                # Extract prediction from response
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
                latencies_llm.append(llm_latency)
            
            # Step 4d: Calculate metrics
            f1_val = f1_score(prediction, ground_truth_str)
            bleu1_val = bleu1(prediction, ground_truth_str)
            jaccard_val = jaccard(prediction, ground_truth_str)
            
            # LoCoMo-specific F1: use multi-answer for category 1 (Multi-Hop)
            if item.get("category") == 1:
                locomo_f1_val = locomo_multi_f1(prediction, ground_truth_str)
            else:
                locomo_f1_val = locomo_f1_score(prediction, ground_truth_str)
            
            # Accumulate metrics
            f1_scores.append(f1_val)
            bleu1_scores.append(bleu1_val)
            jaccard_scores.append(jaccard_val)
            locomo_f1_scores.append(locomo_f1_val)
            
            # Track by category
            category_counts[category] = category_counts.get(category, 0) + 1
            category_f1.setdefault(category, []).append(f1_val)
            category_bleu1.setdefault(category, []).append(bleu1_val)
            category_jaccard.setdefault(category, []).append(jaccard_val)
            category_locomo_f1.setdefault(category, []).append(locomo_f1_val)
            
            print(f"📊 Metrics - F1: {f1_val:.3f}, BLEU-1: {bleu1_val:.3f}, "
                  f"Jaccard: {jaccard_val:.3f}, LoCoMo F1: {locomo_f1_val:.3f}")
            print()
            
            # Save sample details
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
                "timing": {
                    "search_ms": search_latency,
                    "llm_ms": llm_latency
                }
            })
    
    finally:
        # Close connector
        await connector.close()
    
    # Step 5: Aggregate results
    print(f"\n{'='*60}")
    print("📊 Aggregating Results")
    print(f"{'='*60}\n")
    
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
        "f1": sum(f1_scores) / max(len(f1_scores), 1) if f1_scores else 0.0,
        "bleu1": sum(bleu1_scores) / max(len(bleu1_scores), 1) if bleu1_scores else 0.0,
        "jaccard": sum(jaccard_scores) / max(len(jaccard_scores), 1) if jaccard_scores else 0.0,
        "locomo_f1": sum(locomo_f1_scores) / max(len(locomo_f1_scores), 1) if locomo_f1_scores else 0.0
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
        "avg_retrieved_docs": sum(context_counts) / max(len(context_counts), 1) if context_counts else 0.0,
        "avg_context_chars": sum(context_chars) / max(len(context_chars), 1) if context_chars else 0.0,
        "avg_context_tokens": sum(context_tokens) / max(len(context_tokens), 1) if context_tokens else 0.0
    }
    
    # Build result dictionary
    result = {
        "dataset": "locomo",
        "sample_size": len(qa_items),
        "timestamp": datetime.now().isoformat(),
        "params": {
            "end_user_id": end_user_id,
            "search_type": search_type,
            "search_limit": search_limit,
            "context_char_budget": context_char_budget,
            "llm_id": SELECTED_LLM_ID,
            "embedding_id": SELECTED_EMBEDDING_ID
        },
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
        output_dir = os.path.join(
            os.path.dirname(__file__),
            "results"
        )
    
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"locomo_{timestamp_str}.json")
    
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"✅ Results saved to: {output_path}\n")
        return output_path
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
    group_id: Optional[str] = None,
    search_type: str = "hybrid",
    search_limit: int = 12,
    context_char_budget: int = 8000,
    reset_group: bool = False,
    skip_ingest: bool = False,
    output_dir: Optional[str] = None
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
        group_id: Database group ID for retrieval (uses default if None)
        search_type: "keyword", "embedding", or "hybrid"
        search_limit: Max documents to retrieve per query
        context_char_budget: Max characters for context
        reset_group: Whether to clear and re-ingest data
        skip_ingest: If True, skip data ingestion and use existing data in Neo4j
        output_dir: Directory to save results (uses default if None)
        
    Returns:
        Dictionary with evaluation results including metrics, timing, and samples
    """
    # Use default group_id if not provided
    group_id = group_id or SELECTED_GROUP_ID
    
    # Determine data path
    data_path = os.path.join(DATASET_DIR, "locomo10.json")
    if not os.path.exists(data_path):
        raise FileNotFoundError(
            f"数据集文件不存在: {data_path}\n"
            f"请将 locomo10.json 放置在: {DATASET_DIR}"
        )
    
    # Print configuration
    print(f"\n{'='*60}")
    print("🚀 Starting LoCoMo Benchmark Evaluation")
    print(f"{'='*60}")
    print("📊 Configuration:")
    print(f"   Sample size: {sample_size}")
    print(f"   Group ID: {group_id}")
    print(f"   Search type: {search_type}")
    print(f"   Search limit: {search_limit}")
    print(f"   Context budget: {context_char_budget} chars")
    print(f"   Data path: {data_path}")
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
    await step_ingest_data(data_path, group_id, skip_ingest, reset_group)
    
    # Step 3: Initialize clients （初始化客户端）
    connector, llm_client, embedder = step_initialize_clients()
    
    # Step 4: Process all questions （处理所有问题）
    try:
        samples = await step_process_all_questions(
            qa_items=qa_items,
            group_id=group_id,
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
            "group_id": group_id,
            "search_type": search_type,
            "search_limit": search_limit,
            "context_char_budget": context_char_budget,
            "llm_id": SELECTED_LLM_ID,
            "embedding_id": SELECTED_EMBEDDING_ID
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
    """
    parser = argparse.ArgumentParser(
        description="Run LoCoMo benchmark evaluation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        "--sample_size",
        type=int,
        default=20,
        help="Number of QA pairs to evaluate"
    )
    parser.add_argument(
        "--end_user_id",
        type=str,
        default=None,
        help="Database group ID for retrieval (uses default if not specified)"
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
        default=12,
        help="Maximum number of documents to retrieve per query"
    )
    parser.add_argument(
        "--context_char_budget",
        type=int,
        default=8000,
        help="Maximum characters for context"
    )
    parser.add_argument(
        "--reset_group",
        action="store_true",
        help="Clear and re-ingest data (not implemented)"
    )
    parser.add_argument(
        "--skip_ingest",
        action="store_true",
        help="Skip data ingestion and use existing data in Neo4j"
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Directory to save results (uses default if not specified)"
    )
    
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    
    # Run benchmark
    result = asyncio.run(run_locomo_benchmark(
        sample_size=args.sample_size,
        end_user_id=args.end_user_id,
        search_type=args.search_type,
        search_limit=args.search_limit,
        context_char_budget=args.context_char_budget,
        reset_group=args.reset_group,
        skip_ingest=args.skip_ingest,
        output_dir=args.output_dir
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
