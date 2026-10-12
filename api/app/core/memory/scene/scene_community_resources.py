"""Runtime loader for the frozen SceneCommunity ontology and prompt resources."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


RESOURCE_DIR = Path(__file__).with_name("resources")
PROMPT_DIR = RESOURCE_DIR / "prompts"


@dataclass(frozen=True, slots=True)
class SceneCommunityOntology:
    available_categories: tuple[dict[str, Any], ...]
    boundary_by_code: dict[str, dict[str, Any]]

    def category_boundary(self, category_l1: str) -> dict[str, Any]:
        try:
            return self.boundary_by_code[category_l1]
        except KeyError as exc:
            raise ValueError(f"unknown SceneCommunity category: {category_l1}") from exc


@lru_cache(maxsize=1)
def load_scene_community_ontology() -> SceneCommunityOntology:
    payload = json.loads((RESOURCE_DIR / "l1_ontology_v2.json").read_text(encoding="utf-8"))
    categories = payload.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("SceneCommunity ontology categories must not be empty")

    projected: list[dict[str, Any]] = []
    boundaries: dict[str, dict[str, Any]] = {}
    for raw in categories:
        if not isinstance(raw, dict):
            raise ValueError("SceneCommunity ontology category must be an object")
        code = raw.get("code")
        boundary = raw.get("community_boundary")
        if not isinstance(code, str) or not code.strip() or code in boundaries:
            raise ValueError("SceneCommunity ontology category codes must be unique and nonblank")
        if not isinstance(boundary, dict):
            raise ValueError(f"SceneCommunity category {code} has no community_boundary")
        required_boundary_fields = {
            "purpose",
            "instance_axes",
            "internal_segments",
            "split_signals",
        }
        if set(boundary) != required_boundary_fields:
            raise ValueError(f"SceneCommunity category {code} has an invalid boundary contract")
        boundaries[code] = boundary
        projected.append({
            key: value
            for key, value in raw.items()
            if key != "community_boundary"
        })

    if sum(category["code"] == "other" for category in projected) != 1:
        raise ValueError("SceneCommunity ontology must contain exactly one other category")
    return SceneCommunityOntology(tuple(projected), boundaries)


@lru_cache(maxsize=8)
def load_scene_community_prompt(filename: str) -> str:
    if Path(filename).name != filename or not filename.endswith(".jinja2"):
        raise ValueError("invalid SceneCommunity prompt filename")
    return (PROMPT_DIR / filename).read_text(encoding="utf-8")
