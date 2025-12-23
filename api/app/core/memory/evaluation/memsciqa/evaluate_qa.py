import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv():
        return None

from app.core.memory.evaluation.common.metrics import (
    avg_context_tokens,
    exact_match,
    latency_stats,
)
from app.core.memory.evaluation.extraction_utils import (
    ingest_contexts_via_full_pipeline,
)
from app.core.memory.storage_services.search import run_hybrid_search
from app.core.memory.utils.config.definitions import (
    PROJECT_ROOT,
    SELECTED_EMBEDDING_ID,
    SELECTED_GROUP_ID,
    SELECTED_LLM_ID,
)
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


def smart_context_selection(contexts: List[str], question: str, max_chars: int = 4000) -> str:
    """基于问题关键词对上下文进行评分选择，并在预算内拼接文本。"""
    if not contexts:
        return ""
    import re
    # 提取问题关键词（移除停用词）
    question_lower = (question or "").lower()
    stop_words = {
        'what','when','where','who','why','how','did','do','does','is','are','was','were',
        'the','a','an','and','or','but'
    }
    question_words = set(re.findall(r"\b\w+\b", question_lower))
    question_words = {w for w in question_words if w not in stop_words and len(w) > 2}

    # 评分
    scored = []
    for i, ctx in enumerate(contexts):
        ctx_lower = (ctx or "").lower()
        score = 0
        matches = 0
        for w in question_words:
            if w in ctx_lower:
                matches += 1
                score += ctx_lower.count(w) * 2
        length = len(ctx)
        if 100 < length < 2000:
            score += 5
        elif length >= 2000:
            score += 2
        if i < 3:
            score += 3
        scored.append((score, ctx, matches))

    scored.sort(key=lambda x: x[0], reverse=True)

    # 选择直到达到字符限制，必要时截断包含关键词的段落
    selected: List[str] = []
    total = 0
    for score, ctx, _ in scored:
        if total + len(ctx) <= max_chars:
            selected.append(ctx)
            total += len(ctx)
        else:
            if score > 10 and total < max_chars - 200:
                remaining = max_chars - total
                lines = ctx.split('\n')
                rel_lines: List[str] = []
                cur = 0
                for line in lines:
                    l = line.lower()
                    if any(w in l for w in question_words) and cur < remaining - 50:
                        rel_lines.append(line)
                        cur += len(line)
                if rel_lines:
                    truncated = '\n'.join(rel_lines)
                    if len(truncated) > 50:
                        selected.append(truncated + "\n[相关内容截断...]")
                        total += len(truncated)
            break
    return "\n\n".join(selected)


def build_context_from_dialog(dialog_obj: Dict[str, Any]) -> str:
    """Compose a text context from `dialog` list in msc_self_instruct item."""
    parts: List[str] = []
    for turn in dialog_obj.get("dialog", []):
        speaker = turn.get("speaker", "")
        text = turn.get("text", "")
        if text:
            parts.append(f"{speaker}: {text}")
    return "\n".join(parts)


def _combine_dialogues_for_hybrid(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Combine dialogues from embedding and keyword searches (embedding first)."""
    if results is None:
        return []
    emb = []
    kw = []
    if isinstance(results.get("embedding_search"), dict):
        emb = results.get("embedding_search", {}).get("dialogues", []) or []
    elif isinstance(results.get("dialogues"), list):
        emb = results.get("dialogues", []) or []
    if isinstance(results.get("keyword_search"), dict):
        kw = results.get("keyword_search", {}).get("dialogues", []) or []
    seen = set()
    merged: List[Dict[str, Any]] = []
    for d in emb:
        k = (str(d.get("uuid", "")), str(d.get("content", "")))
        if k not in seen:
            merged.append(d)
            seen.add(k)
    for d in kw:
        k = (str(d.get("uuid", "")), str(d.get("content", "")))
        if k not in seen:
            merged.append(d)
            seen.add(k)
    return merged


async def run_memsciqa_eval(sample_size: int = 1, group_id: str | None = None, search_limit: int = 8, context_char_budget: int = 4000, llm_temperature: float = 0.0, llm_max_tokens: int = 64, search_type: str = "hybrid", memory_config: "MemoryConfig" = None) -> Dict[str, Any]:
    group_id = group_id or SELECTED_GROUP_ID
    # Load data
    data_path = os.path.join(PROJECT_ROOT, "data", "msc_self_instruct.jsonl")
    if not os.path.exists(data_path):
        data_path = os.path.join(os.getcwd(), "data", "msc_self_instruct.jsonl")
    with open(data_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    items: List[Dict[str, Any]] = [json.loads(l) for l in lines[:sample_size]]
    # 改为：每条样本仅摄入一个上下文（完整对话转录），避免多上下文摄入
    # 说明：memsciqa 数据集的每个样本天然只有一个对话，保持按样本一上下文的策略
    contexts: List[str] = [build_context_from_dialog(item) for item in items]
    await ingest_contexts_via_full_pipeline(contexts, group_id)

    # LLM client (使用异步调用)
    with get_db_context() as db:
        factory = MemoryClientFactory(db)
        llm_client = factory.get_llm_client(SELECTED_LLM_ID)

    # Evaluate each item
    connector = Neo4jConnector()
    latencies_llm: List[float] = []
    latencies_search: List[float] = []
    contexts_used: List[str] = []
    correct_flags: List[float] = []
    f1s: List[float] = []
    b1s: List[float] = []
    jss: List[float] = []
    try:
        for item in items:
            question = item.get("self_instruct", {}).get("B", "") or item.get("question", "")
            reference = item.get("self_instruct", {}).get("A", "") or item.get("answer", "")
            # 检索：对齐 locomo 的三路检索（dialogues/statements/entities）
            t0 = time.time()
            try:
                results = await run_hybrid_search(
                    query_text=question,
                    search_type=search_type,
                    group_id=group_id,
                    limit=search_limit,
                    include=["dialogues", "statements", "entities"],
                    output_path=None,
                    memory_config=memory_config,
                )
            except Exception:
                results = None
            t1 = time.time()
            latencies_search.append((t1 - t0) * 1000)

            # 构建上下文：包含对话、陈述和实体摘要，并智能选择
            contexts_all: List[str] = []
            if results:
                if search_type == "hybrid":
                    emb = results.get("embedding_search", {}) if isinstance(results.get("embedding_search"), dict) else {}
                    kw = results.get("keyword_search", {}) if isinstance(results.get("keyword_search"), dict) else {}
                    emb_dialogs = emb.get("dialogues", [])
                    emb_statements = emb.get("statements", [])
                    emb_entities = emb.get("entities", [])
                    kw_dialogs = kw.get("dialogues", [])
                    kw_statements = kw.get("statements", [])
                    kw_entities = kw.get("entities", [])
                    all_dialogs = emb_dialogs + kw_dialogs
                    all_statements = emb_statements + kw_statements
                    all_entities = emb_entities + kw_entities

                    # 简单去重与限制
                    seen_texts = set()
                    for d in all_dialogs:
                        text = str(d.get("content", "")).strip()
                        if text and text not in seen_texts:
                            contexts_all.append(text)
                            seen_texts.add(text)
                            if len(contexts_all) >= search_limit:
                                break
                    for s in all_statements:
                        text = str(s.get("statement", "")).strip()
                        if text and text not in seen_texts:
                            contexts_all.append(text)
                            seen_texts.add(text)
                            if len(contexts_all) >= search_limit:
                                break
                    # 实体摘要（最多3个）
                    names = []
                    merged_entities = all_entities[:]
                    for e in merged_entities:
                        name = str(e.get("name", "")).strip()
                        if name and name not in names:
                            names.append(name)
                        if len(names) >= 3:
                            break
                    if names:
                        contexts_all.append("EntitySummary: " + ", ".join(names))
                else:
                    dialogs = results.get("dialogues", [])
                    statements = results.get("statements", [])
                    entities = results.get("entities", [])
                    for d in dialogs:
                        text = str(d.get("content", "")).strip()
                        if text:
                            contexts_all.append(text)
                    for s in statements:
                        text = str(s.get("statement", "")).strip()
                        if text:
                            contexts_all.append(text)
                    names = [str(e.get("name", "")).strip() for e in entities[:3] if e.get("name")]
                    if names:
                        contexts_all.append("EntitySummary: " + ", ".join(names))

            # 智能选择并截断到预算
            context_text = smart_context_selection(contexts_all, question, max_chars=context_char_budget) if contexts_all else ""
            if not context_text:
                context_text = "No relevant context found."
            contexts_used.append(context_text[:200])

            # Call LLM (使用异步调用)
            messages = [
                {"role": "system", "content": "You are a QA assistant. Answer in English. Strictly follow: 1) If the context contains the answer, copy the shortest exact span from the context as the answer; 2) If the answer cannot be determined from the context, respond with 'Unknown'; 3) Return ONLY the answer text, no explanations."},
                {"role": "user", "content": f"Question: {question}\n\nContext:\n{context_text}"},
            ]
            t2 = time.time()
            resp = await llm_client.chat(messages=messages)
            t3 = time.time()
            latencies_llm.append((t3 - t2) * 1000)
            pred = resp.content.strip() if hasattr(resp, 'content') else (resp["choices"][0]["message"]["content"].strip() if isinstance(resp, dict) else str(resp).strip())
            # Metrics: F1, BLEU-1, Jaccard; keep exact match for reference
            correct_flags.append(exact_match(pred, reference))
            from app.core.memory.evaluation.common.metrics import (
                bleu1,
                f1_score,
                jaccard,
            )
            f1s.append(f1_score(str(pred), str(reference)))
            b1s.append(bleu1(str(pred), str(reference)))
            jss.append(jaccard(str(pred), str(reference)))

        # Aggregate metrics
        acc = sum(correct_flags) / max(len(correct_flags), 1)
        ctx_avg_tokens = avg_context_tokens(contexts_used)
        result = {
            "dataset": "memsciqa",
            "items": len(items),
            "metrics": {
                "accuracy": acc,
                # Placeholders for extensibility
                "f1": (sum(f1s) / max(len(f1s), 1)) if f1s else 0.0,
                "bleu1": (sum(b1s) / max(len(b1s), 1)) if b1s else 0.0,
                "jaccard": (sum(jss) / max(len(jss), 1)) if jss else 0.0,
            },
            "latency": {
                "search": latency_stats(latencies_search),
                "llm": latency_stats(latencies_llm),
            },
            "avg_context_tokens": ctx_avg_tokens,
        }
        return result
    finally:
        await connector.close()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Evaluate DMR (memsciqa) with graph search and Qwen")
    parser.add_argument("--sample-size", type=int, default=1, help="评测样本数量")
    parser.add_argument("--group-id", type=str, default=None, help="可选 group_id，默认取 runtime.json")
    parser.add_argument("--search-limit", type=int, default=8, help="每类检索最大返回数")
    parser.add_argument("--context-char-budget", type=int, default=4000, help="上下文字符预算")
    parser.add_argument("--llm-temperature", type=float, default=0.0, help="LLM 温度")
    parser.add_argument("--llm-max-tokens", type=int, default=64, help="LLM 最大生成长度")
    parser.add_argument("--search-type", type=str, choices=["keyword","embedding","hybrid"], default="hybrid", help="检索类型")
    args = parser.parse_args()

    result = asyncio.run(
        run_memsciqa_eval(
            sample_size=args.sample_size,
            group_id=args.group_id,
            search_limit=args.search_limit,
            context_char_budget=args.context_char_budget,
            llm_temperature=args.llm_temperature,
            llm_max_tokens=args.llm_max_tokens,
            search_type=args.search_type,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
