"""统一定义 Wiki 候选的本体类型及页面分类。"""

from enum import StrEnum

ONTOLOGY_VERSION = "ontology_general_v1:v1"


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


def with_verified_type(candidate: dict, source_text: dict[str, str]) -> dict:
    """仅保留候选自己引用且能在原文找到的引文。"""
    refs = set(candidate["source_refs"])
    evidence = [
        {**item, "semantic_type": candidate["semantic_type"]}
        for item in candidate["type_evidence"]
        if item["source_ref"] in refs and item["quote"].strip()
        and item["quote"] in source_text.get(item["source_ref"], "")
    ]
    if not evidence:
        raise ValueError(f"Candidate {candidate['name']!r} has no valid type evidence")
    kind = page_type(candidate["semantic_type"])
    return {**candidate, "kind": kind, "type_evidence": evidence}
