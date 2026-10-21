"""
Workflow template loader.

Loads predefined workflow templates from the filesystem.

Subset scope: only ``TemplateLoader.load_template`` as required by the
``load_workflow_template`` path (template listing / README are out of scope
for this service). Template files must stay in sync with the host; template
changes must be applied on both sides.
"""

import os
from pathlib import Path

import yaml

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow_templates")


class TemplateLoader:
    """Workflow template loader."""

    def __init__(self, templates_dir: str = TEMPLATE_DIR):
        """Initialize the template loader.

        Args:
            templates_dir: Path to the templates directory
        """
        self.templates_dir = Path(templates_dir)
        if not self.templates_dir.exists():
            raise ValueError(f"模板目录不存在: {templates_dir}")

    def load_template(self, template_id: str) -> dict | None:
        """Load the specified template.

        Args:
            template_id: Template ID (directory name)

        Returns:
            Template config dictionary, or None if the template does not exist
        """
        template_dir = self.templates_dir / template_id
        template_file = template_dir / "template.yml"

        if not template_file.exists():
            return None

        try:
            with open(template_file, encoding='utf-8') as f:
                template_data = yaml.safe_load(f)

            # Return the workflow config section
            return {
                "name": template_data.get("name", template_id),
                "description": template_data.get("description", ""),
                "nodes": template_data.get("nodes", []),
                "edges": template_data.get("edges", []),
                "variables": template_data.get("variables", []),
                "execution_config": template_data.get("execution_config", {}),
                "triggers": template_data.get("triggers", [])
            }
        except Exception as e:
            print(f"加载模板 {template_id} 失败: {e}")
            return None


# Global template loader instance
_template_loader: TemplateLoader | None = None


def get_template_loader() -> TemplateLoader:
    """Get the global template loader instance.

    Returns:
        TemplateLoader instance
    """
    global _template_loader
    if _template_loader is None:
        _template_loader = TemplateLoader()
    return _template_loader


def load_workflow_template(template_id: str) -> dict | None:
    """Load a workflow template.

    Args:
        template_id: Template ID

    Returns:
        Template config, or None if it does not exist
    """
    loader = get_template_loader()
    return loader.load_template(template_id)


__all__ = ["TemplateLoader", "get_template_loader", "load_workflow_template"]
