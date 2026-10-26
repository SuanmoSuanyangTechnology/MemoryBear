"""Metadata entry parsing shared by the insight input builder.

Ported from the API helper the profile service also carries: the stored profile
JSON uses the three-part ``l2 | l3 | content`` entry format.
"""

from __future__ import annotations

from typing import Any


def split_metadata_entry(item: Any) -> tuple[str, str, str] | None:
    """Parse ``l2 | l3 | content`` into a triplet; ``None`` when unparsable."""

    if not isinstance(item, str):
        return None
    parts = [part.strip() for part in item.strip().split("|", 2)]
    if len(parts) == 2:
        parts = [parts[0], "", parts[1]]
    if len(parts) != 3 or not parts[2]:
        return None
    return parts[0], parts[1], parts[2]


def flatten_domain_specific_info(domain_specific_info: Any) -> list[tuple[str, str, str]]:
    """Flatten ``domain_specific_info`` into ``(scene, field, content)`` entries."""

    if not isinstance(domain_specific_info, dict):
        return []
    result: list[tuple[str, str, str]] = []
    for scene_key, items in domain_specific_info.items():
        if not isinstance(items, list):
            continue
        for item in items:
            triplet = split_metadata_entry(item)
            if triplet:
                result.append((str(scene_key), triplet[0], triplet[2]))
    return result
