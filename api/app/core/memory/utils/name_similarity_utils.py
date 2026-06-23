"""名称相似度工具函数

从 deduped_and_disamb.py 的 fuzzy_match 内部提取为模块级函数，
供 extraction_engine 和 reflection_engine 共用。
"""
import re
from typing import List, Optional, Tuple

from app.core.memory.models.graph_models import ExtractedEntityNode


def _normalize_text(s: str) -> str:
    """文本标准化：转小写、去除特殊字符、规范化空格"""
    try:
        return re.sub(r"\s+", " ", re.sub(r"[^\w\u4e00-\u9fff]+", " ", (s or "").lower())).strip()
    except Exception:
        return str(s).lower().strip()


def _tokenize(s: str) -> List[str]:
    """分词：提取中文字符和英文数字单词"""
    norm = _normalize_text(s)
    return re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", norm)


def _jaccard(a_tokens: List[str], b_tokens: List[str]) -> float:
    """Jaccard相似度：计算两个token集合的交集/并集"""
    try:
        set_a, set_b = set(a_tokens), set(b_tokens)
        if not set_a and not set_b:
            return 0.0
        inter = len(set_a & set_b)
        union = len(set_a | set_b)
        return inter / union if union > 0 else 0.0
    except Exception:
        return 0.0


def _cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度：计算两个向量的夹角余弦值"""
    try:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


def _simple_normalize(s: str) -> str:
    """简单归一化：strip + lower"""
    return (s or "").strip().lower()


def has_exact_alias_match(e1: ExtractedEntityNode, e2: ExtractedEntityNode) -> bool:
    """检测两个实体之间是否存在完全别名匹配（case-insensitive）

    检查以下情况：
    - e1的主名称与e2的某个别名完全匹配
    - e2的主名称与e1的某个别名完全匹配
    - e1和e2的别名列表有交集
    """
    names1 = set()
    name1 = _simple_normalize(getattr(e1, "name", "") or "")
    if name1:
        names1.add(name1)
    for alias in (getattr(e1, "aliases", []) or []):
        normalized = _simple_normalize(alias)
        if normalized:
            names1.add(normalized)

    names2 = set()
    name2 = _simple_normalize(getattr(e2, "name", "") or "")
    if name2:
        names2.add(name2)
    for alias in (getattr(e2, "aliases", []) or []):
        normalized = _simple_normalize(alias)
        if normalized:
            names2.add(normalized)

    return bool(names1 & names2)


def name_similarity_with_aliases(
    e1: ExtractedEntityNode, e2: ExtractedEntityNode,
    emb_sim: Optional[float] = None,
) -> Tuple[float, float, float, float, float, bool]:
    """名称相似度综合评分

    综合考虑主名称和别名，计算两个实体的相似度。

    算法：
    1. 计算主名称的向量相似度和 Token Jaccard 相似度
    2. 计算所有别名的 Token Jaccard 相似度
    3. 找出所有名称间的最佳匹配
    4. 检测是否存在完全别名匹配

    评分权重：
    - 有完全匹配：embedding(40%) + primary_jaccard(20%) + max_alias_sim(40%)
    - 无完全匹配：embedding(60%) + primary_jaccard(20%) + max_alias_sim(20%)

    Args:
        e1, e2: 实体节点
        emb_sim: 主名称向量相似度。若调用方已在 Neo4j 侧用
            vector.similarity.cosine 算好则直接传入，避免在 Python 侧用
            name_embedding 重新计算（省向量传输与内存）；为 None 时回退到
            Python 内部用 _cosine 计算。

    Returns:
        (综合相似度, 向量相似度, 主名称Jaccard, 别名Jaccard,
         最佳别名匹配度, 是否完全匹配)
    """
    # 1. 主名称向量相似度：优先用外部传入（Neo4j 已算），否则 Python 兜底计算
    if emb_sim is None:
        emb_sim = _cosine(
            getattr(e1, "name_embedding", []) or [],
            getattr(e2, "name_embedding", []) or [],
        )

    # 2. 主名称 token 相似度
    tokens1 = set(_tokenize(getattr(e1, "name", "") or ""))
    tokens2 = set(_tokenize(getattr(e2, "name", "") or ""))
    j_primary = _jaccard(list(tokens1), list(tokens2))

    # 3. 获取所有别名
    aliases1 = getattr(e1, "aliases", []) or []
    aliases2 = getattr(e2, "aliases", []) or []

    # 4. 计算所有别名的 token 集合（用于整体 Jaccard）
    alias_tokens1 = set(tokens1)
    alias_tokens2 = set(tokens2)
    for a in aliases1:
        alias_tokens1 |= set(_tokenize(a))
    for a in aliases2:
        alias_tokens2 |= set(_tokenize(a))
    j_alias = _jaccard(list(alias_tokens1), list(alias_tokens2))

    # 5. 检测完全匹配
    has_exact_match = has_exact_alias_match(e1, e2)

    # 6. 计算最佳别名匹配度（所有名称两两比较）
    all_names1 = [getattr(e1, "name", "") or "", *aliases1]
    all_names2 = [getattr(e2, "name", "") or "", *aliases2]

    max_alias_sim = 0.0
    if has_exact_match:
        max_alias_sim = 1.0
    else:
        for n1 in all_names1:
            if not n1:
                continue
            tokens_n1 = set(_tokenize(n1))
            for n2 in all_names2:
                if not n2:
                    continue
                tokens_n2 = set(_tokenize(n2))
                sim = _jaccard(list(tokens_n1), list(tokens_n2))
                max_alias_sim = max(max_alias_sim, sim)

    # 7. 综合评分
    if has_exact_match:
        s_name = 0.4 * emb_sim + 0.2 * j_primary + 0.4 * max_alias_sim
    else:
        s_name = 0.6 * emb_sim + 0.2 * j_primary + 0.2 * max_alias_sim

    return s_name, emb_sim, j_primary, j_alias, max_alias_sim, has_exact_match
