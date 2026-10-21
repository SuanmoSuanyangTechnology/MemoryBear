"""Prompt-template manager (port of the host ``app/utils/prompt_manager``).

Renders Jinja2 templates from a prompt directory. A missing template raises
``FileNotFoundError`` (the prompt-optimizer service maps it to a not-found
business error) while syntax/render failures raise ``PromptRenderError``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError

logger = logging.getLogger(__name__)


class PromptRenderError(Exception):
    def __init__(self, template_name: str, error: Exception):
        self.template_name = template_name
        self.error = error
        super().__init__(f"Failed to render prompt '{template_name}': {error}")


class PromptManager:
    def __init__(self, prompt_dir: str | Path):
        self.prompt_dir = Path(prompt_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.prompt_dir)),
            autoescape=False,
            keep_trailing_newline=True,
        )
        logger.info("PromptManager initialized: template_dir=%s", self.prompt_dir)

    def __repr__(self):
        templates = self.list_templates()
        return f"<PromptManager({self.prompt_dir.name}): {len(templates)} prompts: {templates}>"

    def list_templates(self) -> list[str]:
        return [
            Path(name).stem
            for name in self.env.loader.list_templates()
            if name.endswith(".jinja2")
        ]

    def get(self, name: str) -> str:
        template_name = self._resolve_name(name)
        try:
            source, _, _ = self.env.loader.get_source(self.env, template_name)
            return source
        except TemplateNotFound:
            raise FileNotFoundError(
                f"Prompt '{name}' not found. Available: {self.list_templates()}"
            ) from None

    def render(self, name: str, **kwargs) -> str:
        template_name = self._resolve_name(name)
        try:
            template = self.env.get_template(template_name)
            return template.render(**kwargs)
        except TemplateNotFound:
            raise FileNotFoundError(
                f"Prompt '{name}' not found. Available: {self.list_templates()}"
            ) from None
        except TemplateSyntaxError as e:
            logger.error("Prompt syntax error in '%s': %s", name, e, exc_info=True)
            raise PromptRenderError(name, e) from e
        except Exception as e:
            logger.error("Prompt render failed for '%s': %s", name, e, exc_info=True)
            raise PromptRenderError(name, e) from e

    @staticmethod
    def _resolve_name(name: str) -> str:
        if not name.endswith(".jinja2"):
            return f"{name}.jinja2"
        return name


__all__ = ["PromptManager", "PromptRenderError"]
