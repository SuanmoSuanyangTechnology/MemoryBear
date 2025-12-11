import math
import re
from typing import List, Dict


def _normalize(text: str) -> List[str]:
    """Lowercase, strip punctuation, and split into tokens."""
    text = text.lower().strip()
    # Python's re doesn't support \p classes; use a simple non-word filter
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [t for t in text.split() if t]
    return tokens


def exact_match(pred: str, ref: str) -> float:
    return float(_normalize(pred) == _normalize(ref))


def jaccard(pred: str, ref: str) -> float:
    p = set(_normalize(pred))
    r = set(_normalize(ref))
    if not p and not r:
        return 1.0
    if not p or not r:
        return 0.0
    return len(p & r) / len(p | r)


def f1_score(pred: str, ref: str) -> float:
    p_tokens = _normalize(pred)
    r_tokens = _normalize(ref)
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
    """Unigram BLEU (BLEU-1) with clipping and brevity penalty."""
    p_tokens = _normalize(pred)
    r_tokens = _normalize(ref)
    if not p_tokens:
        return 0.0
    # Clipped count
    r_counts: Dict[str, int] = {}
    for t in r_tokens:
        r_counts[t] = r_counts.get(t, 0) + 1
    clipped = 0
    p_counts: Dict[str, int] = {}
    for t in p_tokens:
        p_counts[t] = p_counts.get(t, 0) + 1
    for t, c in p_counts.items():
        clipped += min(c, r_counts.get(t, 0))
    precision = clipped / max(len(p_tokens), 1)
    # Brevity penalty
    ref_len = len(r_tokens)
    pred_len = len(p_tokens)
    if pred_len > ref_len or pred_len == 0:
        bp = 1.0
    else:
        bp = math.exp(1 - ref_len / max(pred_len, 1))
    return bp * precision


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    k = (len(vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return vals[int(k)]
    return vals[f] + (k - f) * (vals[c] - vals[f])


def latency_stats(latencies_ms: List[float]) -> Dict[str, float]:
    """Return basic latency stats: mean, p50, p95, iqr (p75-p25)."""
    if not latencies_ms:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "iqr": 0.0}
    p25 = percentile(latencies_ms, 0.25)
    p50 = percentile(latencies_ms, 0.50)
    p75 = percentile(latencies_ms, 0.75)
    p95 = percentile(latencies_ms, 0.95)
    mean = sum(latencies_ms) / max(len(latencies_ms), 1)
    return {"mean": mean, "p50": p50, "p95": p95, "iqr": p75 - p25}


def avg_context_tokens(contexts: List[str]) -> float:
    if not contexts:
        return 0.0
    return sum(len(_normalize(c)) for c in contexts) / len(contexts)
