"""
Template Renderer

Provides safe template rendering using Jinja2, supporting variable references
and expressions.
"""

import logging
import re
from typing import Any

from jinja2 import TemplateSyntaxError, UndefinedError, Environment, StrictUndefined, Undefined

from app.core.workflow.engine.variable_pool import LazyVariableDict

logger = logging.getLogger(__name__)

_NORMALIZE_PATTERN = re.compile(r"\{\{\s*(\d+)\.(\w+)\s*}}")


class SafeUndefined(Undefined):
    """Return empty string instead of raising error when accessing undefined variables"""
    __slots__ = ()

    def _fail_with_undefined_error(self, *args, **kwargs):
        return ""

    __add__ = __radd__ = __mul__ = __rmul__ = __div__ = __rdiv__ = __truediv__ = __rtruediv__ = _fail_with_undefined_error
    __getitem__ = __getattr__ = _fail_with_undefined_error
    __str__ = __repr__ = lambda self: ""


class TemplateRenderer:
    def __init__(self, strict: bool = True):
        """Initialize renderer

        Args:
            strict: Whether to enable strict mode (raise error on undefined variables)
        """
        self.strict = strict
        self.env = Environment(
            undefined=StrictUndefined if strict else SafeUndefined,
            autoescape=False  # Disable auto-escaping since we handle plain text instead of HTML
        )

    @staticmethod
    def normalize_template(template: str) -> str:
        """Normalize template syntax (convert numeric node reference to dict access)"""
        return _NORMALIZE_PATTERN.sub(
            r'{{ node["\1"].\2 }}',
            template
        )

    def render(
            self,
            template: str,
            conv_vars: dict[str, Any] | LazyVariableDict,
            node_outputs: dict[str, Any] | dict[str, LazyVariableDict],
            system_vars: dict[str, Any] | LazyVariableDict | None = None
    ) -> str:
        """Render template

        Args:
            template: Template string
            conv_vars: Conversation variables
            node_outputs: Node outputs
            system_vars: System variables

        Returns:
            Rendered string

        Raises:
            ValueError: If template syntax is invalid or variables are undefined

        Examples:
            >>> renderer = TemplateRenderer()
            >>> renderer.render(
            ...     "Hello {{var.name}}!",
            ...     {"name": "World"},
            ...     {},
            ...     {}
            ... )
            'Hello World!'

            >>> renderer.render(
            ...     "Analysis result: {{node.analyze.output}}",
            ...     {},
            ...     {"analyze": {"output": "positive sentiment"}},
            ...     {}
            ... )
            'Analysis result: positive sentiment'
        """
        # Build namespace context
        context = {
            "conv": conv_vars,  # Conversation variables: {{conv.user_name}}
            "node": node_outputs,  # Node outputs: {{node.node_1.output}}
            "sys": system_vars,  # System variables: {{sys.execution_id}}
        }

        # Allow direct access to node outputs by node ID: {{llm_qa.output}}
        if node_outputs:
            context.update(node_outputs)

        # # 支持直接访问会话变量（不需要 conv. 前缀）：{{user_name}}
        # if conv_vars:
        #     context.update(conv_vars)
        #
        # context["nodes"] = node_outputs or {}  # 旧语法兼容
        template = self.normalize_template(template)
        try:
            tmpl = self.env.from_string(template)
            return tmpl.render(**context)

        except TemplateSyntaxError as e:
            logger.error(f"Template syntax error: {template}, error: {e}")
            raise ValueError(f"Template syntax error: {e}")
        except UndefinedError as e:
            logger.error(f"Undefined variable in template: {template}, error: {e}")
            raise ValueError(f"Undefined variable: {e}")
        except Exception as e:
            logger.error(f"Template rendering error: {template}, error: {e}")
            raise ValueError(f"Template rendering failed: {e}")

    def validate(self, template: str) -> list[str]:
        """Validate template syntax

        Args:
            template: Template string

        Returns:
            List of errors (empty if valid)

        Examples:
            >>> renderer = TemplateRenderer()
            >>> renderer.validate("Hello {{var.name}}!")
            []

            >>> renderer.validate("Hello {{var.name")  # Missing closing tag
            ['Template syntax error: ...']
        """
        errors = []

        try:
            self.env.from_string(template)
        except TemplateSyntaxError as e:
            errors.append(f"Template syntax error: {e}")
        except Exception as e:
            errors.append(f"Template validation failed: {e}")

        return errors


# Global renderer instances (strict / lenient)
_strict_renderer = TemplateRenderer(strict=True)
_lenient_renderer = TemplateRenderer(strict=False)


def render_template(
        template: str,
        conv_vars: dict[str, Any] | LazyVariableDict,
        node_outputs: dict[str, Any] | dict[str, LazyVariableDict],
        system_vars: dict[str, Any] | LazyVariableDict,
        strict: bool = True
) -> str:
    """Render template (convenience function)

    Args:
        strict: Whether to use strict mode
        template: Template string
        conv_vars: Conversation variables
        node_outputs: Node outputs
        system_vars: System variables

    Returns:
        Rendered string

    Examples:
        >>> render_template(
        ...     "Analyze: {{var.text}}",
        ...     {"text": "This is a text"},
        ...     {},
        ...     {}
        ... )
        'Analyze: This is a text'
    """
    renderer = _strict_renderer if strict else _lenient_renderer
    return renderer.render(template, conv_vars, node_outputs, system_vars)


def validate_template(template: str) -> list[str]:
    """Validate template syntax (convenience function)

    Args:
        template: Template string

    Returns:
        List of errors
    """
    return _strict_renderer.validate(template)
