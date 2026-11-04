"""Frozen vocabulary shared by the framework and every resource adapter."""

from __future__ import annotations

import re
from enum import StrEnum


class ResourceType(StrEnum):
    APP = "app"
    TOOL = "tool"
    SKILL_PACKAGE = "skill_package"


SUBTYPES: dict[str, frozenset[str]] = {
    ResourceType.APP.value: frozenset({"agent", "multi_agent", "workflow", "pure_workflow"}),
    ResourceType.TOOL.value: frozenset({"mcp"}),
    ResourceType.SKILL_PACKAGE.value: frozenset({"standard", "legacy", "plugin"}),
}

VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")

DEPENDENCY_ORDER = ("plugins", "skills", "tools")


def is_supported_type(resource_type: str) -> bool:
    return resource_type in SUBTYPES


def is_supported_subtype(resource_type: str, subtype: str) -> bool:
    return subtype in SUBTYPES.get(resource_type, frozenset())
