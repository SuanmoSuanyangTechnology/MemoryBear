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
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        pass

from app.core.memory.evaluation.common.metrics import (
    avg_context_tokens,
    bleu1,
    f1_score,
    jaccard,
    latency_stats,
)
from app.core.memory.evaluation.locomo.locomo_metrics import (
    get_category_name,
    locomo_f1_score,
    locomo_multi_f1,
)
from app.core.memory.evaluation.locomo.locomo_utils import (
    extract_conversations,
    ingest_conversations_if_needed,
    load_locomo_data,
    resolve_temporal_references,
    retrieve_relevant_information,
    select_and_format_information,
)
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.memory.utils.definitions import (
    PROJECT_ROOT,
    SELECTED_EMBEDDING_ID,
    SELECTED_GROUP_ID,
    SELECTED_LLM_ID,
)
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.core.models.base import RedBearModelConfig
from app.db import get_db_context
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services.memory_config_service import MemoryConfigService


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
    
    This function orchestrates the complete evaluation pipeline:
    1. Load LoCoMo dataset (only QA pairs from first conversation)
    2. Check/ingest conversations into database (only first conversation, unless skip_ingest=True)
    3. For each question:
       - Retrieve relevant information
       - Generate answer using LLM
       - Calculate metrics
    4. Aggregate results and save to file
    
    Note: By default, only the first conversation is ingested into the database,
    and only QA pairs from that conversation are evaluated. This ensures that
    all questions have corresponding memory in the database for retrieval.
    
    Args:
        sample_size: Number of QA pairs to evaluate (from first conversation)
        group_id: Database group ID for retrieval (uses default if None)
        search_type: "keyword", "embedding", or "hybrid"
        search_limit: Max documents to retrieve per query
        context_char_budget: Max characters for context
        reset_group: Whether to clear and re-ingest data (not implemented)
        skip_ingest: If True, skip data ingestion and use existing data in Neo4j
        output_dir: Directory to save results (uses default if None)
        
    Returns:
        Dictionary with evaluation results including metrics, timing, and samples
    """
    # Use default group_id if not provided
    group_id = group_id or SELECTED_GROUP_ID
    
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
    print(f"   Group ID: {group_id}")
    print(f"   Search type: {search_type}")
    print(f"   Search limit: {search_limit}")
    print(f"   Context budget: {context_char_budget} chars")
    print(f"   Data path: {data_path}")
    print(f"{'='*60}\n")
    
    # Step 1: Load LoCoMo data
    print("📂 Loading LoCoMo dataset...")
    try:
        # Only load QA pairs from the first conversation (index 0)
        # since we only ingest the first conversation into the database
        qa_items = load_locomo_data(data_path, sample_size, conversation_index=0)
        print(f"✅ Loaded {len(qa_items)} QA pairs from conversation 0\n")
    except Exception as e:
        print(f"❌ Failed to load data: {e}")
        return {
            "error": f"Data loading failed: {e}",
            "timestamp": datetime.now().isoformat()
        }
    
    # Step 2: Extract conversations and ingest if needed
    if skip_ingest:
        print("⏭️  Skipping data ingestion (using existing data in Neo4j)")
        print(f"   Group ID: {group_id}\n")
    else:
        print("💾 Checking database ingestion...")
        try:
            conversations = extract_conversations(data_path, max_dialogues=1)
            print(f"📝 Extracted {len(conversations)} conversations")
            
            # Always ingest for now (ingestion check not implemented)
            print(f"🔄 Ingesting conversations into group '{group_id}'...")
            success = await ingest_conversations_if_needed(
                conversations=conversations,
                group_id=group_id,
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
    
    # Initialize LLM client with database context
    with get_db_context() as db:
        factory = MemoryClientFactory(db)
        llm_client = factory.get_llm_client(SELECTED_LLM_ID)
    
    # Initialize embedder
    with get_db_context() as db:
        config_service = MemoryConfigService(db)
        cfg_dict = config_service.get_embedder_config(SELECTED_EMBEDDING_ID)
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
                    group_id=group_id,
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
    
    # Overall metrics
    overall_metrics = {
        "f1": sum(f1_scores) / max(len(f1_scores), 1) if f1_scores else 0.0,
        "bleu1": sum(bleu1_scores) / max(len(bleu1_scores), 1) if bleu1_scores else 0.0,
        "jaccard": sum(jaccard_scores) / max(len(jaccard_scores), 1) if jaccard_scores else 0.0,
        "locomo_f1": sum(locomo_f1_scores) / max(len(locomo_f1_scores), 1) if locomo_f1_scores else 0.0
    }
    
    # Per-category metrics
    by_category: Dict[str, Dict[str, Any]] = {}
    for cat in category_counts:
        f1_list = category_f1.get(cat, [])
        b1_list = category_bleu1.get(cat, [])
        j_list = category_jaccard.get(cat, [])
        lf_list = category_locomo_f1.get(cat, [])
        
        by_category[cat] = {
            "count": category_counts[cat],
            "f1": sum(f1_list) / max(len(f1_list), 1) if f1_list else 0.0,
            "bleu1": sum(b1_list) / max(len(b1_list), 1) if b1_list else 0.0,
            "jaccard": sum(j_list) / max(len(j_list), 1) if j_list else 0.0,
            "locomo_f1": sum(lf_list) / max(len(lf_list), 1) if lf_list else 0.0
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
            "group_id": group_id,
            "search_type": search_type,
            "search_limit": search_limit,
            "context_char_budget": context_char_budget,
            "llm_id": SELECTED_LLM_ID,
            "embedding_id": SELECTED_EMBEDDING_ID
        },
        "overall_metrics": overall_metrics,
        "by_category": by_category,
        "latency": latency,
        "context_stats": context_stats,
        "samples": samples
    }
    
    # Step 6: Save results
    if output_dir is None:
        output_dir = os.path.join(
            os.path.dirname(__file__),
            "results"
        )
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate timestamped filename
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"locomo_{timestamp_str}.json")
    
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"✅ Results saved to: {output_path}\n")
    except Exception as e:
        print(f"❌ Failed to save results: {e}")
        print("📊 Printing results to console instead:\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
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
        "--group_id",
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
        group_id=args.group_id,
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
