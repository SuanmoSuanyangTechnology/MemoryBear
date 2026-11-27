"""统一定义 Wiki 候选的本体类型及页面分类。"""

import re
from enum import StrEnum

ONTOLOGY_VERSION = "ontology_general_v1:v1"
TYPE_EVIDENCE_CONTEXT_CHARS = 180
_EVIDENCE_PUNCTUATION = str.maketrans("（），：", "(),:")
_EVIDENCE_MARKDOWN = re.compile(
    r"(?<![a-zA-Z0-9*\\])(?P<bold>\*\*|\\\*\\\*)"
    r"(?=\S)(?P<label>[^*\r\n]+?)(?<=\S)(?P=bold)(?![a-zA-Z0-9*\\])"
    r"|(?<=[:：])[ \t]*\r?\n[ \t]*-[ \t]+(?=\S)"
)


class OntologyType(StrEnum):
    PERSON = "ot_person"
    ORGANIZATION = "ot_organization"
    LOCATION = "ot_location"
    EVENT = "ot_event"
    OBJECT = "ot_object"
    MEASUREMENT = "ot_measurement"
    NORM = "ot_norm"
    PROCEDURE = "ot_procedure"
    EXPLANATION = "ot_explanation"


TYPES = {
    OntologyType.PERSON: ("entity", "具有独立身份的具体自然人。职业、角色或人群归入定义与原理。"),
    OntologyType.ORGANIZATION: (
        "entity",
        "具有独立身份或职责边界的企业、团队或机构。产品归入对象，组织模式归入定义与原理。",
    ),
    OntologyType.LOCATION: (
        "entity", "具有可识别空间范围的具体地理位置、行政区域或场所。软件空间归入对象。",
    ),
    OntologyType.EVENT: (
        "entity", "有时间或过程边界的具体活动、项目、会议、事故或发布。例行步骤归入方法与流程。",
    ),
    OntologyType.OBJECT: (
        "entity",
        "其余可独立识别的具体对象，如产品、系统、品牌、设备或数据集。优先使用上述具体类型。",
    ),
    OntologyType.MEASUREMENT: (
        "concept", "稳定描述如何衡量、计算、评分或比较的指标、公式、量表和评价口径。",
    ),
    OntologyType.NORM: (
        "concept", "规定适用条件、权限、义务、限制或判断标准的知识。",
    ),
    OntologyType.PROCEDURE: (
        "concept", "为完成某个目标而执行的操作方法、步骤、流程或实践。",
    ),
    OntologyType.EXPLANATION: (
        "concept", "解释术语、分类、角色、模型、理论或机制的定义与原理。优先使用上述具体类型。",
    ),
}

TYPE_RULES = "\n".join(f"- {code}: {definition}" for code, (_, definition) in TYPES.items())


def page_type(semantic_type: str) -> str:
    return TYPES[semantic_type][0]


def candidate_identity(candidate: dict) -> str:
    return candidate.get("semantic_type") or candidate["kind"]


def _evidence_text(text: str) -> tuple[str, list[int]]:
    """去掉分类引文中的加粗和冒号后的列表排版，记录每个字符的原文位置。"""
    parts = []
    offsets = []
    start = 0
    for match in _EVIDENCE_MARKDOWN.finditer(text):
        parts.append(text[start:match.start()])
        offsets.extend(range(start, match.start()))
        if match["label"] is not None:
            parts.append(match["label"])
            offsets.extend(range(*match.span("label")))
        start = match.end()
    parts.append(text[start:])
    offsets.extend(range(start, len(text)))
    return "".join(parts).translate(_EVIDENCE_PUNCTUATION), offsets


def _evidence_span(text: str, quote: str) -> tuple[int, int] | None:
    start = text.find(quote)
    if start >= 0:
        return start, start + len(quote)

    visible_quote, _ = _evidence_text(quote.strip())
    if not any(char.isalnum() for char in visible_quote):
        return None
    visible_text, offsets = _evidence_text(text)
    start = visible_text.find(visible_quote)
    if start < 0:
        return None
    return offsets[start], offsets[start + len(visible_quote) - 1] + 1


def with_verified_type(candidate: dict, source_text: dict[str, str]) -> dict:
    """仅保留候选自己引用且能在原文找到的引文。"""
    refs = set(candidate["source_refs"])
    evidence = []
    for item in candidate["type_evidence"]:
        ref, quote = item["source_ref"], item["quote"]
        if ref not in refs or not quote.strip():
            continue
        text = source_text.get(ref, "")
        span = _evidence_span(text, quote)
        if span is None:
            continue
        start, end = span
        # 分类引文附近可能还有原文明确写出的同名、隶属或纠正关系。
        context = text[max(0, start - TYPE_EVIDENCE_CONTEXT_CHARS):
                       end + TYPE_EVIDENCE_CONTEXT_CHARS]
        evidence.append({
            **item, "quote": text[start:end], "semantic_type": candidate["semantic_type"],
            "context": context,
        })
    if not evidence:
        raise ValueError(f"Candidate {candidate['name']!r} has no valid type evidence")
    kind = page_type(candidate["semantic_type"])
    return {**candidate, "kind": kind, "type_evidence": evidence}
