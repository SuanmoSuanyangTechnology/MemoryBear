import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from typing import List, Dict, Any
import re

from dotenv import load_dotenv
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.src.search import run_hybrid_search  # 使用与 evaluate_qa.py 相同的检索函数
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.models.base import RedBearModelConfig
from app.core.memory.utils.config.config_utils import get_embedder_config

from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.memory.evaluation.config import DATASET_DIR, SELECTED_GROUP_ID, SELECTED_EMBEDDING_ID, SELECTED_LLM_ID
from app.core.memory.evaluation.common.metrics import exact_match, latency_stats, avg_context_tokens

from app.core.memory.evaluation.common.metrics import f1_score, bleu1, jaccard


def smart_context_selection(contexts: List[str], question: str, max_chars: int = 4000) -> str:
    """基于问题关键词对上下文进行评分选择，并在预算内拼接文本。

    参考 evaluation/memsciqa/evaluate_qa.py 的实现，避免路径导入带来的不稳定。
    """
    if not contexts:
        return ""
    question_lower = (question or "").lower()
    stop_words = {
        'what','when','where','who','why','how','did','do','does','is','are','was','were',
        'the','a','an','and','or','but'
    }
    question_words = set(re.findall(r"\b\w+\b", question_lower))
    question_words = {w for w in question_words if w not in stop_words and len(w) > 2}

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


def extract_question_keywords(question: str, max_keywords: int = 8) -> List[str]:
    """提取问题中的关键词（简单英文分词，去停用词，长度>=3）。"""
    ql = (question or "").lower()
    stop_words = {
        'what','when','where','who','why','how','did','do','does','is','are','was','were',
        'the','a','an','and','or','but','of','to','in','on','for','with','from','that','this'
    }
    words = re.findall(r"\b[\w-]+\b", ql)
    kws = [w for w in words if w not in stop_words and len(w) >= 3]
    # 去重保序
    seen = set()
    uniq = []
    for w in kws:
        if w not in seen:
            uniq.append(w)
            seen.add(w)
        if len(uniq) >= max_keywords:
            break
    return uniq


def analyze_contexts_simple(contexts: List[str], keywords: List[str], top_n: int = 5) -> List[Dict[str, int | float]]:
    """对上下文进行简单相关性打分，仅用于控制台可视化。

    评分: score = match_count*200 + min(len(text), 100000)/100
    """
    results = []
    for ctx in contexts:
        tl = (ctx or "").lower()
        match_count = sum(1 for k in keywords if k in tl)
        length = len(ctx)
        score = match_count * 200 + min(length, 100000) / 100.0
        results.append({"score": float(f"{score:.0f}"), "match": match_count, "length": length})
    results.sort(key=lambda x: (x["score"], x["match"], x["length"]), reverse=True)
    return results[:max(top_n, 0)]


# 纯测试脚本不进行摄入；若需摄入请使用 evaluate_qa.py


def load_dataset_memsciqa(data_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(data_path):
        raise FileNotFoundError(f"未找到数据集: {data_path}")
    items: List[Dict[str, Any]] = []
    with open(data_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                # 跳过坏行但不中断
                continue
    return items


async def run_memsciqa_test(
    sample_size: int = 3,
    group_id: str | None = None,
    search_limit: int = 8,
    context_char_budget: int = 4000,
    llm_temperature: float = 0.0,
    llm_max_tokens: int = 64,
    search_type: str = "embedding",
    data_path: str | None = None,
    start_index: int = 0,
    verbose: bool = True,
) -> Dict[str, Any]:
    """memsciqa 增强测试脚本：结合 evaluate_qa 的三路检索与智能上下文选择。

    - 支持从指定索引开始与评估全部样本（sample_size<=0）
    - 支持在摄入前重置组（清空图）与跳过摄入
    - 支持 keyword / embedding / hybrid 三种检索
    """

    # 默认使用指定的 memsci 组 ID
    group_id = group_id or "group_memsci"

    # 数据路径解析
    if not data_path:
        data_path = os.path.join(DATASET_DIR, "msc_self_instruct.jsonl")
        if not os.path.exists(data_path):
            raise FileNotFoundError(
                f"数据集文件不存在: {data_path}\n"
                f"请将 msc_self_instruct.jsonl 放置在: {DATASET_DIR}"
            )

    # 加载数据
    all_items = load_dataset_memsciqa(data_path)
    if sample_size is None or sample_size <= 0:
        items = all_items[start_index:]
    else:
        items = all_items[start_index:start_index + sample_size]

    # 初始化 LLM（纯测试：不进行摄入）
    llm = get_llm_client(SELECTED_LLM_ID)

    # 初始化 Neo4j 连接与向量检索 Embedder（对齐 locomo_test）
    connector = Neo4jConnector()
    embedder = None
    if search_type in ("embedding", "hybrid"):
        cfg_dict = get_embedder_config(SELECTED_EMBEDDING_ID)
        embedder = OpenAIEmbedderClient(
            model_config=RedBearModelConfig.model_validate(cfg_dict)
        )

    # 评估循环
    latencies_llm: List[float] = []
    latencies_search: List[float] = []
    # 存储完整上下文文本用于统计
    contexts_used: List[str] = []
    per_query_context_chars: List[int] = []
    per_query_context_counts: List[int] = []
    correct_flags: List[float] = []
    f1s: List[float] = []
    b1s: List[float] = []
    jss: List[float] = []
    samples: List[Dict[str, Any]] = []

    total_items = len(items)
    for idx, item in enumerate(items):
        if verbose:
            print(f"\n🧪 评估样本: {idx+1}/{total_items}")
        question = item.get("self_instruct", {}).get("B", "") or item.get("question", "")
        reference = item.get("self_instruct", {}).get("A", "") or item.get("answer", "")

        # 检索：使用与 evaluate_qa.py 相同的 run_hybrid_search
        t0 = time.time()
        results = None
        try:
            results = await run_hybrid_search(
                query_text=question,
                search_type=search_type,
                group_id=group_id,
                limit=search_limit,
                include=["dialogues", "statements", "entities"],
                output_path=None,
            )
        except Exception as e:
            if verbose:
                print(f"⚠️ 检索异常: {e}")
            results = None
        t1 = time.time()
        search_ms = (t1 - t0) * 1000
        latencies_search.append(search_ms)

        # 构建上下文：与 evaluate_qa.py 完全一致的逻辑
        contexts_all: List[str] = []
        retrieved_counts: Dict[str, int] = {}
        if results:
            # 处理 hybrid 搜索结果
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
                
                # 简单去重
                seen_dialog = set()
                dialogues = []
                for d in all_dialogs:
                    key = (str(d.get("uuid", "")), str(d.get("content", "")))
                    if key not in seen_dialog:
                        dialogues.append(d)
                        seen_dialog.add(key)
                
                seen_stmt = set()
                statements = []
                for s in all_statements:
                    key = str(s.get("statement", ""))
                    if key not in seen_stmt:
                        statements.append(s)
                        seen_stmt.add(key)
                
                seen_ent = set()
                entities = []
                for e in all_entities:
                    key = str(e.get("name", ""))
                    if key not in seen_ent:
                        entities.append(e)
                        seen_ent.add(key)
            else:
                # embedding 或 keyword 单独搜索
                dialogues = results.get("dialogues", [])
                statements = results.get("statements", [])
                entities = results.get("entities", [])
            
            retrieved_counts = {
                "dialogues": len(dialogues),
                "statements": len(statements),
                "entities": len(entities),
            }
            
            # 构建上下文文本
            for d in dialogues:
                text = str(d.get("content", "")).strip()
                if text:
                    contexts_all.append(text)
            
            for s in statements:
                text = str(s.get("statement", "")).strip()
                if text:
                    contexts_all.append(text)
            
            # 实体摘要
            if entities:
                scored = [e for e in entities if e.get("score") is not None]
                top_entities = sorted(scored, key=lambda x: x.get("score", 0), reverse=True)[:3] if scored else entities[:3]
                if top_entities:
                    summary_lines = []
                    for e in top_entities:
                        name = str(e.get("name", "")).strip()
                        etype = str(e.get("entity_type", "")).strip()
                        score = e.get("score")
                        if name:
                            meta = []
                            if etype:
                                meta.append(f"type={etype}")
                            if isinstance(score, (int, float)):
                                meta.append(f"score={score:.3f}")
                            summary_lines.append(f"EntitySummary: {name}{(' [' + '; '.join(meta) + ']') if meta else ''}")
                    if summary_lines:
                        contexts_all.append("\n".join(summary_lines))

        if verbose:
            if retrieved_counts:
                print(f"✅ 检索成功: {retrieved_counts.get('dialogues',0)} dialogues, {retrieved_counts.get('statements',0)} 条陈述, {retrieved_counts.get('entities',0)} 个实体, {retrieved_counts.get('summaries',0)} 个摘要")
            print(f"📊 有效上下文数量: {len(contexts_all)}")
            q_keywords = extract_question_keywords(question, max_keywords=8)
            if q_keywords:
                print(f"🔍 问题关键词: {set(q_keywords)}")
            if contexts_all:
                analysis = analyze_contexts_simple(contexts_all, q_keywords, top_n=5)
                if analysis:
                    print("📊 上下文相关性分析:")
                    for a in analysis:
                        print(f"  - 得分: {int(a['score'])}, 关键词匹配: {a['match']}, 长度: {a['length']}")
                # 打印检索到的上下文预览，便于定位为何为 Unknown
                print("🔎 上下文预览（最多前10条，每条截断展示）:")
                for i, ctx in enumerate(contexts_all[:10]):
                    preview = str(ctx).replace("\n", " ")
                    if len(preview) > 300:
                        preview = preview[:300] + "..."
                    print(f"  [{i+1}] 长度: {len(ctx)} | 片段: {preview}")
                # 标注参考答案是否出现在任一上下文中
                ref_lower = (str(reference) or "").lower()
                if ref_lower:
                    hits = []
                    for i, ctx in enumerate(contexts_all):
                        if ref_lower in str(ctx).lower():
                            hits.append(i+1)
                    print(f"🔗 参考答案命中上下文条数: {len(hits)}" + (f" | 命中索引: {hits}" if hits else ""))

        context_text = smart_context_selection(contexts_all, question, max_chars=context_char_budget) if contexts_all else ""
        if not context_text:
            context_text = "No relevant context found."
        contexts_used.append(context_text)
        per_query_context_chars.append(len(context_text))
        per_query_context_counts.append(len(contexts_all))

        if verbose:
            selected_count = (context_text.count("\n\n") + 1) if context_text else 0
            print(f"✅ 智能选择: {selected_count}个上下文, 总长度: {len(context_text)}字符")
            # 展示拼接后的上下文片段，便于核查是否包含答案
            concat_preview = context_text.replace("\n", " ")
            if len(concat_preview) > 600:
                concat_preview = concat_preview[:600] + "..."
            print(f"🧵 拼接上下文预览: {concat_preview}")

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a QA assistant. Answer in English. Follow these guidelines:\n"
                    "1) If the context contains information to answer the question, provide a concise answer based on the context;\n"
                    "2) If the context does not contain enough information to answer the question, respond with 'Unknown';\n"
                    "3) Keep your answer brief and to the point;\n"
                    "4) Do not add explanations or additional text beyond the answer."
                ),
            },
            {"role": "user", "content": f"Question: {question}\n\nContext:\n{context_text}"},
        ]

        t2 = time.time()
        try:
            # 使用异步调用
            resp = await llm.chat(messages=messages)
            # 更健壮的响应解析，处理不同的LLM响应格式
            if hasattr(resp, 'content'):
                pred = resp.content.strip()
            elif isinstance(resp, dict) and "choices" in resp and len(resp["choices"]) > 0:
                pred = resp["choices"][0]["message"]["content"].strip()
            elif isinstance(resp, dict) and "content" in resp:
                pred = resp["content"].strip()
            elif isinstance(resp, str):
                pred = resp.strip()
            else:
                pred = "Unknown"
                print(f"⚠️  LLM响应格式异常: {type(resp)} - {resp}")

            # 检查预测是否为"Unknown"或空，如果是则检查上下文是否真的没有答案
            if pred.lower() in ["unknown", ""]:
                # 如果参考答案在上下文中存在，但LLM返回Unknown，可能是提示词问题
                ref_lower = (str(reference) or "").lower()
                if ref_lower and any(ref_lower in ctx.lower() for ctx in contexts_all):
                    print("⚠️  参考答案在上下文中存在但LLM返回Unknown，检查提示词")
        except Exception as e:
            # 更详细的错误处理
            pred = "Unknown"
            print(f"⚠️  LLM调用异常: {e}")
        t3 = time.time()
        llm_ms = (t3 - t2) * 1000
        latencies_llm.append(llm_ms)

        exact = exact_match(pred, reference)
        correct_flags.append(exact)
        f1_val = f1_score(str(pred), str(reference))
        b1_val = bleu1(str(pred), str(reference))
        j_val = jaccard(str(pred), str(reference))
        f1s.append(f1_val)
        b1s.append(b1_val)
        jss.append(j_val)

        if verbose:
            print(f"🤖 LLM 回答: {pred}")
            print(f"✅ 正确答案: {reference}")
            print(f"📈 当前指标 - F1: {f1_val:.3f}, BLEU-1: {b1_val:.3f}, Jaccard: {j_val:.3f}")
            print(f"⏱️ 延迟 - 检索: {search_ms:.0f}ms, LLM: {llm_ms:.0f}ms")

        # 对齐 locomo/qwen_search_eval.py 的样本输出结构
        samples.append({
            "question": str(question),
            "answer": str(reference),
            "prediction": str(pred),
            "metrics": {
                "f1": f1_val,
                "b1": b1_val,
                "j": j_val
            },
            "retrieval": {
                "retrieved_documents": len(contexts_all),
                "context_length": len(context_text),
                "search_limit": search_limit,
                "max_chars": context_char_budget
            },
            "timing": {
                "search_ms": search_ms,
                "llm_ms": llm_ms
            }
        })

    # 计算总体指标与聚合
    acc = sum(correct_flags) / max(len(correct_flags), 1)
    ctx_avg_tokens = avg_context_tokens(contexts_used)
    result = {
        "dataset": "memsciqa",
        "items": len(items),
        "metrics": {
            "f1": (sum(f1s) / max(len(f1s), 1)) if f1s else 0.0,
            "b1": (sum(b1s) / max(len(b1s), 1)) if b1s else 0.0,
            "j": (sum(jss) / max(len(jss), 1)) if jss else 0.0,
        },
        "context": {
            "avg_tokens": ctx_avg_tokens,
            "avg_chars": (sum(per_query_context_chars) / max(len(per_query_context_chars), 1)) if per_query_context_chars else 0.0,
            "count_avg": (sum(per_query_context_counts) / max(len(per_query_context_counts), 1)) if per_query_context_counts else 0.0,
            "avg_memory_tokens": 0.0
        },
        "latency": {
            "search": latency_stats(latencies_search),
            "llm": latency_stats(latencies_llm),
        },
        "samples": samples,
        "params": {
            "group_id": group_id,
            "search_limit": search_limit,
            "context_char_budget": context_char_budget,
            "llm_temperature": llm_temperature,
            "llm_max_tokens": llm_max_tokens,
            "search_type": search_type,
            "start_index": start_index,
            "llm_id": SELECTED_LLM_ID,
            "retrieval_embedding_id": SELECTED_EMBEDDING_ID
        },
        "timestamp": datetime.now().isoformat(),
    }
    try:
        await connector.close()
    except Exception:
        pass
    return result


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="memsciqa 测试脚本（三路检索 + 智能上下文选择）")
    parser.add_argument("--sample-size", type=int, default=10, help="样本数量（<=0 表示全部）")
    parser.add_argument("--all", action="store_true", help="评估全部样本（覆盖 --sample-size）")
    parser.add_argument("--start-index", type=int, default=0, help="起始样本索引")
    parser.add_argument("--group-id", type=str, default="group_memsci", help="图数据库 Group ID（默认 group_memsci）")
    parser.add_argument("--search-limit", type=int, default=8, help="检索条数上限")
    parser.add_argument("--context-char-budget", type=int, default=4000, help="上下文字符预算")
    parser.add_argument("--llm-temperature", type=float, default=0.0, help="LLM 温度")
    parser.add_argument("--llm-max-tokens", type=int, default=64, help="LLM 最大输出 token")
    parser.add_argument("--search-type", type=str, default="embedding", choices=["embedding","keyword","hybrid"], help="检索类型（hybrid 等同于 embedding）")
    parser.add_argument("--data-path", type=str, default=None, help="数据集路径（默认 data/msc_self_instruct.jsonl）")
    parser.add_argument("--output", type=str, default=None, help="将评估结果保存到指定文件路径（JSON）")
    parser.add_argument("--verbose", action="store_true", default=True, help="打印过程日志（默认开启）")
    parser.add_argument("--quiet", action="store_true", help="关闭过程日志")
    args = parser.parse_args()

    sample_size = 0 if args.all else args.sample_size

    verbose_flag = False if args.quiet else args.verbose
    result = asyncio.run(
        run_memsciqa_test(
            sample_size=sample_size,
            group_id=args.group_id,
            search_limit=args.search_limit,
            context_char_budget=args.context_char_budget,
            llm_temperature=args.llm_temperature,
            llm_max_tokens=args.llm_max_tokens,
            search_type=args.search_type,
            data_path=args.data_path,
            start_index=args.start_index,
            verbose=verbose_flag,
        )
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 结果保存
    out_path = args.output
    if not out_path:
        eval_dir = os.path.dirname(os.path.abspath(__file__))
        dataset_results_dir = os.path.join(eval_dir, "results")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(dataset_results_dir, f"memsciqa_{result['params']['search_type']}_{ts}.json")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n💾 结果已保存: {out_path}")
    except Exception as e:
        print(f"⚠️ 结果保存失败: {e}")


if __name__ == "__main__":
    main()
