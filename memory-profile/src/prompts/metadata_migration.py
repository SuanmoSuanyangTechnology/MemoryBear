"""Prompt renderer for the one-shot legacy metadata migration.

The template is the API's ``migrate_user_metadata_v1.jinja2`` (same text,
same variables), copied here so the merge can run it without importing the
API package.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path(__file__).with_name("templates")
_ENVIRONMENT = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=False)


def render_migration_prompt(*, entity_id: str, language: str, input_json: str) -> str:
    """Render the migration prompt exactly like the API's ``prompt_env``."""

    return _ENVIRONMENT.get_template("migrate_user_metadata_v1.jinja2").render(
        entity_id=entity_id,
        language=language,
        input_json=input_json,
    )


__all__ = ["render_migration_prompt"]