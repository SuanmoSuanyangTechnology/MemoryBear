import logging
import re
from typing import Any

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.enums import ComparisonOperator, LogicOperator
from app.core.workflow.nodes.if_else import IfElseNodeConfig
from app.core.workflow.nodes.operators import ConditionExpressionResolver, CompareOperatorInstance

logger = logging.getLogger(__name__)


class IfElseNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = IfElseNodeConfig(**self.config)

    @staticmethod
    def _evaluate(operator, instance: CompareOperatorInstance) -> Any:
        match operator:
            case ComparisonOperator.EMPTY:
                return instance.empty()
            case ComparisonOperator.NOT_EMPTY:
                return instance.not_empty()
            case ComparisonOperator.CONTAINS:
                return instance.contains()
            case ComparisonOperator.NOT_CONTAINS:
                return instance.not_contains()
            case ComparisonOperator.START_WITH:
                return instance.startswith()
            case ComparisonOperator.END_WITH:
                return instance.endswith()
            case ComparisonOperator.EQ:
                return instance.eq()
            case ComparisonOperator.NE:
                return instance.ne()
            case ComparisonOperator.LT:
                return instance.lt()
            case ComparisonOperator.LE:
                return instance.le()
            case ComparisonOperator.GT:
                return instance.gt()
            case ComparisonOperator.GE:
                return instance.ge()
            case _:
                raise ValueError(f"Invalid condition: {operator}")

    def evaluate_conditional_edge_expressions(self, state) -> list[bool]:
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
            branch_result = []
            for expression in case_branch.expressions:
                pattern = r"\{\{\s*(.*?)\s*\}\}"
                left_string = re.sub(pattern, r"\1", expression.left).strip()
                try:
                    left_value = self.get_variable(left_string, state)
                except KeyError:
                    left_value = None
                evaluator = ConditionExpressionResolver.resolve_by_value(left_value)(
                    self.get_variable_pool(state),
                    expression.left,
                    expression.right,
                    expression.input_type
                )
                branch_result.append(self._evaluate(expression.operator, evaluator))
            if case_branch.logical_operator == LogicOperator.AND:
                conditions.append(all(branch_result))
            else:
                condition_res = any(branch_result)
                conditions.append(condition_res)
                if condition_res:
                    return conditions

        # Default fallback branch
        conditions.append(True)

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
        expressions = self.evaluate_conditional_edge_expressions(state)
        # TODO: 变量类型及文本类型解析
        for i in range(len(expressions)):
            if expressions[i]:
                logger.info(f"Node {self.node_id}: switched to branch CASE {i + 1}")
                return f'CASE{i + 1}'
        return f'CASE{len(expressions)}'
