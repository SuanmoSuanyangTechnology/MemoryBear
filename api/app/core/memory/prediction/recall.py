from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .budget import DEFAULT_LIMITS, Limits
from .llm import StructuredLLM
from .schemas import MemoryStatement, QueryTerms

_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z'-]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> set[str]:
    tokens = {token.lower() for token in _LATIN_TOKEN.findall(text or "")}
    for run in _CJK_RUN.findall(text or ""):
        if len(run) == 1:
            continue
        tokens.add(run)
        tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class ScoredMemory:
    statement: MemoryStatement
    score: float
    recency: float
    importance: float
    relevance: float
    graph_relevance: float
    lexical_relevance: float


class MemoryStream:
    def __init__(self, statements: list[MemoryStatement], *, as_of: str | None, top_k: int = 16) -> None:
        self.statements = statements
        self.by_id = {statement.id: statement for statement in statements}
        self.top_k = top_k
        self._as_of = _parse_time(as_of)
        self._tokens = {statement.id: tokenize(statement.text) for statement in statements}
        counts: dict[str, int] = {}
        for statement in statements:
            for entity_id in set(statement.entity_ids):
                counts[entity_id] = counts.get(entity_id, 0) + 1
        peak = max(counts.values(), default=1)
        self._salience = {key: math.log1p(value) / math.log1p(peak) for key, value in counts.items()}
        self._ubiquitous = {key for key, value in counts.items() if statements and value / len(statements) >= 0.4}

    def _recency(self, statement: MemoryStatement) -> float:
        moment = _parse_time(statement.dialog_at)
        if moment is None or self._as_of is None:
            return 0.5
        days = max((self._as_of - moment).total_seconds() / 86400, 0)
        return math.exp(-(math.log(2) / 60) * days)

    def _importance(self, statement: MemoryStatement) -> float:
        emotion = max(0.0, min(1.0, statement.emotion_intensity or 0.0))
        type_weight = {"OPINION": 1.0, "PREDICTION": 1.0, "SUGGESTION": 0.8}.get(statement.stmt_type.upper(), 0.5)
        salience = max((self._salience.get(entity_id, 0.0) for entity_id in statement.entity_ids), default=0.0)
        return min(1.0, 0.4 * emotion + 0.3 * type_weight + 0.3 * salience)

    def recall(self, focus_entity_ids: Iterable[str], query_terms: Iterable[str], top_k: int | None = None) -> list[ScoredMemory]:
        focus = set(focus_entity_ids) - self._ubiquitous
        terms = set().union(*(tokenize(term) for term in query_terms))
        scored: list[ScoredMemory] = []
        for statement in self.statements:
            graph_score = min(1.0, len(focus.intersection(statement.entity_ids)) / len(focus) * 2) if focus else 0.0
            lexical_score = min(1.0, len(terms.intersection(self._tokens[statement.id])) / len(terms) * 3) if terms else 0.0
            relevance = 0.5 * graph_score + 0.5 * lexical_score
            if relevance <= 0:
                continue
            recency = self._recency(statement)
            importance = self._importance(statement)
            scored.append(
                ScoredMemory(
                    statement,
                    0.25 * recency + 0.25 * importance + 0.5 * relevance,
                    recency,
                    importance,
                    relevance,
                    graph_score,
                    lexical_score,
                )
            )
        return sorted(scored, key=lambda item: item.score, reverse=True)[: top_k or self.top_k]

    def term_hits(self, terms: Iterable[str]) -> dict[str, list[MemoryStatement]]:
        result: dict[str, list[MemoryStatement]] = {}
        for term in terms:
            needles = tokenize(term)
            if needles:
                result[term] = [statement for statement in self.statements if needles <= self._tokens[statement.id]]
        return result

    def sample_texts(self, count: int = 12) -> list[str]:
        step = max(1, len(self.statements) // count) if self.statements else 1
        return [statement.text for statement in self.statements[::step]][:count]

    def render(self, memories: list[ScoredMemory], max_chars: int = 240) -> str:
        return "\n".join(
            f"[{item.statement.id}][{(item.statement.dialog_at or '时间未知')[:10]}] {item.statement.text[:max_chars]}"
            for item in memories
        ) or "（无相关记忆）"


_TERMS_SYSTEM = """你为记忆检索生成关键词。输出 core_terms（2-5 个直接指代问题主题的精准词）和 terms（8-15 个同义或相邻概念）。必须使用记忆样本的语言和措辞，不要把 support、people、life 等通用词放进 core_terms。"""


async def derive_query_terms(llm: StructuredLLM, question: str, stream: MemoryStream, limits: Limits = DEFAULT_LIMITS) -> QueryTerms:
    samples = stream.sample_texts()
    if not samples:
        return QueryTerms(terms=[question])
    prompt = f"问题：{question}\n\n记忆样本：\n" + "\n".join(f"- {text[:160]}" for text in samples)
    try:
        result = await llm.structured(_TERMS_SYSTEM, prompt, QueryTerms, temperature=0.2, max_tokens=limits.terms_tokens)
    except Exception:
        return QueryTerms(terms=[question])
    assert isinstance(result, QueryTerms)
    result.core_terms = [term.strip() for term in result.core_terms if term.strip()]
    result.terms = [term.strip() for term in result.terms if term.strip()]
    return result if result.all_terms() else QueryTerms(terms=[question])
