from typing import Any

from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.jinja_render.config import JinjaRenderNodeConfig
from app.core.workflow.template_renderer import TemplateRenderer


class JinjaRenderNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = JinjaRenderNodeConfig(**self.config)

    async def execute(self, state: WorkflowState) -> Any:
        """
        Execute the node: render the Jinja2 template with mapped variables.

        The rendered result is returned in a structure compatible with WorkflowState
        merging, so that downstream nodes can access it via node_outputs.

        Args:
            state (WorkflowState): Current workflow state containing variables,
                node outputs, and runtime variables.

        Returns:
            dict[str, Any]: Node output dictionary containing the rendered result
                under `node_outputs[self.node_id]["output"]["rendered"]` and a
                status flag "completed".

        Raises:
            RuntimeError: If Jinja2 template rendering fails due to invalid template
                syntax or missing variables.
        """
        render = TemplateRenderer(strict=False)

        context = {}
        for variable in self.typed_config.mapping:
            context[variable.name] = self._render_template(variable.value, state)

        try:
            res = render.env.from_string(self.typed_config.template).render(**context)
        except Exception as e:
            raise RuntimeError(f"JinjaRender Node {self.node_name} render failed: {e}") from e

        return res
