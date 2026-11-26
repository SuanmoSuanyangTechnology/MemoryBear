"""统一情绪定义（10 个情绪桶）。

叶子模块：只依赖标准库，供写入侧、读取侧、schema、prompt 渲染共同引用，
保证后端所有情绪 code / 中英文名 / 效价 / 旧值映射只有这一份定义。

- 10 类 code 与固定顺序：``EMOTION_CODES``
- 中英文显示名：``EMOTION_NAMES_ZH`` / ``EMOTION_NAMES_EN``
- 效价分区：``POSITIVE_EMOTIONS`` / ``NEGATIVE_EMOTIONS`` / ``NEUTRAL_EMOTIONS``
- 旧 6 类中不在 10 类内的值：``LEGACY_EMOTION_MAP``（fear→anxiety、surprise→neutral，仅读取时映射）
- 关键词词典与匹配：``EMOTION_LEXICON`` / ``match_emotion_keywords``
- Cypher 读取归一化片段：``legacy_case_cypher``

所有容器均不可变（tuple / frozenset / MappingProxyType），可在线程池中安全共享。
"""

import re
from types import MappingProxyType
from typing import Literal, Mapping, Optional

EMOTION_CODES: tuple[str, ...] = (
    "neutral", "joy", "relief", "anxiety", "sadness",
    "anger", "frustration", "loneliness", "confusion", "hope",
)

EMOTION_NAMES_ZH: Mapping[str, str] = MappingProxyType({
    "neutral": "中性",
    "joy": "开心",
    "relief": "缓解",
    "anxiety": "焦虑",
    "sadness": "难过",
    "anger": "生气",
    "frustration": "挫败",
    "loneliness": "孤独",
    "confusion": "困惑",
    "hope": "期待",
})

EMOTION_NAMES_EN: Mapping[str, str] = MappingProxyType({
    "neutral": "Neutral",
    "joy": "Joy",
    "relief": "Relief",
    "anxiety": "Anxiety",
    "sadness": "Sadness",
    "anger": "Anger",
    "frustration": "Frustration",
    "loneliness": "Loneliness",
    "confusion": "Confusion",
    "hope": "Hope",
})

POSITIVE_EMOTIONS: frozenset[str] = frozenset({"joy", "relief", "hope"})
NEGATIVE_EMOTIONS: frozenset[str] = frozenset({"anxiety", "sadness", "anger", "frustration", "loneliness"})
NEUTRAL_EMOTIONS: frozenset[str] = frozenset({"neutral", "confusion"})

# 旧 6 类中不在 10 类内的值（joy/sadness/anger/neutral 本就在 10 类内），只在读取时映射，不回写存量数据
LEGACY_EMOTION_MAP: Mapping[str, str] = MappingProxyType({
    "fear": "anxiety",
    "surprise": "neutral",
})

_CODE_SET: frozenset[str] = frozenset(EMOTION_CODES)

EmotionValence = Literal["positive", "negative", "neutral"]


def normalize_emotion_type(value: object) -> Optional[str]:
    """把任意输入归一化为 10 类 code。

    Args:
        value: 原始情绪值（可能是 10 类、旧 6 类、未知值或非 str）。

    Returns:
        10 类 code；旧值按 ``LEGACY_EMOTION_MAP`` 映射；非 str、空串或未知值返回 None。不抛异常。
    """
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if not key:
        return None
    if key in _CODE_SET:
        return key
    return LEGACY_EMOTION_MAP.get(key)


def normalize_emotion_type_or_raw(value: object) -> object:
    """已知值（10 类或旧值）归一化为 code；None 返回 None；未知值原样返回（供既有兜底入口使用）。"""
    if value is None:
        return None
    code = normalize_emotion_type(value)
    return code if code is not None else value


def emotion_valence(value: object) -> Optional[EmotionValence]:
    """先归一化再返回效价（positive / negative / neutral）；未知值返回 None。"""
    code = normalize_emotion_type(value)
    if code is None:
        return None
    if code in POSITIVE_EMOTIONS:
        return "positive"
    if code in NEGATIVE_EMOTIONS:
        return "negative"
    return "neutral"


def emotion_display_name(value: object, language: str = "zh") -> Optional[str]:
    """先归一化再返回显示名；language == "en" 用英文名，其他用中文名；未知值返回 None。"""
    code = normalize_emotion_type(value)
    if code is None:
        return None
    names = EMOTION_NAMES_EN if language == "en" else EMOTION_NAMES_ZH
    return names[code]


def emotion_storage_aliases(code: str) -> tuple[str, ...]:
    """返回存储中会归一化到 ``code`` 的所有原始值，``code`` 排在首位。

    例：anxiety → ("anxiety", "fear")；neutral → ("neutral", "surprise")；joy → ("joy",)。
    非 10 类的输入原样返回单元素元组（未知值过滤结果为空，保持既有行为）。
    """
    legacy = tuple(old for old, new in LEGACY_EMOTION_MAP.items() if new == code)
    return (code,) + legacy


# ── 关键词词典（每类中英文都非空；可在不改接口的前提下扩充）──
EMOTION_LEXICON: Mapping[str, Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "neutral": MappingProxyType({
        "zh": ("平静", "淡定", "无所谓", "还行", "一般般"),
        "en": ("calm", "okay", "indifferent", "so-so"),
    }),
    "joy": MappingProxyType({
        "zh": ("开心", "高兴", "快乐", "愉快", "兴奋", "喜欢", "满意", "幸福", "太好了", "激动", "哈哈"),
        "en": ("happy", "glad", "joy", "excited", "delighted", "pleased", "love", "awesome", "great"),
    }),
    "relief": MappingProxyType({
        "zh": ("松了一口气", "放心", "安心", "解脱", "踏实", "总算", "终于", "轻松", "释然", "缓解"),
        "en": ("relieved", "relief", "phew", "at ease", "reassured", "finally"),
    }),
    "anxiety": MappingProxyType({
        "zh": ("焦虑", "担心", "紧张", "害怕", "不安", "恐惧", "忐忑", "担忧", "着急", "压力", "慌"),
        "en": ("anxious", "worried", "nervous", "afraid", "scared", "stressed", "stress", "panic", "fear"),
    }),
    "sadness": MappingProxyType({
        "zh": ("难过", "伤心", "悲伤", "失落", "心痛", "痛苦", "不开心", "郁闷", "低落", "遗憾", "想哭", "哭"),
        "en": ("sad", "upset", "unhappy", "heartbroken", "depressed", "crying", "cry"),
    }),
    "anger": MappingProxyType({
        "zh": ("生气", "愤怒", "气死", "恼火", "火大", "讨厌", "受够了", "可恶", "不爽", "发火", "烦死"),
        "en": ("angry", "mad", "furious", "annoyed", "irritated", "pissed", "hate"),
    }),
    "frustration": MappingProxyType({
        "zh": ("挫败", "沮丧", "无奈", "失望", "灰心", "泄气", "白费", "搞不定", "卡住", "崩溃", "失败"),
        "en": ("frustrated", "frustrating", "disappointed", "stuck", "give up", "failed", "hopeless"),
    }),
    "loneliness": MappingProxyType({
        "zh": ("孤独", "寂寞", "孤单", "一个人", "没人陪", "冷清", "想念"),
        "en": ("lonely", "alone", "isolated", "lonesome"),
    }),
    "confusion": MappingProxyType({
        "zh": ("困惑", "迷茫", "不懂", "不明白", "搞不清", "疑惑", "纠结", "懵"),
        "en": ("confused", "confusing", "puzzled", "unsure", "don't understand"),
    }),
    "hope": MappingProxyType({
        "zh": ("期待", "希望", "盼望", "憧憬", "但愿", "期盼", "向往", "愿望"),
        "en": ("hope", "hopeful", "looking forward", "look forward", "wish", "can't wait"),
    }),
})


def _compile_english_word(word: str) -> re.Pattern[str]:
    """英文词/短语按整词、不区分大小写匹配；短语内空白匹配任意空白。"""
    pattern = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(rf"(?<![A-Za-z]){pattern}(?![A-Za-z])", re.IGNORECASE)


# 模块导入时预编译：{code: ((小写词, pattern), ...)}
_EN_PATTERNS: Mapping[str, tuple[tuple[str, re.Pattern[str]], ...]] = MappingProxyType({
    code: tuple((word.lower(), _compile_english_word(word)) for word in entry["en"])
    for code, entry in EMOTION_LEXICON.items()
})


def match_emotion_keywords(text: Optional[str], emotion_type: Optional[str], limit: int = 3) -> list[str]:
    """在原文中匹配给定情绪类别词表里出现的关键词。

    只匹配归一化后那一类的词表（不跨类）。中文按子串查找；英文按整词、不区分大小写匹配，
    返回词表中的小写形式。按长度降序去包含（命中区间落在已选更长词区间内则丢弃，
    如已选「不开心」时丢弃「开心」），再按首次出现位置升序、同位置长度降序排序，小写去重后取前 ``limit`` 个。

    Args:
        text: 原文。
        emotion_type: 情绪类别（10 类或旧值）。
        limit: 最多返回的关键词数量。

    Returns:
        关键词列表；输入非法、无匹配或 ``limit <= 0`` 时返回 ``[]``。不抛异常。
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return []
    if not isinstance(text, str) or not text:
        return []
    code = normalize_emotion_type(emotion_type)
    if code is None:
        return []

    # 候选：(词, 起始位置, 结束位置)
    candidates: list[tuple[str, int, int]] = []
    for word in EMOTION_LEXICON[code]["zh"]:
        pos = text.find(word)
        if pos >= 0:
            candidates.append((word, pos, pos + len(word)))
    for word, pattern in _EN_PATTERNS[code]:
        match = pattern.search(text)
        if match:
            candidates.append((word, match.start(), match.end()))

    # 按命中长度降序去包含
    candidates.sort(key=lambda c: (-(c[2] - c[1]), c[1]))
    selected: list[tuple[str, int, int]] = []
    for word, start, end in candidates:
        if any(s <= start and end <= e for _, s, e in selected):
            continue
        selected.append((word, start, end))

    selected.sort(key=lambda c: (c[1], -(c[2] - c[1])))
    result: list[str] = []
    seen: set[str] = set()
    for word, _, _ in selected:
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
        if len(result) >= limit:
            break
    return result


_CYPHER_EXPR_RE = re.compile(r"^[a-z_]+\.[a-z_]+$")


def legacy_case_cypher(expr: str) -> str:
    """生成把旧值归一化为 10 类的 Cypher CASE 表达式。

    Args:
        expr: 属性表达式，必须形如 ``s.emotion_type``（只允许小写字母和下划线）。

    Returns:
        ``CASE expr WHEN 'fear' THEN 'anxiety' WHEN 'surprise' THEN 'neutral' ELSE expr END``

    Raises:
        ValueError: ``expr`` 不是合法的属性表达式（防止拼接注入，调用参数应全为代码常量）。
    """
    if not isinstance(expr, str) or not _CYPHER_EXPR_RE.match(expr):
        raise ValueError(f"invalid cypher property expression: {expr!r}")
    whens = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in LEGACY_EMOTION_MAP.items())
    return f"CASE {expr} {whens} ELSE {expr} END"


def _check_consistency() -> None:
    """导入时一致性校验（不用 assert，避免 python -O 去掉）。"""
    codes = set(EMOTION_CODES)
    if len(codes) != len(EMOTION_CODES):
        raise RuntimeError("emotion_buckets inconsistent: duplicate EMOTION_CODES")
    if not (codes == set(EMOTION_NAMES_ZH) == set(EMOTION_NAMES_EN) == set(EMOTION_LEXICON)):
        raise RuntimeError("emotion_buckets inconsistent: names/lexicon keys != EMOTION_CODES")
    if (POSITIVE_EMOTIONS & NEGATIVE_EMOTIONS) or (POSITIVE_EMOTIONS & NEUTRAL_EMOTIONS) \
            or (NEGATIVE_EMOTIONS & NEUTRAL_EMOTIONS) \
            or (POSITIVE_EMOTIONS | NEGATIVE_EMOTIONS | NEUTRAL_EMOTIONS) != codes:
        raise RuntimeError("emotion_buckets inconsistent: valence sets must partition EMOTION_CODES")
    if not set(LEGACY_EMOTION_MAP.values()) <= codes or set(LEGACY_EMOTION_MAP) & codes:
        raise RuntimeError("emotion_buckets inconsistent: legacy map must map non-codes into EMOTION_CODES")
    for code, entry in EMOTION_LEXICON.items():
        if not entry.get("zh") or not entry.get("en"):
            raise RuntimeError(f"emotion_buckets inconsistent: lexicon for {code!r} needs zh and en words")


_check_consistency()
