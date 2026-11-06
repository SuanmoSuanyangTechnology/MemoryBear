"""Adapter modules register themselves on import.

Each resource type owns one module and appends exactly one line here; the
registry itself is never edited per type.
"""

from . import tool  # noqa: F401  (registers ToolAdapter on default_registry)
