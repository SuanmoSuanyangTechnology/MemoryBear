"""One Jinja2 environment for every prompt template.

Prompts are plain files under :data:`TEMPLATES_DIR`, so adding or editing one
never means editing Python. ``autoescape`` stays off because prompts are text
for the model, not HTML.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

#: Every prompt lives here, next to the loader that reads it.
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_ENVIRONMENT = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=False,
    keep_trailing_newline=False,
)


def render_prompt(template_name: str, **context: object) -> str:
    """Render ``<template_name>.jinja2`` with ``context``.

    A missing template raises :class:`jinja2.TemplateNotFound` instead of
    silently sending an empty prompt, so a renamed or deleted prompt fails at
    the call site.
    """

    return _ENVIRONMENT.get_template(f"{template_name}.jinja2").render(**context)


def template_names() -> tuple[str, ...]:
    """Every template the loader can resolve, without the ``.jinja2`` suffix."""

    return tuple(
        sorted(name.removesuffix(".jinja2") for name in _ENVIRONMENT.list_templates())
    )
