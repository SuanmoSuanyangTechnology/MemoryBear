# -*- coding: utf-8 -*-
"""偏好类型种子构建

把两份 JSON 合并成偏好域 / 偏好类别的 ORM 对象：

1. ``preference_engine/prompts/coding_preference_ontology.json``：key、顺序、
   ``description`` / ``includes`` / ``boundary``、``allowed_modes``。
2. ``preference_engine/prompts/preference_type_display.json``：中英文展示文案。

顺序与数量以本体 JSON 为准；展示 JSON 多出的 key 忽略，缺失的 key 回退。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Optional

from app.core.utils.datetime_utils import utcnow_naive
from app.models.ontology_preference import (
    OntologyPreferenceClass,
    OntologyPreferenceScene,
)

_PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent
    / "core"
    / "memory"
    / "storage_services"
    / "preference_engine"
    / "prompts"
)
_ONTOLOGY_PATH = _PROMPTS_DIR / "coding_preference_ontology.json"
_DISPLAY_PATH = _PROMPTS_DIR / "preference_type_display.json"

_ALLOWED_MODES = {"positive", "negative", "relative"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _localized(value: Any, language: str) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    text = value.get(language)
    if text is None:
        text = value.get("zh" if language == "en" else "en")
    if text is None:
        return None
    text = str(text).strip()
    return text or None


def _find_display_type(display_payload: dict, domain: str) -> dict:
    for item in display_payload.get("types") or []:
        if item.get("domain") == domain:
            return item
    return {}


def _find_display_item(display_type: dict, section: str, key: str) -> dict:
    for item in display_type.get(section) or []:
        if item.get("key") == key:
            return item
    return {}


def build_seed_rows(
    workspace_id: uuid.UUID,
    domain: str = "coding",
) -> tuple[OntologyPreferenceScene, list[OntologyPreferenceClass]]:
    """读两份 JSON，构建 1 条偏好域 + N 条类别/任务（未落库）。

    本体 JSON 缺少 subjects / situation_keys / allowed_modes 时直接抛错，
    不写半份数据。
    """
    ontology_payload = _load_json(_ONTOLOGY_PATH)
    display_payload = _load_json(_DISPLAY_PATH)

    if ontology_payload.get("ontology_id") != "preference":
        raise ValueError("Invalid preference ontology_id")

    deterministic_domain = (ontology_payload.get("deterministic_fields") or {}).get("domain")
    if deterministic_domain != domain:
        raise ValueError(f"Ontology deterministic domain is not {domain}")

    domain_data = (ontology_payload.get("domains") or {}).get(domain)
    if not isinstance(domain_data, dict):
        raise ValueError(f"Preference ontology domain not found: {domain}")

    subjects = domain_data.get("subjects") or []
    situations = domain_data.get("situation_keys") or []
    modes = list(ontology_payload.get("allowed_modes") or [])
    if not subjects or not situations:
        raise ValueError("Preference ontology has incomplete closed sets")
    if set(modes) != _ALLOWED_MODES:
        raise ValueError("Preference ontology has invalid allowed_modes")

    display_type = _find_display_type(display_payload, domain)
    now = utcnow_naive()
    scene_id = uuid.uuid4()

    display_name_zh = _localized(display_type.get("display_name"), "zh") or domain
    display_name_en = _localized(display_type.get("display_name"), "en") or domain
    scene = OntologyPreferenceScene(
        preference_scene_id=scene_id,
        workspace_id=workspace_id,
        domain=domain,
        display_name_zh=display_name_zh,
        display_name_en=display_name_en,
        note_zh=_localized(display_type.get("note"), "zh"),
        note_en=_localized(display_type.get("note"), "en"),
        allowed_modes=modes,
        is_system_default=True,
        created_at=now,
        updated_at=now,
    )

    classes: list[OntologyPreferenceClass] = []
    for index, item in enumerate(subjects, start=1):
        key = str(item.get("key") or "").strip()
        if not key:
            raise ValueError("Preference subject key is empty")
        display_item = _find_display_item(display_type, "subjects", key)
        classes.append(
            OntologyPreferenceClass(
                preference_class_id=uuid.uuid4(),
                preference_scene_id=scene_id,
                item_type="subject",
                item_key=key,
                label_zh=_localized(display_item.get("label"), "zh") or key,
                label_en=_localized(display_item.get("label"), "en") or key,
                display_description_zh=_localized(display_item.get("description"), "zh"),
                display_description_en=_localized(display_item.get("description"), "en"),
                semantic_description=item.get("description"),
                semantic_includes=item.get("includes"),
                semantic_boundary=item.get("boundary"),
                sort_order=index,
                is_system_default=True,
                created_at=now,
                updated_at=now,
            )
        )

    for index, item in enumerate(situations, start=1):
        key = str(item.get("key") or "").strip()
        if not key:
            raise ValueError("Preference situation key is empty")
        display_item = _find_display_item(display_type, "situations", key)
        classes.append(
            OntologyPreferenceClass(
                preference_class_id=uuid.uuid4(),
                preference_scene_id=scene_id,
                item_type="situation",
                item_key=key,
                label_zh=_localized(display_item.get("label"), "zh") or key,
                label_en=_localized(display_item.get("label"), "en") or key,
                display_description_zh=None,
                display_description_en=None,
                semantic_description=item.get("description"),
                semantic_includes=None,
                semantic_boundary=item.get("boundary"),
                sort_order=index,
                is_system_default=True,
                created_at=now,
                updated_at=now,
            )
        )

    return scene, classes
