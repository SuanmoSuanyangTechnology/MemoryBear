"""Jinja2 prompt resources of the memory-read core.

The host renders its prompts from files under ``api/app/core/memory/prompt/`` and
``api/app/core/memory/agent/utils/prompt/``. This service must not read the API
tree, so the four templates the read path needs are copied verbatim into
``src/core/resources/prompts/`` and shipped with the package (design C9).

Resolution is package-relative — ``Path(__file__)`` — never the process working
directory, so the same code works from a wheel, a container entrypoint or a test
run. Rendering keeps the host's semantics: no auto-escaping and the default
``Environment`` newline handling, so a rendered prompt is byte-identical to the
API host's output for the same context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, Template, TemplateNotFound

#: Suffix the host's ``TemplateService`` expects; callers may pass it or omit it.
PROMPT_SUFFIX = ".jinja2"

#: Package-relative prompt directory (``src/core/resources/prompts``).
PROMPT_ROOT = Path(__file__).resolve().parents[2] / "resources" / "prompts"


class PromptNotFoundError(FileNotFoundError):
    """The requested prompt resource is not part of this package."""

    def __init__(self, name: str, root: Path) -> None:
        self.name = name
        self.root = root
        super().__init__(f"prompt resource {name!r} not found under {root}")


class JinjaPromptRenderer:
    """Render one versioned Jinja2 prompt resource per call.

    Templates are compiled once and cached for the process (the host caches its
    ``Template`` objects the same way), while rendered strings stay per call.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root).resolve() if root is not None else PROMPT_ROOT
        self._environment = Environment(
            loader=FileSystemLoader(str(self._root)),
            autoescape=False,
        )
        self._templates: dict[str, Template] = {}

    @property
    def root(self) -> Path:
        """Directory prompt resources are read from."""

        return self._root

    def render(self, name: str, **context: Any) -> str:
        """Render ``name`` (``problem_split`` or ``problem_split.jinja2``).

        Raises:
            PromptNotFoundError: the resource is missing from the package.
        """

        return self._template(name).render(**context)

    def _template(self, name: str) -> Template:
        key = name if name.endswith(PROMPT_SUFFIX) else f"{name}{PROMPT_SUFFIX}"
        template = self._templates.get(key)
        if template is not None:
            return template
        try:
            template = self._environment.get_template(key)
        except TemplateNotFound as error:
            raise PromptNotFoundError(key, self._root) from error
        self._templates[key] = template
        return template


def build_prompt_renderer(*, root: str | Path | None = None) -> JinjaPromptRenderer:
    """Composition entry point of the :class:`PromptRenderer` port.

    ``root`` exists for packaging tests that render from a fixture directory; the
    composition root calls it without arguments and gets the bundled resources.
    """

    return JinjaPromptRenderer(root)


__all__ = [
    "PROMPT_ROOT",
    "PROMPT_SUFFIX",
    "JinjaPromptRenderer",
    "PromptNotFoundError",
    "build_prompt_renderer",
]