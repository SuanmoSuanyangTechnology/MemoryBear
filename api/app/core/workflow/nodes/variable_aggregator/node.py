import logging
import re
from typing import Any

from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.variable_aggregator.config import VariableAggregatorNodeConfig
from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE
from app.core.workflow.variable_pool import VariablePool

logger = logging.getLogger(__name__)


class VariableAggregatorNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: VariableAggregatorNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        config = VariableAggregatorNodeConfig(**self.config)
        output = {}
        for var_type in config.group_type:
            output[var_type] = config.group_type[var_type]
        return output

    @staticmethod
    def _get_express(variable_string: str) -> Any:
        """
        Extract the variable name from a template string '{{ var }}'.

        Args:
            variable_string: A string containing the variable template.

        Returns:
            The extracted variable name, or the stripped original string if no template is found.
        """
        pattern = r"\{\{\s*(.*?)\s*\}\}"
        expression = re.sub(pattern, r"\1", variable_string).strip()
        return expression

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        """
        Execute the variable aggregation logic.

        Returns:
            - str: In non-group mode, returns the first non-None variable value.
            - dict: In group mode, returns a mapping of group_name -> first non-None variable value.
        """
        self.typed_config = VariableAggregatorNodeConfig(**self.config)
        if not self.typed_config.group:
            # --------------------------
            # Non-group mode
            # --------------------------
            for variable in self.typed_config.group_variables:
                var_express = self._get_express(variable)
                try:
                    value = self.get_variable(var_express, variable_pool)
                except Exception as e:
                    logger.warning(f"Failed to get variable '{var_express}': {e}")
                    continue

                if value is not None:
                    logger.info(f"Node: {self.node_id} variable aggregation result: {value}")
                    return value

            logger.info("No variable found in non-group mode; returning empty string.")
            return DEFAULT_VALUE(self.typed_config.group_type["output"])

        # --------------------------
        # Group mode
        # --------------------------
        result = {}
        for group_name, variables in self.typed_config.group_variables.items():
            for variable in variables:
                var_express = self._get_express(variable)
                try:
                    value = self.get_variable(var_express, variable_pool)
                except Exception as e:
                    logger.warning(f"Failed to get variable '{var_express}' in group '{group_name}': {e}")
                    continue

                if value is not None:
                    result[group_name] = value
                    break
            else:
                result[group_name] = DEFAULT_VALUE(self.typed_config.group_type[group_name])
                logger.info(f"No variable found for group '{group_name}'; set empty string.")
        logger.info(f"Node: {self.node_id} variable aggregation result: {result}")
        return result
