"""
Template Service for loading and rendering Jinja2 templates.

This service provides centralized template management with caching and error handling.
"""

import os
from functools import lru_cache

from jinja2 import (
    Environment,
    FileSystemLoader,
    Template,
    TemplateNotFound,
)

from app.core.logging_config import (
    get_agent_logger,
    log_prompt_rendering,
)



logger = get_agent_logger(__name__)


class TemplateRenderError(Exception):
    """Exception raised when template rendering fails."""
    
    def __init__(self, template_name: str, error: Exception, variables: dict):
        self.template_name = template_name
        self.error = error
        self.variables = variables
        super().__init__(
            f"Failed to render template '{template_name}': {str(error)}"
        )


class TemplateService:
    """Service for loading and rendering Jinja2 templates with caching."""
    
    def __init__(self, template_root: str):
        """
        Initialize the template service.
        
        Args:
            template_root: Root directory containing template files
        """
        self.template_root = template_root
        self.env = Environment(
            loader=FileSystemLoader(template_root),
            autoescape=False  # Disable autoescape for prompt templates
        )
        logger.info(f"TemplateService initialized with root: {template_root}")
    
    @lru_cache(maxsize=128)
    def _load_template(self, template_name: str) -> Template:
        """
        Load a template from disk with caching.
        
        Args:
            template_name: Relative path to template file
            
        Returns:
            Loaded Jinja2 Template object
            
        Raises:
            TemplateNotFound: If template file doesn't exist
        """
        try:
            return self.env.get_template(template_name)
        except TemplateNotFound as e:
            expected_path = os.path.join(self.template_root, template_name)
            logger.error(
                f"Template not found: {template_name}. "
                f"Expected path: {expected_path}"
            )
            raise
    
    async def render_template(
        self,
        template_name: str,
        operation_name: str,
        **variables
    ) -> str:
        """
        Load and render a Jinja2 template.
        
        Args:
            template_name: Relative path to template file
            operation_name: Name for logging (e.g., "split_the_problem")
            **variables: Template variables to render
            
        Returns:
            Rendered template string
            
        Raises:
            TemplateRenderError: If template loading or rendering fails
        """
        try:
            # Load template (cached)
            template = self._load_template(template_name)
            
            # Render template
            rendered = template.render(**variables)
            
            # Log rendered prompt
            log_prompt_rendering(operation_name, rendered)
            
            return rendered
            
        except TemplateNotFound as e:
            logger.error(
                f"Template rendering failed for {operation_name} "
                f"({template_name}): Template not found",
                exc_info=True
            )
            raise TemplateRenderError(template_name, e, variables)
            
        except Exception as e:
            logger.error(
                f"Template rendering failed for {operation_name} "
                f"({template_name}): {e}",
                exc_info=True
            )
            raise TemplateRenderError(template_name, e, variables)
