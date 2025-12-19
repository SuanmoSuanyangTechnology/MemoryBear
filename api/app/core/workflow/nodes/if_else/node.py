import logging
from typing import Any

from app.core.workflow.nodes import BaseNode, WorkflowState
from app.core.workflow.nodes.enums import ComparisonOperator
from app.core.workflow.nodes.if_else import IfElseNodeConfig
from app.core.workflow.nodes.if_else.config import ConditionDetail

logger = logging.getLogger(__name__)


class ConditionExpressionBuilder:
    """
    Build a Python boolean expression string based on a comparison operator.

    This class does not evaluate the expression.
    It only generates a valid Python expression string
    that can be evaluated later in a workflow context.
    """

    def __init__(self, left: str, operator: ComparisonOperator, right: str):
        self.left = left
        self.operator = operator
        self.right = right

    def _empty(self):
        return f"{self.left} == ''"

    def _not_empty(self):
        return f"{self.left} != ''"

    def _contains(self):
        return f"{self.right} in {self.left}"

    def _not_contains(self):
        return f"{self.right} not in {self.left}"

    def _startwith(self):
        return f'{self.left}.startswith({self.right})'

    def _endwith(self):
        return f'{self.left}.endswith({self.right})'

    def _eq(self):
        return f"{self.left} == {self.right}"

    def _ne(self):
        return f"{self.left} != {self.right}"

    def _lt(self):
        return f"{self.left} < {self.right}"

    def _le(self):
        return f"{self.left} <= {self.right}"

    def _gt(self):
        return f"{self.left} > {self.right}"

    def _ge(self):
        return f"{self.left} >= {self.right}"

    def build(self):
        match self.operator:
            case ComparisonOperator.EMPTY:
                return self._empty()
            case ComparisonOperator.NOT_EMPTY:
                return self._not_empty()
            case ComparisonOperator.CONTAINS:
                return self._contains()
            case ComparisonOperator.NOT_CONTAINS:
                return self._not_contains()
            case ComparisonOperator.START_WITH:
                return self._startwith()
            case ComparisonOperator.END_WITH:
                return self._endwith()
            case ComparisonOperator.EQ:
                return self._eq()
            case ComparisonOperator.NE:
                return self._ne()
            case ComparisonOperator.LT:
                return self._lt()
            case ComparisonOperator.LE:
                return self._le()
            case ComparisonOperator.GT:
                return self._gt()
            case ComparisonOperator.GE:
                return self._ge()
            case _:
                raise ValueError(f"Invalid condition: {self.operator}")


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
                for condition in case_branch.conditions
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
        """
        expressions = self.build_conditional_edge_expressions()
        for i in range(len(expressions)):
            logger.info(expressions[i])
            if self._evaluate_condition(expressions[i], state):
                return f'CASE{i+1}'
        return f'CASE{len(expressions)}'
