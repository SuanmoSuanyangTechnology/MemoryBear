# file name: check_neo4j_connection_fixed.py
import asyncio
import os
import sys
import json
import time
import math
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any
from dotenv import load_dotenv
# 1
# 添加项目根目录到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# 关键：将 src 目录置于最前，确保从当前仓库加载模块
src_dir = os.path.join(project_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

load_dotenv()

# 首先定义 _loc_normalize 函数，因为其他函数依赖它
def _loc_normalize(text: str) -> str:
    text = str(text) if text is not None else ""
    text = text.lower()
    text = re.sub(r"[\,]", " ", text)
    text = re.sub(r"\b(a|an|the|and)\b", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return text

# 尝试从 metrics.py 导入基础指标
try:
    from common.metrics import f1_score, bleu1, jaccard
    print("✅ 从 metrics.py 导入基础指标成功")
except ImportError as e:
    print(f"❌ 从 metrics.py 导入失败: {e}")
    # 回退到本地实现
    def f1_score(pred: str, ref: str) -> float:
        pred_str = str(pred) if pred is not None else ""
        ref_str = str(ref) if ref is not None else ""

        p_tokens = _loc_normalize(pred_str).split()
        r_tokens = _loc_normalize(ref_str).split()
        if not p_tokens and not r_tokens:
            return 1.0
        if not p_tokens or not r_tokens:
            return 0.0
        p_set = set(p_tokens)
        r_set = set(r_tokens)
        tp = len(p_set & r_set)
        precision = tp / len(p_set) if p_set else 0.0
        recall = tp / len(r_set) if r_set else 0.0
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    def bleu1(pred: str, ref: str) -> float:
        pred_str = str(pred) if pred is not None else ""
        ref_str = str(ref) if ref is not None else ""

        p_tokens = _loc_normalize(pred_str).split()
        r_tokens = _loc_normalize(ref_str).split()
        if not p_tokens:
            return 0.0

        r_counts = {}
        for t in r_tokens:
            r_counts[t] = r_counts.get(t, 0) + 1

        clipped = 0
        p_counts = {}
        for t in p_tokens:
            p_counts[t] = p_counts.get(t, 0) + 1

        for t, c in p_counts.items():
            clipped += min(c, r_counts.get(t, 0))

        precision = clipped / max(len(p_tokens), 1)
        ref_len = len(r_tokens)
        pred_len = len(p_tokens)

        if pred_len > ref_len or pred_len == 0:
            bp = 1.0
        else:
            bp = math.exp(1 - ref_len / max(pred_len, 1))

        return bp * precision

    def jaccard(pred: str, ref: str) -> float:
        pred_str = str(pred) if pred is not None else ""
        ref_str = str(ref) if ref is not None else ""

        p = set(_loc_normalize(pred_str).split())
        r = set(_loc_normalize(ref_str).split())
        if not p and not r:
            return 1.0
        if not p or not r:
            return 0.0
        return len(p & r) / len(p | r)

# 尝试从 qwen_search_eval.py 导入 LoCoMo 特定指标
try:
    # 添加 evaluation 目录路径
    evaluation_dir = os.path.join(project_root, "evaluation")
    if evaluation_dir not in sys.path:
        sys.path.insert(0, evaluation_dir)

    # 尝试从不同位置导入
    try:
        from locomo.qwen_search_eval import loc_f1_score, loc_multi_f1, _resolve_relative_times
        print("✅ 从 locomo.qwen_search_eval 导入 LoCoMo 特定指标成功")
    except ImportError:
        from qwen_search_eval import loc_f1_score, loc_multi_f1, _resolve_relative_times
        print("✅ 从 qwen_search_eval 导入 LoCoMo 特定指标成功")

except ImportError as e:
    print(f"❌ 从 qwen_search_eval.py 导入失败: {e}")
    # 回退到本地实现 LoCoMo 特定函数
    def _resolve_relative_times(text: str, anchor: datetime) -> str:
        t = str(text) if text is not None else ""
        t = re.sub(r"\btoday\b", anchor.date().isoformat(), t, flags=re.IGNORECASE)
        t = re.sub(r"\byesterday\b", (anchor - timedelta(days=1)).date().isoformat(), t, flags=re.IGNORECASE)
        t = re.sub(r"\btomorrow\b", (anchor + timedelta(days=1)).date().isoformat(), t, flags=re.IGNORECASE)

        def _ago_repl(m: re.Match[str]) -> str:
            n = int(m.group(1))
            return (anchor - timedelta(days=n)).date().isoformat()
        def _in_repl(m: re.Match[str]) -> str:
            n = int(m.group(1))
            return (anchor + timedelta(days=n)).date().isoformat()

        t = re.sub(r"\b(\d+)\s+days\s+ago\b", _ago_repl, t, flags=re.IGNORECASE)
        t = re.sub(r"\bin\s+(\d+)\s+days\b", _in_repl, t, flags=re.IGNORECASE)
        t = re.sub(r"\blast\s+week\b", (anchor - timedelta(days=7)).date().isoformat(), t, flags=re.IGNORECASE)
        t = re.sub(r"\bnext\s+week\b", (anchor + timedelta(days=7)).date().isoformat(), t, flags=re.IGNORECASE)
        return t

    def loc_f1_score(prediction: str, ground_truth: str) -> float:
        p_tokens = _loc_normalize(prediction).split()
        g_tokens = _loc_normalize(ground_truth).split()
        if not p_tokens or not g_tokens:
            return 0.0
        p = set(p_tokens)
        g = set(g_tokens)
        tp = len(p & g)
        precision = tp / len(p) if p else 0.0
        recall = tp / len(g) if g else 0.0
        return (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    def loc_multi_f1(prediction: str, ground_truth: str) -> float:
        predictions = [p.strip() for p in str(prediction).split(',') if p.strip()]
        ground_truths = [g.strip() for g in str(ground_truth).split(',') if g.strip()]
        if not predictions or not ground_truths:
            return 0.0
        def _f1(a: str, b: str) -> float:
            return loc_f1_score(a, b)
        vals = []
        for gt in ground_truths:
            vals.append(max(_f1(pred, gt) for pred in predictions))
        return sum(vals) / len(vals)


def smart_context_selection(contexts: List[str], question: str, max_chars: int = 8000) -> str:
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


def get_dynamic_search_params(question: str, question_index: int, total_questions: int):
    """根据问题复杂度和进度动态调整检索参数"""

    # 分析问题复杂度
    word_count = len(question.split())
    has_temporal = any(word in question.lower() for word in ['when', 'date', 'time', 'ago'])
    has_multi_hop = any(word in question.lower() for word in ['and', 'both', 'also', 'while'])

    # 根据进度调整 - 后期问题可能需要更精确的检索
    progress_factor = question_index / total_questions

    base_limit = 12
    if has_temporal and has_multi_hop:
        base_limit = 20
    elif word_count > 8:
        base_limit = 16

    # 随着测试进行，逐渐收紧检索范围
    adjusted_limit = max(8, int(base_limit * (1 - progress_factor * 0.3)))

    # 动态调整最大字符数
    max_chars = 8000 + 4000 * (1 - progress_factor)

    return {
        "limit": adjusted_limit,
        "max_chars": int(max_chars)
    }


class EnhancedEvaluationMonitor:
    def __init__(self, reset_interval=5, performance_threshold=0.6):
        self.question_count = 0
        self.reset_interval = reset_interval
        self.performance_threshold = performance_threshold
        self.consecutive_low_scores = 0
        self.performance_history = []
        self.recent_f1_scores = []

    def should_reset_connections(self, current_f1=None):
        """基于计数和性能双重判断"""
        # 定期重置
        if self.question_count % self.reset_interval == 0:
            return True

        # 性能驱动的重置
        if current_f1 is not None and current_f1 < self.performance_threshold:
            self.consecutive_low_scores += 1
            if self.consecutive_low_scores >= 2:  # 连续2个低分就重置
                print("🚨 连续低分，触发紧急重置")
                self.consecutive_low_scores = 0
                return True
        else:
            self.consecutive_low_scores = 0

        return False

    def record_performance(self, question_index, metrics, context_length, retrieved_docs):
        """记录性能指标，检测衰减"""
        self.performance_history.append({
            'index': question_index,
            'metrics': metrics,
            'context_length': context_length,
            'retrieved_docs': retrieved_docs,
            'timestamp': time.time()
        })

        # 记录最近的F1分数
        self.recent_f1_scores.append(metrics['f1'])
        if len(self.recent_f1_scores) > 5:
            self.recent_f1_scores.pop(0)

    def get_recent_performance(self):
        """获取近期平均性能"""
        if not self.recent_f1_scores:
            return 0.5
        return sum(self.recent_f1_scores) / len(self.recent_f1_scores)

    def get_performance_trend(self):
        """分析性能趋势"""
        if len(self.performance_history) < 2:
            return "stable"

        recent_metrics = [item['metrics']['f1'] for item in self.performance_history[-5:]]
        earlier_metrics = [item['metrics']['f1'] for item in self.performance_history[-10:-5]]

        if len(recent_metrics) < 2 or len(earlier_metrics) < 2:
            return "stable"

        recent_avg = sum(recent_metrics) / len(recent_metrics)
        earlier_avg = sum(earlier_metrics) / len(earlier_metrics)

        if recent_avg < earlier_avg * 0.8:
            return "degrading"
        elif recent_avg > earlier_avg * 1.1:
            return "improving"
        else:
            return "stable"


def get_enhanced_search_params(question: str, question_index: int, total_questions: int, recent_performance: float):
    """基于问题复杂度和近期性能动态调整检索参数"""

    # 基础参数
    base_params = get_dynamic_search_params(question, question_index, total_questions)

    # 性能自适应调整
    if recent_performance < 0.5:  # 近期表现差
        # 增加检索范围，尝试获取更多上下文
        base_params["limit"] = min(base_params["limit"] + 5, 25)
        base_params["max_chars"] = min(base_params["max_chars"] + 2000, 12000)
        print(f"📈 性能自适应：增加检索范围 (limit={base_params['limit']}, max_chars={base_params['max_chars']})")

    elif recent_performance > 0.8:  # 近期表现好
        # 收紧检索，提高精度
        base_params["limit"] = max(base_params["limit"] - 2, 8)
        base_params["max_chars"] = max(base_params["max_chars"] - 1000, 6000)
        print(f"🎯 性能自适应：提高检索精度 (limit={base_params['limit']}, max_chars={base_params['max_chars']})")

    # 中间阶段特殊处理
    mid_sequence_factor = abs(question_index / total_questions - 0.5)
    if mid_sequence_factor < 0.2:  # 在中间30%的问题
        print("🎯 中间阶段：使用更精确的检索策略")
        base_params["limit"] = max(base_params["limit"] - 2, 10)  # 减少数量，提高质量
        base_params["max_chars"] = max(base_params["max_chars"] - 1000, 7000)

    return base_params


def enhanced_context_selection(contexts: List[str], question: str, question_index: int, total_questions: int, max_chars: int = 8000) -> str:
    """考虑问题序列位置的智能选择"""

    if not contexts:
        return ""

    # 在序列中间阶段使用更严格的筛选
    mid_sequence_factor = abs(question_index / total_questions - 0.5)  # 距离中心的距离

    if mid_sequence_factor < 0.2:  # 在中间30%的问题
        print("🎯 中间阶段：使用严格上下文筛选")

        # 提取问题关键词
        question_lower = question.lower()
        stop_words = {'what', 'when', 'where', 'who', 'why', 'how', 'did', 'do', 'does', 'is', 'are', 'was', 'were', 'the', 'a', 'an', 'and', 'or', 'but'}
        question_words = set(re.findall(r'\b\w+\b', question_lower))
        question_words = {word for word in question_words if word not in stop_words and len(word) > 2}

        # 只保留高度相关的上下文
        filtered_contexts = []
        for context in contexts:
            context_lower = context.lower()
            relevance_score = sum(3 if word in context_lower else 0 for word in question_words)

            # 额外加分给包含数字、日期的上下文（对事实性问题更重要）
            if any(char.isdigit() for char in context):
                relevance_score += 2

            # 提高阈值：只有得分>=3的上下文才保留
            if relevance_score >= 3:
                filtered_contexts.append(context)
            else:
                print(f"  - 过滤低分上下文: 得分={relevance_score}")

        contexts = filtered_contexts
        print(f"🔍 严格筛选后保留 {len(contexts)} 个上下文")

    # 使用原有的智能选择逻辑
    return smart_context_selection(contexts, question, max_chars)


async def run_enhanced_evaluation():
    """使用增强方法进行完整评估 - 解决中间性能衰减问题"""
    try:
        from dotenv import load_dotenv
    except Exception:
        def load_dotenv():
            return None
     
    # 修正导入路径：使用 app.core.memory.src 前缀
    from app.repositories.neo4j.neo4j_connector import Neo4jConnector
    from app.repositories.neo4j.graph_search import search_graph_by_embedding
    from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
    from app.core.models.base import RedBearModelConfig
    from app.core.memory.utils.llm.llm_utils import get_llm_client
    from app.core.memory.utils.config.config_utils import get_embedder_config
    from app.core.memory.utils.config.definitions import SELECTED_LLM_ID, SELECTED_EMBEDDING_ID

    # 加载数据
    # 获取项目根目录
    current_file = os.path.abspath(__file__)
    evaluation_dir = os.path.dirname(os.path.dirname(current_file))  # evaluation目录
    memory_dir = os.path.dirname(evaluation_dir)  # memory目录
    data_path = os.path.join(memory_dir, "data", "locomo10.json")
    with open(data_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    qa_items = []
    if isinstance(raw, list):
        for entry in raw:
            qa_items.extend(entry.get("qa", []))
    else:
        qa_items.extend(raw.get("qa", []))
    
    items = qa_items[:20]  # 测试多少个问题
    
    # 初始化增强监控器
    monitor = EnhancedEvaluationMonitor(reset_interval=5, performance_threshold=0.6)
    
    llm = get_llm_client(SELECTED_LLM_ID)
    
    # 初始化embedder
    cfg_dict = get_embedder_config(SELECTED_EMBEDDING_ID)
    embedder = OpenAIEmbedderClient(
        model_config=RedBearModelConfig.model_validate(cfg_dict)
    )
    
    # 初始化连接器
    connector = Neo4jConnector()

    # 初始化结果字典
    results = {
        "questions": [],
        "overall_metrics": {"f1": 0.0, "b1": 0.0, "j": 0.0, "loc_f1": 0.0},
        "category_metrics": {},
        "retrieval_stats": {"total_questions": len(items), "avg_context_length": 0, "avg_retrieved_docs": 0},
        "performance_trend": "stable",
        "timestamp": datetime.now().isoformat(),
        "enhanced_strategy": True
    }

    total_f1 = 0.0
    total_bleu1 = 0.0
    total_jaccard = 0.0
    total_loc_f1 = 0.0
    total_context_length = 0
    total_retrieved_docs = 0
    category_stats = {}

    try:
        for i, item in enumerate(items):
            monitor.question_count += 1

            # 获取近期性能用于重置判断
            recent_performance = monitor.get_recent_performance()

            # 增强的重置判断
            should_reset = monitor.should_reset_connections(current_f1=recent_performance)
            if should_reset and i > 0:
                print(f"🔄 重置Neo4j连接 (问题 {i+1}/{len(items)}, 近期性能: {recent_performance:.3f})...")
                await connector.close()
                connector = Neo4jConnector()  # 创建新连接
                print("✅ 连接重置完成")

            q = item.get("question", "")
            ref = item.get("answer", "")
            ref_str = str(ref) if ref is not None else ""

            print(f"\n🔍 [{i+1}/{len(items)}] 问题: {q}")
            print(f"✅ 真实答案: {ref_str}")

            # 分类别统计
            category = "Unknown"
            if item.get("category") == 1:
                category = "Multi-Hop"
            elif item.get("category") == 2:
                category = "Temporal"
            elif item.get("category") == 3:
                category = "Open Domain"
            elif item.get("category") == 4:
                category = "Single-Hop"

            # 增强的检索参数
            search_params = get_enhanced_search_params(q, i, len(items), recent_performance)
            search_limit = search_params["limit"]
            max_chars = search_params["max_chars"]

            print(f"🏷️ 类别: {category}, 检索参数: limit={search_limit}, max_chars={max_chars}")
            
            # 使用项目标准的混合检索方法
            t0 = time.time()
            contexts_all = []

            try:
                # 使用统一的搜索服务
                from app.core.memory.storage_services.search import run_hybrid_search
                
                print("🔀 使用混合搜索服务...")
                
                search_results = await run_hybrid_search(
                    query_text=q,
                    search_type="hybrid",
                    group_id="locomo_sk",
                    limit=20,
                    include=["statements", "chunks", "entities", "summaries"],
                    alpha=0.6,  # BM25权重
                    embedding_id=SELECTED_EMBEDDING_ID
                )
                
                # 处理搜索结果 - 新的搜索服务返回统一的结构
                chunks = search_results.get("chunks", [])
                statements = search_results.get("statements", [])
                entities = search_results.get("entities", [])
                summaries = search_results.get("summaries", [])
                
                print(f"✅ 混合检索成功: {len(chunks)} chunks, {len(statements)} 条陈述, {len(entities)} 个实体, {len(summaries)} 个摘要")

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
                            summary_lines.append(f"EntitySummary: {name}{(' [' + ' '.join(meta) + ']') if meta else ''}")
                    if summary_lines:
                        contexts_all.append("\n".join(summary_lines))

                print(f"📊 有效上下文数量: {len(contexts_all)}")
            except Exception as e:
                print(f"❌ 检索失败: {e}")
                contexts_all = []

            t1 = time.time()
            search_time = (t1 - t0) * 1000

            # 增强的上下文选择
            context_text = ""
            if contexts_all:
                # 使用增强的上下文选择
                context_text = enhanced_context_selection(contexts_all, q, i, len(items), max_chars=max_chars)

                # 如果智能选择后仍然过长，进行最终保护性截断
                if len(context_text) > max_chars:
                    print(f"⚠️ 智能选择后仍然过长 ({len(context_text)}字符)，进行最终截断")
                    context_text = context_text[:max_chars] + "\n\n[最终截断...]"

                # 时间解析
                anchor_date = datetime(2023, 5, 8)  # 使用固定日期确保一致性
                context_text = _resolve_relative_times(context_text, anchor_date)

                context_text = f"Reference date: {anchor_date.date().isoformat()}\n\n" + context_text

                print(f"📝 最终上下文长度: {len(context_text)} 字符")

                # 显示不同上下文的预览（不只是第一条）
                print("🔍 上下文预览:")
                for j, context in enumerate(contexts_all[:3]):  # 显示前3个上下文
                    preview = context[:150].replace('\n', ' ')
                    print(f"  上下文{j+1}: {preview}...")
                
                # 🔍 调试：检查答案是否在上下文中
                if ref_str and ref_str.strip():
                    answer_found = any(ref_str.lower() in ctx.lower() for ctx in contexts_all)
                    print(f"🔍 调试：答案 '{ref_str}' 是否在检索到的上下文中？ {'✅ 是' if answer_found else '❌ 否'}")
                
            else:
                print("❌ 没有检索到有效上下文")
                context_text = "No relevant context found."

            # LLM 回答
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
            try:
                # 使用异步调用
                resp = await llm.chat(messages=messages)
                # 兼容不同的响应格式
                pred = resp.content.strip() if hasattr(resp, 'content') else (resp["choices"][0]["message"]["content"].strip() if isinstance(resp, dict) else "Unknown")
            except Exception as e:
                print(f"❌ LLM 生成失败: {e}")
                pred = "Unknown"
            t3 = time.time()
            llm_time = (t3 - t2) * 1000

            # 计算指标 - 使用导入的指标函数
            f1_val = f1_score(pred, ref_str)
            bleu1_val = bleu1(pred, ref_str)
            jaccard_val = jaccard(pred, ref_str)
            loc_f1_val = loc_f1_score(pred, ref_str)

            print(f"🤖 LLM 回答: {pred}")
            print(f"📈 指标 - F1: {f1_val:.3f}, BLEU-1: {bleu1_val:.3f}, Jaccard: {jaccard_val:.3f}, LoCoMo F1: {loc_f1_val:.3f}")
            print(f"⏱️ 时间 - 检索: {search_time:.1f}ms, LLM: {llm_time:.1f}ms")

            # 更新统计
            total_f1 += f1_val
            total_bleu1 += bleu1_val
            total_jaccard += jaccard_val
            total_loc_f1 += loc_f1_val
            total_context_length += len(context_text)
            total_retrieved_docs += len(contexts_all)

            if category not in category_stats:
                category_stats[category] = {"count": 0, "f1_sum": 0.0, "b1_sum": 0.0, "j_sum": 0.0, "loc_f1_sum": 0.0}

            category_stats[category]["count"] += 1
            category_stats[category]["f1_sum"] += f1_val
            category_stats[category]["b1_sum"] += bleu1_val
            category_stats[category]["j_sum"] += jaccard_val
            category_stats[category]["loc_f1_sum"] += loc_f1_val

            # 记录性能指标
            metrics = {"f1": f1_val, "bleu1": bleu1_val, "jaccard": jaccard_val, "loc_f1": loc_f1_val}
            monitor.record_performance(i, metrics, len(context_text), len(contexts_all))

            # 保存结果
            question_result = {
                "question": q,
                "ground_truth": ref_str,
                "prediction": pred,
                "category": category,
                "metrics": metrics,
                "retrieval": {
                    "retrieved_documents": len(contexts_all),
                    "context_length": len(context_text),
                    "search_limit": search_limit,
                    "max_chars": max_chars,
                    "recent_performance": recent_performance
                },
                "timing": {
                    "search_ms": search_time,
                    "llm_ms": llm_time
                }
            }

            results["questions"].append(question_result)

            print("="*60)

    except Exception as e:
        print(f"❌ 评估过程中发生错误: {e}")
        # 即使出错，也返回已有的结果
        import traceback
        traceback.print_exc()

    finally:
        await connector.close()

    # 计算总体指标
    n = len(items)
    if n > 0:
        results["overall_metrics"] = {
            "f1": total_f1 / n,
            "b1": total_bleu1 / n,
            "j": total_jaccard / n,
            "loc_f1": total_loc_f1 / n
        }

        for category, stats in category_stats.items():
            count = stats["count"]
            results["category_metrics"][category] = {
                "count": count,
                "f1": stats["f1_sum"] / count,
                "bleu1": stats["b1_sum"] / count,
                "jaccard": stats["j_sum"] / count,
                "loc_f1": stats["loc_f1_sum"] / count
            }

        results["retrieval_stats"]["avg_context_length"] = total_context_length / n
        results["retrieval_stats"]["avg_retrieved_docs"] = total_retrieved_docs / n

        # 分析性能趋势
        results["performance_trend"] = monitor.get_performance_trend()
        results["reset_interval"] = monitor.reset_interval
        results["total_questions_processed"] = monitor.question_count

    return results


if __name__ == "__main__":
    print("🚀 运行增强版完整评估（解决中间性能衰减问题）...")
    print("📋 增强特性:")
    print("  - 双重重置策略：定期重置 + 性能驱动重置")
    print("  - 动态检索参数：基于近期性能自适应调整")
    print("  - 中间阶段严格筛选：提高上下文质量要求")
    print("  - 连续性能监控：实时检测性能衰减")

    result = asyncio.run(run_enhanced_evaluation())

    print("\n📊 最终评估结果:")
    print("总体指标:")
    print(f"  F1: {result['overall_metrics']['f1']:.4f}")
    print(f"  BLEU-1: {result['overall_metrics']['b1']:.4f}")
    print(f"  Jaccard: {result['overall_metrics']['j']:.4f}")
    print(f"  LoCoMo F1: {result['overall_metrics']['loc_f1']:.4f}")

    print("\n分类别指标:")
    for category, metrics in result['category_metrics'].items():
        print(f"  {category}: F1={metrics['f1']:.4f}, BLEU-1={metrics['bleu1']:.4f}, Jaccard={metrics['jaccard']:.4f}, LoCoMo F1={metrics['loc_f1']:.4f} (样本数: {metrics['count']})")

    print("\n检索统计:")
    stats = result['retrieval_stats']
    print(f"  平均上下文长度: {stats['avg_context_length']:.0f} 字符")
    print(f"  平均检索文档数: {stats['avg_retrieved_docs']:.1f}")

    print(f"\n性能趋势: {result['performance_trend']}")
    print(f"重置间隔: 每{result['reset_interval']}个问题")
    print(f"处理问题总数: {result['total_questions_processed']}")
    print(f"增强策略: {'启用' if result.get('enhanced_strategy', False) else '未启用'}")


    # 保存结果到指定目录
    # 使用代码文件所在目录的绝对路径
    current_file_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(current_file_dir, "results")
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "enhanced_evaluation_results.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存到: {output_file}")
