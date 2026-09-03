from __future__ import annotations

import re

import langid


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+")
_URL_RE = re.compile(r"^(?:https?://|www\.)", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ENGLISH_FUNCTION_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "for",
    "i",
    "is",
    "me",
    "my",
    "of",
    "please",
    "that",
    "the",
    "this",
    "to",
    "we",
    "you",
    "your",
}


def detect_memory_language(text: str) -> str:
    """Return ``zh`` or ``en``, preferring Chinese without clear evidence."""
    content = (text or "").strip()
    if not content or _CJK_RE.search(content):
        return "zh"
    if (
        not _LATIN_RE.search(content)
        or _URL_RE.match(content)
        or _EMAIL_RE.fullmatch(content)
    ):
        return "zh"

    latin_words = _LATIN_WORD_RE.findall(content)
    has_english_sentence_evidence = len(latin_words) >= 4 or (
        len(latin_words) >= 3
        and any(word.lower() in _ENGLISH_FUNCTION_WORDS for word in latin_words)
    )
    if not has_english_sentence_evidence:
        return "zh"

    try:
        detected = langid.classify(content)[0]
    except Exception:
        return "zh"
    return detected if detected in ("zh", "en") else "zh"
