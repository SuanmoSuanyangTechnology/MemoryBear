"""Load and validate the shipped preference ontology."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_ONTOLOGY_PATH = Path(__file__).parent / "prompts" / "ontology_v2.json"


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
