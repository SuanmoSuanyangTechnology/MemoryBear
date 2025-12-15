import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict

# Add src directory to Python path for proper imports when running from evaluation directory
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv():
        return None

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.utils.config.definitions import SELECTED_GROUP_ID, PROJECT_ROOT

from app.core.memory.evaluation.memsciqa.evaluate_qa import run_memsciqa_eval
from app.core.memory.evaluation.longmemeval.qwen_search_eval import run_longmemeval_test
from app.core.memory.evaluation.locomo.qwen_search_eval import run_locomo_eval


async def run(
    dataset: str,
    sample_size: int,
    reset_group: bool,
    group_id: str | None,
    judge_model: str | None = None,
    search_limit: int | None = None,
    context_char_budget: int | None = None,
    llm_temperature: float | None = None,
    llm_max_tokens: int | None = None,
    search_type: str | None = None,
    start_index: int | None = None,
    max_contexts_per_item: int | None = None,
) -> Dict[str, Any]:
    # 恢复原始风格：统一入口做路由，并沿用各数据集既有默认
    group_id = group_id or SELECTED_GROUP_ID

    if reset_group:
        connector = Neo4jConnector()
        try:
            await connector.delete_group(group_id)
        finally:
            await connector.close()

    if dataset == "locomo":
        kwargs: Dict[str, Any] = {"sample_size": sample_size, "group_id": group_id}
        if search_limit is not None:
            kwargs["search_limit"] = search_limit
        if context_char_budget is not None:
            kwargs["context_char_budget"] = context_char_budget
        if llm_temperature is not None:
            kwargs["llm_temperature"] = llm_temperature
        if llm_max_tokens is not None:
            kwargs["llm_max_tokens"] = llm_max_tokens
        if search_type is not None:
            kwargs["search_type"] = search_type
        return await run_locomo_eval(**kwargs)

    if dataset == "memsciqa":
        kwargs: Dict[str, Any] = {"sample_size": sample_size, "group_id": group_id}
        if search_limit is not None:
            kwargs["search_limit"] = search_limit
        if context_char_budget is not None:
            kwargs["context_char_budget"] = context_char_budget
        if llm_temperature is not None:
            kwargs["llm_temperature"] = llm_temperature
        if llm_max_tokens is not None:
            kwargs["llm_max_tokens"] = llm_max_tokens
        if search_type is not None:
            kwargs["search_type"] = search_type
        return await run_memsciqa_eval(**kwargs)

    if dataset == "longmemeval":
        kwargs: Dict[str, Any] = {"sample_size": sample_size, "group_id": group_id}
        if search_limit is not None:
            kwargs["search_limit"] = search_limit
        if context_char_budget is not None:
            kwargs["context_char_budget"] = context_char_budget
        if llm_temperature is not None:
            kwargs["llm_temperature"] = llm_temperature
        if llm_max_tokens is not None:
            kwargs["llm_max_tokens"] = llm_max_tokens
        if search_type is not None:
            kwargs["search_type"] = search_type
        if start_index is not None:
            kwargs["start_index"] = start_index
        if max_contexts_per_item is not None:
            kwargs["max_contexts_per_item"] = max_contexts_per_item
        return await run_longmemeval_test(**kwargs)
    raise ValueError(f"未知数据集: {dataset}")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="统一评估入口：memsciqa / longmemeval / locomo")
    parser.add_argument("--dataset", choices=["memsciqa", "longmemeval", "locomo"], required=True)
    parser.add_argument("--sample-size", type=int, default=1, help="先用一条数据跑通")
    parser.add_argument("--reset-group", action="store_true", help="运行前清空当前 group_id 的图数据")
    parser.add_argument("--group-id", type=str, default=None, help="可选 group_id，默认取 runtime.json")
    parser.add_argument("--judge-model", type=str, default=None, help="可选：longmemeval 判别式评测模型名")
    parser.add_argument("--search-limit", type=int, default=None, help="检索返回的对话节点数量上限（不提供则使用各脚本默认）")
    parser.add_argument("--context-char-budget", type=int, default=None, help="上下文字符预算（不提供则使用各脚本默认）")
    parser.add_argument("--llm-temperature", type=float, default=None, help="生成温度（不提供则使用各脚本默认）")
    parser.add_argument("--llm-max-tokens", type=int, default=None, help="最大生成 tokens（不提供则使用各脚本默认）")
    parser.add_argument("--search-type", type=str, default=None, choices=["keyword", "embedding", "hybrid"], help="检索类型（不提供则使用各脚本默认）")
    # 仅透传到 longmemeval；其他数据集忽略
    parser.add_argument("--start-index", type=int, default=None, help="仅 longmemeval：起始样本索引（不提供则用脚本默认）")
    parser.add_argument("--max-contexts-per-item", type=int, default=None, help="仅 longmemeval：每条样本摄入的上下文数量上限（不提供则用脚本默认）")
    parser.add_argument("--output", type=str, default=None, help="可选：将评估结果保存到指定文件路径（JSON）；不提供时默认保存到 evaluation/<dataset>/results 目录")
    args = parser.parse_args()

    result = asyncio.run(run(
        args.dataset,
        args.sample_size,
        args.reset_group,
        args.group_id,
        args.judge_model,
        args.search_limit,
        args.context_char_budget,
        args.llm_temperature,
        args.llm_max_tokens,
        args.search_type,
        args.start_index,
        args.max_contexts_per_item,
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 结果输出逻辑保持不变
    if args.output:
        out_path = args.output
    else:
        eval_dir = os.path.dirname(os.path.abspath(__file__))
        dataset_results_dir = os.path.join(eval_dir, args.dataset, "results")
        out_filename = f"{args.dataset}_{args.sample_size}.json"
        out_path = os.path.join(dataset_results_dir, out_filename)

    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到: {out_path}")


if __name__ == "__main__":
    main()
