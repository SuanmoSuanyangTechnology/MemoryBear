import argparse
import asyncio
import json
import os
import statistics
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv():
        return None

import re

from app.core.memory.evaluation.common.metrics import (
    avg_context_tokens,
    bleu1,
    jaccard,
    latency_stats,
)
from app.core.memory.evaluation.common.metrics import f1_score as common_f1
from app.core.memory.evaluation.extraction_utils import (
    ingest_contexts_via_full_pipeline,
)
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.memory.storage_services.search import run_hybrid_search
from app.core.memory.utils.config.definitions import (
    PROJECT_ROOT,
    SELECTED_EMBEDDING_ID,
    SELECTED_GROUP_ID,
    SELECTED_LLM_ID,
)
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.core.models.base import RedBearModelConfig
from app.db import get_db_context
from app.repositories.neo4j.graph_search import search_graph, search_graph_by_embedding
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services.memory_config_service import MemoryConfigService


# 参考 evaluation/locomo/evaluation.py 的 F1 计算逻辑（移除外部依赖，内联实现）
def _loc_normalize(text: str) -> str:
    import re
    # 确保输入是字符串
    text = str(text) if text is not None else ""
    text = text.lower()
    text = re.sub(r"[\,]", " ", text)  # 去掉逗号
    text = re.sub(r"\b(a|an|the|and)\b", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return text

# 追加：相对时间归一化为绝对日期（有限支持：today/yesterday/tomorrow/X days ago/in X days/last week/next week）
def _resolve_relative_times(text: str, anchor: datetime) -> str:
    import re
    # 确保输入是字符串
    t = str(text) if text is not None else ""
    # today / yesterday / tomorrow
    t = re.sub(r"\btoday\b", anchor.date().isoformat(), t, flags=re.IGNORECASE)
    t = re.sub(r"\byesterday\b", (anchor - timedelta(days=1)).date().isoformat(), t, flags=re.IGNORECASE)
    t = re.sub(r"\btomorrow\b", (anchor + timedelta(days=1)).date().isoformat(), t, flags=re.IGNORECASE)
    # X days ago / in X days
    def _ago_repl(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return (anchor - timedelta(days=n)).date().isoformat()
    def _in_repl(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return (anchor + timedelta(days=n)).date().isoformat()
    t = re.sub(r"\b(\d+)\s+days\s+ago\b", _ago_repl, t, flags=re.IGNORECASE)
    t = re.sub(r"\bin\s+(\d+)\s+days\b", _in_repl, t, flags=re.IGNORECASE)
    # last week / next week（以7天近似）
    t = re.sub(r"\blast\s+week\b", (anchor - timedelta(days=7)).date().isoformat(), t, flags=re.IGNORECASE)
    t = re.sub(r"\bnext\s+week\b", (anchor + timedelta(days=7)).date().isoformat(), t, flags=re.IGNORECASE)
    return t

def loc_f1_score(prediction: str, ground_truth: str) -> float:
    # 单答案 F1：按词集合计算（近似原始实现，去除词干依赖）
    # 确保输入是字符串
    pred_str = str(prediction) if prediction is not None else ""
    truth_str = str(ground_truth) if ground_truth is not None else ""

    p_tokens = _loc_normalize(pred_str).split()
    g_tokens = _loc_normalize(truth_str).split()
    if not p_tokens or not g_tokens:
        return 0.0
    p = set(p_tokens)
    g = set(g_tokens)
    tp = len(p & g)
    precision = tp / len(p) if p else 0.0
    recall = tp / len(g) if g else 0.0
    return (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

def loc_multi_f1(prediction: str, ground_truth: str) -> float:
    # 多答案 F1：prediction 与 ground_truth 以逗号分隔，逐一匹配取最大，再对多个 GT 取平均
    # 确保输入是字符串
    pred_str = str(prediction) if prediction is not None else ""
    truth_str = str(ground_truth) if ground_truth is not None else ""

    predictions = [p.strip() for p in str(pred_str).split(',') if p.strip()]
    ground_truths = [g.strip() for g in str(truth_str).split(',') if g.strip()]
    if not predictions or not ground_truths:
        return 0.0
    def _f1(a: str, b: str) -> float:
        return loc_f1_score(a, b)
    vals = []
    for gt in ground_truths:
        vals.append(max(_f1(pred, gt) for pred in predictions))
    return sum(vals) / len(vals)

# 标准化 LoCoMo 类别名：支持数字 category 与字符串 cat/type
CATEGORY_MAP_NUM_TO_NAME = {
    4: "Single-Hop",
    1: "Multi-Hop",
    3: "Open Domain",
    2: "Temporal",
}

_TYPE_ALIASES = {
    "single-hop": "Single-Hop",
    "singlehop": "Single-Hop",
    "single hop": "Single-Hop",
    "multi-hop": "Multi-Hop",
    "multihop": "Multi-Hop",
    "multi hop": "Multi-Hop",
    "open domain": "Open Domain",
    "opendomain": "Open Domain",
    "temporal": "Temporal",
}

def get_category_label(item: Dict[str, Any]) -> str:
    # 1) 直接用字符串 cat
    cat = item.get("cat")
    if isinstance(cat, str) and cat.strip():
        name = cat.strip()
        lower = name.lower()
        return _TYPE_ALIASES.get(lower, name)
    # 2) 数字 category 转名称
    cat_num = item.get("category")
    if isinstance(cat_num, int):
        return CATEGORY_MAP_NUM_TO_NAME.get(cat_num, "unknown")
    # 3) 备用 type 字段
    t = item.get("type")
    if isinstance(t, str) and t.strip():
        lower = t.strip().lower()
        return _TYPE_ALIASES.get(lower, t.strip())
    return "unknown"


def smart_context_selection(contexts: List[str], question: str, max_chars: int = 12000) -> str:
    """基于问题关键词智能选择上下文"""
    if not contexts:
        return ""

    # 提取问题关键词（只保留有意义的词）
    question_lower = question.lower()
    stop_words = {'what', 'when', 'where', 'who', 'why', 'how', 'did', 'do', 'does', 'is', 'are', 'was', 'were', 'the', 'a', 'an', 'and', 'or', 'but'}
    question_words = set(re.findall(r'\b\w+\b', question_lower))
    question_words = {word for word in question_words if word not in stop_words and len(word) > 2}

    print(f"🔍 问题关键词: {question_words}")

    # 给每个上下文打分
    scored_contexts = []
    for i, context in enumerate(contexts):
        context_lower = context.lower()
        score = 0

        # 关键词匹配得分
        keyword_matches = 0
        for word in question_words:
            if word in context_lower:
                keyword_matches += 1
                # 关键词出现次数越多，得分越高
                score += context_lower.count(word) * 2

        # 上下文长度得分（适中的长度更好）
        context_len = len(context)
        if 100 < context_len < 2000:  # 理想长度范围
            score += 5
        elif context_len >= 2000:  # 太长可能包含无关信息
            score += 2

        # 如果是前几个上下文，给予额外分数（通常相关性更高）
        if i < 3:
            score += 3

        scored_contexts.append((score, context, keyword_matches))

    # 按得分排序
    scored_contexts.sort(key=lambda x: x[0], reverse=True)

    # 选择高得分的上下文，直到达到字符限制
    selected = []
    total_chars = 0
    selected_count = 0

    print("📊 上下文相关性分析:")
    for score, context, matches in scored_contexts[:5]:  # 只显示前5个
        print(f"  - 得分: {score}, 关键词匹配: {matches}, 长度: {len(context)}")

    for score, context, matches in scored_contexts:
        if total_chars + len(context) <= max_chars:
            selected.append(context)
            total_chars += len(context)
            selected_count += 1
        else:
            # 如果这个上下文得分很高但放不下，尝试截取
            if score > 10 and total_chars < max_chars - 500:
                remaining = max_chars - total_chars
                # 找到包含关键词的部分
                lines = context.split('\n')
                relevant_lines = []
                current_chars = 0

                for line in lines:
                    line_lower = line.lower()
                    line_relevance = any(word in line_lower for word in question_words)

                    if line_relevance and current_chars < remaining - 100:
                        relevant_lines.append(line)
                        current_chars += len(line)

                if relevant_lines:
                    truncated = '\n'.join(relevant_lines)
                    if len(truncated) > 100:  # 确保有足够内容
                        selected.append(truncated + "\n[相关内容截断...]")
                        total_chars += len(truncated)
                        selected_count += 1
            break  # 不再尝试添加更多上下文

    result = "\n\n".join(selected)
    print(f"✅ 智能选择: {selected_count}个上下文, 总长度: {total_chars}字符")
    return result


def get_search_params_by_category(category: str):
    """根据问题类别调整检索参数"""
    params_map = {
        "Multi-Hop": {"limit": 20, "max_chars": 15000},
        "Temporal": {"limit": 16, "max_chars": 10000},
        "Open Domain": {"limit": 24, "max_chars": 18000},
        "Single-Hop": {"limit": 12, "max_chars": 8000},
    }
    return params_map.get(category, {"limit": 16, "max_chars": 12000})


async def run_locomo_eval(
    sample_size: int = 1,
    group_id: str | None = None,
    search_limit: int = 8,
    context_char_budget: int = 4000,  # 保持默认值不变
    llm_temperature: float = 0.0,
    llm_max_tokens: int = 32,
    search_type: str = "hybrid",  # 保持默认值不变
    output_path: str | None = None,
    skip_ingest_if_exists: bool = True,
    llm_timeout: float = 10.0,
    llm_max_retries: int = 1
) -> Dict[str, Any]:

    # 函数内部使用三路检索逻辑，但保持参数签名不变
    group_id = group_id or SELECTED_GROUP_ID
    data_path = os.path.join(PROJECT_ROOT, "data", "locomo10.json")
    if not os.path.exists(data_path):
        data_path = os.path.join(os.getcwd(), "data", "locomo10.json")
    with open(data_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # LoCoMo 数据结构：顶层为若干对象，每个对象下有 qa 列表
    qa_items: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for entry in raw:
            qa_items.extend(entry.get("qa", []))
    else:
        qa_items.extend(raw.get("qa", []))
    items: List[Dict[str, Any]] = qa_items[:sample_size]

    # === 保持原来的数据摄入逻辑 ===
    entries = raw if isinstance(raw, list) else [raw]

    # 只摄入前1条对话（保持原样）
    max_dialogues_to_ingest = 1
    contents: List[str] = []
    print(f"📊 找到 {len(entries)} 个对话对象，只摄入前 {max_dialogues_to_ingest} 条")

    for i, entry in enumerate(entries[:max_dialogues_to_ingest]):
        if not isinstance(entry, dict):
            continue

        conv = entry.get("conversation", {})
        sample_id = entry.get("sample_id", f"unknown_{i}")

        print(f"🔍 处理对话 {i+1}: {sample_id}")

        lines: List[str] = []
        if isinstance(conv, dict):
            # 收集所有 session_* 的消息
            session_count = 0
            for key, val in conv.items():
                if isinstance(val, list) and key.startswith("session_"):
                    session_count += 1
                    for msg in val:
                        role = msg.get("speaker") or "用户"
                        text = msg.get("text") or ""
                        text = str(text).strip()
                        if not text:
                            continue
                        lines.append(f"{role}: {text}")

            print(f"  - 包含 {session_count} 个session, {len(lines)} 条消息")

        if not lines:
            print(f"⚠️  警告: 对话 {sample_id} 没有对话内容，跳过摄入")
            continue

        contents.append("\n".join(lines))

    print(f"📥 总共摄入 {len(contents)} 个对话的conversation内容")

    # 选择要评测的QA对（从所有对话中选取）
    indexed_items: List[tuple[int, Dict[str, Any]]] = []
    if isinstance(raw, list):
        for e_idx, entry in enumerate(raw):
            for qa in entry.get("qa", []):
                indexed_items.append((e_idx, qa))
    else:
        for qa in raw.get("qa", []):
            indexed_items.append((0, qa))

    # 这里使用sample_size来限制评测的QA数量
    selected = indexed_items[:sample_size]
    items: List[Dict[str, Any]] = [qa for _, qa in selected]

    print(f"🎯 将评测 {len(items)} 个QA对，数据库中只包含 {len(contents)} 个对话")
    # === 修改结束 ===

    connector = Neo4jConnector()

    # 关键修复：强制重新摄入纯净的对话数据
    print("🔄 强制重新摄入纯净的对话数据...")
    await ingest_contexts_via_full_pipeline(contents, group_id, save_chunk_output=True)

    # 使用异步LLM客户端
    with get_db_context() as db:
        factory = MemoryClientFactory(db)
        llm_client = factory.get_llm_client(SELECTED_LLM_ID)
    # 初始化embedder用于直接调用
    with get_db_context() as db:
        config_service = MemoryConfigService(db)
        cfg_dict = config_service.get_embedder_config(SELECTED_EMBEDDING_ID)
    embedder = OpenAIEmbedderClient(
        model_config=RedBearModelConfig.model_validate(cfg_dict)
    )
    
    # connector initialized above
    latencies_llm: List[float] = []
    latencies_search: List[float] = []
    # 上下文诊断收集
    per_query_context_counts: List[int] = []
    per_query_context_avg_tokens: List[float] = []
    per_query_context_chars: List[int] = []
    per_query_context_tokens_total: List[int] = []
    # 详细样本调试信息
    samples: List[Dict[str, Any]] = []
    # 通用指标
    f1s: List[float] = []
    b1s: List[float] = []
    jss: List[float] = []
    # 参考 LoCoMo 评测的类别专用 F1（multi-hop 使用多答案 F1）
    loc_f1s: List[float] = []
    # Per-category aggregation
    cat_counts: Dict[str, int] = {}
    cat_f1s: Dict[str, List[float]] = {}
    cat_b1s: Dict[str, List[float]] = {}
    cat_jss: Dict[str, List[float]] = {}
    cat_loc_f1s: Dict[str, List[float]] = {}
    try:
        for item in items:
            q = item.get("question", "")
            ref = item.get("answer", "")
            # 确保答案是字符串
            ref_str = str(ref) if ref is not None else ""
            cat = get_category_label(item)

            print(f"\n=== 处理问题: {q} ===")

            # 根据类别调整检索参数
            search_params = get_search_params_by_category(cat)
            adjusted_limit = search_params["limit"]
            max_chars = search_params["max_chars"]

            print(f"🏷️ 类别: {cat}, 检索参数: limit={adjusted_limit}, max_chars={max_chars}")

            # 改进的检索逻辑：使用三路检索（statements, dialogues, entities）
            t0 = time.time()
            contexts_all: List[str] = []
            search_results = None  # 保存完整的检索结果

            try:
                if search_type == "embedding":
                    # 直接调用嵌入检索，包含三路数据
                    search_results = await search_graph_by_embedding(
                        connector=connector,
                        embedder_client=embedder,
                        query_text=q,
                        group_id=group_id,
                        limit=adjusted_limit,
                        include=["chunks", "statements", "entities", "summaries"],  # 修复：使用正确的类型
                    )
                    chunks = search_results.get("chunks", [])
                    statements = search_results.get("statements", [])
                    entities = search_results.get("entities", [])
                    summaries = search_results.get("summaries", [])
                    
                    print(f"✅ 嵌入检索成功: {len(chunks)} chunks, {len(statements)} 条陈述, {len(entities)} 个实体, {len(summaries)} 个摘要")
                    
                    # 构建上下文：优先使用 chunks、statements 和 summaries
                    for c in chunks:
                        content = str(c.get("content", "")).strip()
                        if content:
                            contexts_all.append(content)

                    for s in statements:
                        stmt_text = str(s.get("statement", "")).strip()
                        if stmt_text:
                            contexts_all.append(stmt_text)
                    
                    for sm in summaries:
                        summary_text = str(sm.get("summary", "")).strip()
                        if summary_text:
                            contexts_all.append(summary_text)

                    # 实体摘要：最多加入前3个高分实体，避免噪声
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

                elif search_type == "keyword":
                    # 直接调用关键词检索
                    search_results = await search_graph(
                        connector=connector,
                        q=q,
                        group_id=group_id,
                        limit=adjusted_limit
                    )
                    dialogs = search_results.get("dialogues", [])
                    statements = search_results.get("statements", [])
                    entities = search_results.get("entities", [])
                    print(f"🔤 关键词检索找到 {len(dialogs)} 条对话, {len(statements)} 条陈述, {len(entities)} 个实体")

                    # 构建上下文
                    for d in dialogs:
                        content = str(d.get("content", "")).strip()
                        if content:
                            contexts_all.append(content)
                    for s in statements:
                        stmt_text = str(s.get("statement", "")).strip()
                        if stmt_text:
                            contexts_all.append(stmt_text)
                    # 实体处理（关键词检索的实体可能没有分数）
                    if entities:
                        entity_names = [str(e.get("name", "")).strip() for e in entities[:5] if e.get("name")]
                        if entity_names:
                            contexts_all.append(f"EntitySummary: {', '.join(entity_names)}")

                else:  # hybrid
                    # 🎯 关键修复：混合检索使用更严格的回退机制
                    print("🔀 使用混合检索（带回退机制）...")
                    try:
                        search_results = await run_hybrid_search(
                            query_text=q,
                            search_type=search_type,
                            group_id=group_id,
                            limit=adjusted_limit,
                            include=["chunks", "statements", "entities", "summaries"],
                            output_path=None,
                        )
                        
                        # 🎯 关键修复：正确处理混合检索的扁平结构
                        # 新的API返回扁平结构，直接从顶层获取结果
                        if search_results and isinstance(search_results, dict):
                            # 新API返回扁平结构：直接从顶层获取
                            chunks = search_results.get("chunks", [])
                            statements = search_results.get("statements", [])
                            entities = search_results.get("entities", [])
                            summaries = search_results.get("summaries", [])
                            
                            # 检查是否有有效结果
                            if chunks or statements or entities or summaries:
                                print(f"✅ 混合检索成功: {len(chunks)} chunks, {len(statements)} 陈述, {len(entities)} 实体, {len(summaries)} 摘要")
                            else:
                                # 如果顶层没有结果，尝试旧的嵌套结构（向后兼容）
                                reranked = search_results.get("reranked_results", {})
                                if reranked and isinstance(reranked, dict):
                                    chunks = reranked.get("chunks", [])
                                    statements = reranked.get("statements", [])
                                    entities = reranked.get("entities", [])
                                    summaries = reranked.get("summaries", [])
                                    print(f"✅ 混合检索成功（使用旧格式reranked结果）: {len(chunks)} chunks, {len(statements)} 陈述")
                                else:
                                    raise ValueError("混合检索返回空结果")
                        else:
                            raise ValueError("混合检索返回空结果")
                            
                    except Exception as e:
                        print(f"❌ 混合检索失败: {e}，回退到嵌入检索")
                        search_results = await search_graph_by_embedding(
                            connector=connector,
                            embedder_client=embedder,
                            query_text=q,
                            group_id=group_id,
                            limit=adjusted_limit,
                            include=["chunks", "statements", "entities", "summaries"],
                        )
                        chunks = search_results.get("chunks", [])
                        statements = search_results.get("statements", [])
                        entities = search_results.get("entities", [])
                        summaries = search_results.get("summaries", [])
                        print(f"✅ 回退嵌入检索成功: {len(chunks)} chunks, {len(statements)} 陈述")
                    
                    # 🎯 统一处理：构建上下文（所有检索类型共用）
                    for c in chunks:
                        content = str(c.get("content", "")).strip()
                        if content:
                            contexts_all.append(content)
                    
                    for s in statements:
                        stmt_text = str(s.get("statement", "")).strip()
                        if stmt_text:
                            contexts_all.append(stmt_text)
                    
                    for sm in summaries:
                        summary_text = str(sm.get("summary", "")).strip()
                        if summary_text:
                            contexts_all.append(summary_text)
                    
                    # 实体摘要：最多加入前3个高分实体
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
                
                # 关键修复：过滤掉包含当前问题答案的上下文
                filtered_contexts = []
                for context in contexts_all:
                    content = str(context)
                    # 排除包含当前问题标准答案的上下文
                    if ref_str and ref_str.strip() and ref_str.strip() in content:
                        print("🚫 过滤掉包含标准答案的上下文")
                        continue
                    filtered_contexts.append(context)

                print(f"📊 过滤后保留 {len(filtered_contexts)} 个上下文 (原 {len(contexts_all)} 个)")
                contexts_all = filtered_contexts

                # 输出完整的检索结果信息
                print("🔍 检索结果详情:")
                if search_results:
                    output_data = {
                        "statements": [
                            {
                                "statement": s.get("statement", "")[:200] + "..." if len(s.get("statement", "")) > 200 else s.get("statement", ""),
                                "score": s.get("score", 0.0)
                            }
                            for s in (statements[:2] if 'statements' in locals() else [])
                        ],
                        "dialogues": [
                            {
                                "uuid": d.get("uuid", ""),
                                "group_id": d.get("group_id", ""),
                                "content": d.get("content", "")[:200] + "..." if len(d.get("content", "")) > 200 else d.get("content", ""),
                                "score": d.get("score", 0.0)
                            }
                            for d in (dialogs[:2] if 'dialogs' in locals() else [])
                        ],
                        "entities": [
                            {
                                "name": e.get("name", ""),
                                "entity_type": e.get("entity_type", ""),
                                "score": e.get("score", 0.0)
                            }
                            for e in (entities[:2] if 'entities' in locals() else [])
                        ]
                    }
                    print(json.dumps(output_data, ensure_ascii=False, indent=2))
                else:
                    print("   无检索结果")

            except Exception as e:
                print(f"❌ {search_type}检索失败: {e}")
                contexts_all = []
                search_results = None

            t1 = time.time()
            latencies_search.append((t1 - t0) * 1000)

            # 使用智能上下文选择
            context_text = ""
            if contexts_all:
                context_text = smart_context_selection(contexts_all, q, max_chars=max_chars)

                # 如果智能选择后仍然过长，进行最终保护性截断
                if len(context_text) > max_chars:
                    print(f"⚠️ 智能选择后仍然过长 ({len(context_text)}字符)，进行最终截断")
                    context_text = context_text[:max_chars] + "\n\n[最终截断...]"

                # 时间解析
                anchor_date = datetime(2023, 5, 8)  # 使用固定日期确保一致性
                context_text = _resolve_relative_times(context_text, anchor_date)

                context_text = f"Reference date: {anchor_date.date().isoformat()}\n\n" + context_text

                print(f"📝 最终上下文长度: {len(context_text)} 字符")

                # 显示不同上下文的预览
                print("🔍 上下文预览:")
                for j, context in enumerate(contexts_all[:3]):  # 显示前3个上下文
                    preview = context[:150].replace('\n', ' ')
                    print(f"  上下文{j+1}: {preview}...")

            else:
                print("❌ 没有检索到有效上下文")
                context_text = "No relevant context found."

            # 记录上下文诊断信息
            per_query_context_counts.append(len(contexts_all))
            per_query_context_avg_tokens.append(avg_context_tokens([context_text]))
            per_query_context_chars.append(len(context_text))
            per_query_context_tokens_total.append(len(_loc_normalize(context_text).split()))

            # LLM 提示词
            messages = [
                {"role": "system", "content": (
                    "You are a precise QA assistant. Answer following these rules:\n"
                    "1) Extract the EXACT information mentioned in the context\n"
                    "2) For time questions: calculate actual dates from relative times\n"
                    "3) Return ONLY the answer text in simplest form\n"
                    "4) For dates, use format 'DD Month YYYY' (e.g., '7 May 2023')\n"
                    "5) If no clear answer found, respond with 'Unknown'"
                )},
                {"role": "user", "content": f"Question: {q}\n\nContext:\n{context_text}"},
            ]

            t2 = time.time()
            # 使用异步调用
            resp = await llm_client.chat(messages=messages)
            t3 = time.time()
            latencies_llm.append((t3 - t2) * 1000)
            
            # 兼容不同的响应格式
            pred = resp.content.strip() if hasattr(resp, 'content') else (resp["choices"][0]["message"]["content"].strip() if isinstance(resp, dict) else "Unknown")
            
            # 计算指标（确保使用字符串）
            f1_val = common_f1(str(pred), ref_str)
            b1_val = bleu1(str(pred), ref_str)
            j_val = jaccard(str(pred), ref_str)

            f1s.append(f1_val)
            b1s.append(b1_val)
            jss.append(j_val)

            # Accumulate by category
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            cat_f1s.setdefault(cat, []).append(f1_val)
            cat_b1s.setdefault(cat, []).append(b1_val)
            cat_jss.setdefault(cat, []).append(j_val)

            # LoCoMo 专用 F1：multi-hop(1) 使用多答案 F1，其它(2/3/4)使用单答案 F1
            if item.get("category") in [2, 3, 4]:
                loc_val = loc_f1_score(str(pred), ref_str)
            elif item.get("category") in [1]:
                loc_val = loc_multi_f1(str(pred), ref_str)
            else:
                loc_val = loc_f1_score(str(pred), ref_str)
            loc_f1s.append(loc_val)
            cat_loc_f1s.setdefault(cat, []).append(loc_val)

            # 保存完整的检索结果信息
            samples.append({
                "question": q,
                "answer": ref_str,
                "category": cat,
                "prediction": pred,
                "metrics": {
                    "f1": f1_val,
                    "b1": b1_val,
                    "j": j_val,
                    "loc_f1": loc_val
                },
                "retrieval": {
                    "retrieved_documents": len(contexts_all),
                    "context_length": len(context_text),
                    "search_limit": adjusted_limit,
                    "max_chars": max_chars
                },
                "timing": {
                    "search_ms": (t1 - t0) * 1000,
                    "llm_ms": (t3 - t2) * 1000
                }
            })

            print(f"🤖 LLM 回答: {pred}")
            print(f"✅ 正确答案: {ref_str}")
            print(f"📈 当前指标 - F1: {f1_val:.3f}, BLEU-1: {b1_val:.3f}, Jaccard: {j_val:.3f}, LoCoMo F1: {loc_val:.3f}")

        # Compute per-category averages and dispersion (std, iqr)
        def _percentile(sorted_vals: List[float], p: float) -> float:
            if not sorted_vals:
                return 0.0
            if len(sorted_vals) == 1:
                return sorted_vals[0]
            k = (len(sorted_vals) - 1) * p
            f = int(k)
            c = f + 1 if f + 1 < len(sorted_vals) else f
            if f == c:
                return sorted_vals[f]
            return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)

        by_category: Dict[str, Dict[str, float | int]] = {}
        for c in cat_counts:
            f_list = cat_f1s.get(c, [])
            b_list = cat_b1s.get(c, [])
            j_list = cat_jss.get(c, [])
            lf_list = cat_loc_f1s.get(c, [])
            j_sorted = sorted(j_list)
            j_std = statistics.stdev(j_list) if len(j_list) > 1 else 0.0
            j_q75 = _percentile(j_sorted, 0.75)
            j_q25 = _percentile(j_sorted, 0.25)
            by_category[c] = {
                "count": cat_counts[c],
                "f1": (sum(f_list) / max(len(f_list), 1)) if f_list else 0.0,
                "b1": (sum(b_list) / max(len(b_list), 1)) if b_list else 0.0,
                "j": (sum(j_list) / max(len(j_list), 1)) if j_list else 0.0,
                "j_std": j_std,
                "j_iqr": (j_q75 - j_q25) if j_list else 0.0,
                # 参考 LoCoMo 评测的类别专用 F1
                "loc_f1": (sum(lf_list) / max(len(lf_list), 1)) if lf_list else 0.0,
            }

        # 累加命中（cum accuracy by category）：与 evaluation_stats.py 输出形式相仿
        cum_accuracy_by_category = {c: sum(cat_loc_f1s.get(c, [])) for c in cat_counts}

        result = {
            "dataset": "locomo",
            "items": len(items),
            "metrics": {
                "f1": sum(f1s) / max(len(f1s), 1),
                "b1": sum(b1s) / max(len(b1s), 1),
                "j": sum(jss) / max(len(jss), 1),
                # LoCoMo 类别专用 F1 的总体
                "loc_f1": sum(loc_f1s) / max(len(loc_f1s), 1),
            },
            "by_category": by_category,
            "category_counts": cat_counts,
            "cum_accuracy_by_category": cum_accuracy_by_category,
            "context": {
                "avg_tokens": (sum(per_query_context_avg_tokens) / max(len(per_query_context_avg_tokens), 1)) if per_query_context_avg_tokens else 0.0,
                "avg_chars": (sum(per_query_context_chars) / max(len(per_query_context_chars), 1)) if per_query_context_chars else 0.0,
                "count_avg": (sum(per_query_context_counts) / max(len(per_query_context_counts), 1)) if per_query_context_counts else 0.0,
                "avg_memory_tokens": (sum(per_query_context_tokens_total) / max(len(per_query_context_tokens_total), 1)) if per_query_context_tokens_total else 0.0,
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
                "search_type": search_type,
                "llm_id": SELECTED_LLM_ID,
                "retrieval_embedding_id": SELECTED_EMBEDDING_ID,
                "skip_ingest_if_exists": skip_ingest_if_exists,
                "llm_timeout": llm_timeout,
                "llm_max_retries": llm_max_retries,
                "llm_temperature": llm_temperature,
                "llm_max_tokens": llm_max_tokens
            },
            "timestamp": datetime.now().isoformat()
        }
        if output_path:
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                print(f"✅ 结果已保存到: {output_path}")
            except Exception as e:
                print(f"❌ 保存结果失败: {e}")
        return result
    finally:
        await connector.close()


def main():
    parser = argparse.ArgumentParser(description="Run LoCoMo evaluation with Qwen search")
    parser.add_argument("--sample_size", type=int, default=1, help="Number of samples to evaluate")
    parser.add_argument("--group_id", type=str, default=None, help="Group ID for retrieval")
    parser.add_argument("--search_limit", type=int, default=8, help="Search limit per query")
    parser.add_argument("--context_char_budget", type=int, default=12000, help="Max characters for context")
    parser.add_argument("--llm_temperature", type=float, default=0.0, help="LLM temperature")
    parser.add_argument("--llm_max_tokens", type=int, default=32, help="LLM max tokens")
    parser.add_argument("--search_type", type=str, default="embedding", choices=["keyword", "embedding", "hybrid"], help="Search type")
    parser.add_argument("--output_path", type=str, default=None, help="Output path for results")
    parser.add_argument("--skip_ingest_if_exists", action="store_true", help="Skip ingest if group exists")
    parser.add_argument("--llm_timeout", type=float, default=10.0, help="LLM timeout in seconds")
    parser.add_argument("--llm_max_retries", type=int, default=1, help="LLM max retries")
    args = parser.parse_args()

    load_dotenv()

    result = asyncio.run(run_locomo_eval(
        sample_size=args.sample_size,
        group_id=args.group_id,
        search_limit=args.search_limit,
        context_char_budget=args.context_char_budget,
        llm_temperature=args.llm_temperature,
        llm_max_tokens=args.llm_max_tokens,
        search_type=args.search_type,
        output_path=args.output_path,
        skip_ingest_if_exists=args.skip_ingest_if_exists,
        llm_timeout=args.llm_timeout,
        llm_max_retries=args.llm_max_retries
    ))

    print("\n" + "="*50)
    print("📊 最终评测结果:")
    print(f"   样本数量: {result['items']}")
    print(f"   F1: {result['metrics']['f1']:.3f}")
    print(f"   BLEU-1: {result['metrics']['b1']:.3f}")
    print(f"   Jaccard: {result['metrics']['j']:.3f}")
    print(f"   LoCoMo F1: {result['metrics']['loc_f1']:.3f}")
    print(f"   平均上下文长度: {result['context']['avg_chars']:.0f} 字符")
    print(f"   平均检索延迟: {result['latency']['search']['mean']:.1f}ms")
    print(f"   平均LLM延迟: {result['latency']['llm']['mean']:.1f}ms")

    if result['by_category']:
        print("\n📈 按类别细分:")
        for cat, metrics in result['by_category'].items():
            print(f"   {cat}:")
            print(f"     样本数: {metrics['count']}")
            print(f"     F1: {metrics['f1']:.3f}")
            print(f"     LoCoMo F1: {metrics['loc_f1']:.3f}")
            print(f"     Jaccard: {metrics['j']:.3f} (±{metrics['j_std']:.3f}, IQR={metrics['j_iqr']:.3f})")


if __name__ == "__main__":
    main()
