"""子问题 5 · LLM 层：未识别实体消解 + 三元组提取"""
import json
import logging
import os
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_prompt_dir = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "utils", "prompt", "prompts"
)
_prompt_env = Environment(loader=FileSystemLoader(_prompt_dir))

# 合法的 type_id 和 predicate_id 范围
VALID_TYPE_IDS = set(range(1, 14))       # 1-13
VALID_PREDICATE_IDS = set(range(1, 14))  # 1-13


class EntityOutput(BaseModel):
    name: str
    type: str
    type_id: int
    description: str = ""
    type_description: str = ""
    is_explicit_memory: bool = False
    entity_idx: int = 0


class TripletOutput(BaseModel):
    subject_name: str
    predicate: str
    predicate_id: int
    predicate_surface: str = ""
    predicate_description: str = ""
    object_name: str
    valid_at: str = "NULL"
    invalid_at: str = "NULL"


class UnresolvedResult(BaseModel):
    resolved: bool = False
    resolution_note: str = ""
    entities: List[EntityOutput] = []
    triplets: List[TripletOutput] = []


@dataclass
class ValidatedResult:
    """校验后的结果"""
    valid: bool
    reason: str
    entities: List[EntityOutput]
    triplets: List[TripletOutput]
    resolved: bool
    resolution_note: str


def validate_unresolved_output(result: UnresolvedResult) -> ValidatedResult:
    """校验 LLM 输出，过滤无效实体和 triplet"""
    if not result.entities:
        return ValidatedResult(
            valid=False, reason="entities_empty",
            entities=[], triplets=[],
            resolved=result.resolved, resolution_note=result.resolution_note,
        )

    # 过滤无效实体
    valid_entities = []
    valid_entity_names = set()
    for entity in result.entities:
        if not entity.name or not entity.name.strip():
            continue
        if entity.type_id not in VALID_TYPE_IDS:
            logger.warning(f"过滤非法 type_id={entity.type_id} entity={entity.name}")
            continue
        valid_entities.append(entity)
        valid_entity_names.add(entity.name)

    if not valid_entities:
        return ValidatedResult(
            valid=False, reason="all_entities_filtered",
            entities=[], triplets=[],
            resolved=result.resolved, resolution_note=result.resolution_note,
        )

    # 过滤无效 triplet
    valid_triplets = []
    for triplet in result.triplets:
        if triplet.predicate_id not in VALID_PREDICATE_IDS:
            logger.warning(f"过滤非法 predicate_id={triplet.predicate_id}")
            continue
        if triplet.subject_name not in valid_entity_names:
            logger.warning(f"过滤 triplet: subject '{triplet.subject_name}' 不在 entities 中")
            continue
        if triplet.object_name not in valid_entity_names:
            logger.warning(f"过滤 triplet: object '{triplet.object_name}' 不在 entities 中")
            continue
        valid_triplets.append(triplet)

    return ValidatedResult(
        valid=True, reason="ok",
        entities=valid_entities, triplets=valid_triplets,
        resolved=result.resolved, resolution_note=result.resolution_note,
    )


async def resolve_unresolved_statement(
    llm_client,
    statement: Dict[str, Any],
    context_chunks: List[str],
    language: str = "zh",
) -> Optional[UnresolvedResult]:
    """调用 LLM 对 unresolved statement 进行消解 + 三元组提取"""
    try:
        from app.core.memory.storage_services.extraction_engine.steps.base import call_structured

        template = _prompt_env.get_template("resolve_unresolved_triplet.jinja2")

        input_json = {
            "statement_id": statement["statement_id"],
            "statement_text": statement["statement_text"],
            "statement_type": statement.get("stmt_type", "FACT"),
            "temporal_type": statement.get("temporal_info", "DYNAMIC"),
            "supporting_context": context_chunks,
            "speaker": statement.get("speaker", "user"),
            "dialog_at": str(statement.get("dialog_at", "")),
            "valid_at": str(statement.get("valid_at", "NULL")),
            "invalid_at": str(statement.get("invalid_at", "NULL")),
            "has_unsolved_reference": True,
        }

        rendered_prompt = template.render(
            statement_text=statement["statement_text"],
            speaker=statement.get("speaker", "user"),
            input_json=json.dumps(input_json, ensure_ascii=False),
            language=language,
        )

        messages = [{"role": "user", "content": rendered_prompt}]
        response = await call_structured(llm_client, messages, UnresolvedResult)

        if isinstance(response, UnresolvedResult):
            return response
        elif isinstance(response, dict):
            return UnresolvedResult(
                resolved=response.get("resolved", False),
                resolution_note=response.get("resolution_note", ""),
                entities=[EntityOutput(**e) for e in response.get("entities", [])],
                triplets=[TripletOutput(**t) for t in response.get("triplets", [])],
            )
        elif isinstance(response, BaseModel):
            data = response.model_dump()
            return UnresolvedResult(
                resolved=data.get("resolved", False),
                resolution_note=data.get("resolution_note", ""),
                entities=[EntityOutput(**e) for e in data.get("entities", [])],
                triplets=[TripletOutput(**t) for t in data.get("triplets", [])],
            )
        else:
            return None

    except Exception as e:
        logger.error(
            f"LLM 未识别实体消解失败 statement={statement.get('statement_id', '?')}: {e}",
            exc_info=True,
        )
        return None
