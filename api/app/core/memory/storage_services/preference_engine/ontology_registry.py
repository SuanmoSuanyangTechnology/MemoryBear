"""Load and validate the shipped preference ontology."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from app.config.preference_type_initializer import ensure_seeded
from app.db import get_db_context
from app.models.ontology_preference import (
    OntologyPreferenceClass,
    OntologyPreferenceScene,
)

_ONTOLOGY_PATH = Path(__file__).parent / "prompts" / "coding_preference_ontology.json"


@dataclass(frozen=True)
class PreferenceOntology:
    domain: str
    subjects: tuple[str, ...]
    situation_keys: tuple[str, ...]
    allowed_modes: tuple[str, ...]
    subjects_json: str
    situation_keys_json: str


@lru_cache(maxsize=4)
def load_preference_ontology(domain: str = "coding") -> PreferenceOntology:
    payload = json.loads(_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    if payload.get("ontology_id") != "preference":
        raise ValueError("Invalid preference ontology_id")
    deterministic_domain = payload.get("deterministic_fields", {}).get("domain")
    if deterministic_domain != domain:
        raise ValueError(f"Ontology deterministic domain is not {domain}")
    domain_data = payload.get("domains", {}).get(domain)
    if not isinstance(domain_data, dict):
        raise ValueError(f"Preference ontology domain not found: {domain}")
    subjects = tuple(item["key"] for item in domain_data.get("subjects", []))
    situations = tuple(item["key"] for item in domain_data.get("situation_keys", []))
    modes = tuple(payload.get("allowed_modes", []))
    if not subjects or not situations or set(modes) != {"positive", "negative", "relative"}:
        raise ValueError("Preference ontology has incomplete closed sets")
    return PreferenceOntology(
        domain=domain,
        subjects=subjects,
        situation_keys=situations,
        allowed_modes=modes,
        subjects_json=json.dumps(domain_data["subjects"], ensure_ascii=False),
        situation_keys_json=json.dumps(domain_data["situation_keys"], ensure_ascii=False),
    )


def load_preference_ontology_from_db(
    workspace_id: str | uuid.UUID, domain: str = "coding"
) -> PreferenceOntology:
    """从 workspace 的偏好类型表加载封闭本体；缺失时先惰性种入。

    workspace_id 统一转成 str 再做缓存键，避免 UUID / str 重复缓存。
    """
    return _load_preference_ontology_from_db_cached(str(workspace_id), domain)


def clear_preference_ontology_cache() -> None:
    """偏好类型数据变更后调用，清空进程内本体缓存。"""
    _load_preference_ontology_from_db_cached.cache_clear()


@lru_cache(maxsize=128)
def _load_preference_ontology_from_db_cached(
    workspace_id: str, domain: str = "coding"
) -> PreferenceOntology:
    """缓存实现：入参 workspace_id 固定为 str。"""
    workspace_uuid = uuid.UUID(workspace_id)
    with get_db_context() as db:
        ensure_seeded(db, workspace_uuid, domain=domain)
        scene = db.execute(
            select(OntologyPreferenceScene).where(
                OntologyPreferenceScene.workspace_id == workspace_uuid,
                OntologyPreferenceScene.domain == domain,
            )
        ).scalars().first()
        if scene is None:
            raise ValueError(
                f"Preference scene not found: workspace={workspace_id} domain={domain}"
            )
        rows = db.execute(
            select(OntologyPreferenceClass)
            .where(
                OntologyPreferenceClass.preference_scene_id == scene.preference_scene_id
            )
            .order_by(
                OntologyPreferenceClass.item_type.asc(),
                OntologyPreferenceClass.sort_order.asc(),
            )
        ).scalars().all()

        # Session 关闭/回滚后 ORM 对象会 detached，先在会话内转成普通 dict
        subjects = [
            {
                "key": row.item_key,
                "description": row.semantic_description,
                "includes": row.semantic_includes,
                "boundary": row.semantic_boundary,
            }
            for row in rows
            if row.item_type == "subject"
        ]
        situations = [
            {
                "key": row.item_key,
                "description": row.semantic_description,
                "boundary": row.semantic_boundary,
            }
            for row in rows
            if row.item_type == "situation"
        ]
        modes = tuple(scene.allowed_modes or [])

    if not subjects or not situations or set(modes) != {"positive", "negative", "relative"}:
        raise ValueError("Preference ontology has incomplete closed sets")

    return PreferenceOntology(
        domain=domain,
        subjects=tuple(item["key"] for item in subjects),
        situation_keys=tuple(item["key"] for item in situations),
        allowed_modes=modes,
        subjects_json=json.dumps(subjects, ensure_ascii=False),
        situation_keys_json=json.dumps(situations, ensure_ascii=False),
    )
