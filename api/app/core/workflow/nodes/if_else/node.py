import logging
from typing import Any

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.if_else import IfElseNodeConfig
from app.core.workflow.nodes.if_else.config import ConditionDetail
from app.core.workflow.nodes.operators import ConditionExpressionBuilder

logger = logging.getLogger(__name__)


class IfElseNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = IfElseNodeConfig(**self.config)

    @staticmethod
    def _build_condition_expression(
            condition: ConditionDetail,
    ) -> str:
        """
        Build a single boolean condition expression string.

        This method does NOT evaluate the condition.
        It only generates a valid Python boolean expression string
        (e.g. "x > 10", "'a' in name") that can later be used
        in a conditional edge or evaluated by the workflow engine.

        Args:
            condition (ConditionDetail): Definition of a single comparison condition.

        Returns:
            str: A Python boolean expression string.
        """
        return ConditionExpressionBuilder(
            left=condition.left,
            operator=condition.comparison_operator,
            right=condition.right
        ).build()

    def build_conditional_edge_expressions(self) -> list[str]:
        """
        Build conditional edge expressions for the If-Else node.

        This method does NOT evaluate any condition at runtime.
        Instead, it converts each case branch into a Python boolean
        expression string, which will later be attached to LangGraph
        as conditional edges.

        Each returned expression corresponds to one branch and is
        evaluated in order. A fallback 'True' condition is appended
        to ensure a default branch when no previous conditions match.

        Returns:
            list[str]: A list of Python boolean expression strings,
            ordered by branch priority.
        """
        branch_index = 0
        conditions = []

        for case_branch in self.typed_config.cases:
            branch_index += 1

            branch_conditions = [
                self._build_condition_expression(condition)
                for condition in case_branch.expressions
            ]
            if len(branch_conditions) > 1:
                combined_condition = f' {case_branch.logical_operator} '.join(branch_conditions)
            else:
                combined_condition = branch_conditions[0]
            conditions.append(combined_condition)

        # Default fallback branch
        conditions.append("True")

        return conditions

    async def execute(self, state: WorkflowState) -> Any:
        """
        Execute the conditional branching logic of the node.

        Evaluates the node's configured conditional expressions (expressions) in order.
        Once a condition is satisfied, it returns the corresponding CASE identifier.
        If none of the conditions match, it returns the default last CASE.

        Args:
            state (WorkflowState): The current workflow state, containing variables, messages, node outputs, etc.

        Returns:
            str: The matched branch identifier, e.g., 'CASE1', 'CASE2', ..., used for node transitions.
        """
        expressions = self.build_conditional_edge_expressions()
        for i in range(len(expressions)):
            logger.info(expressions[i])
            if self._evaluate_condition(expressions[i], state):
                logger.info(f"Node {self.node_id}: switched to branch CASE {i + 1}")
                return f'CASE{i + 1}'
        return f'CASE{len(expressions)}'
