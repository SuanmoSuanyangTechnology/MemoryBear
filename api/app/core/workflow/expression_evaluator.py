import logging
import re
from typing import Any

from simpleeval import simple_eval, NameNotDefined, InvalidExpression

logger = logging.getLogger(__name__)


class ExpressionEvaluator:
    """Safe expression evaluator for workflow variables and node outputs."""
    
    # Reserved namespaces
    RESERVED_NAMESPACES = {"var", "node", "sys", "nodes"}
    
    @staticmethod
    def evaluate(
        expression: str,
        conv_vars: dict[str, Any],
        node_outputs: dict[str, Any],
        system_vars: dict[str, Any] | None = None
    ) -> Any:
        """
        Safely evaluate an expression using workflow variables.

        Args:
            expression (str): The expression string, e.g., "var.score > 0.8"
            conv_vars (dict): Conversation-level variables
            node_outputs (dict): Outputs from workflow nodes
            system_vars (dict, optional): System variables

        Returns:
            Any: Result of the evaluated expression

        Raises:
            ValueError: If the expression is invalid or evaluation fails
        """
        # Remove Jinja2-style brackets if present
        expression = expression.strip()
        pattern = r"\{\{\s*(.*?)\s*\}\}"
        expression = re.sub(pattern, r"\1", expression).strip()

        # Build context for evaluation
        context = {
            "conv": conv_vars,                   # conversation variables
            "node": node_outputs,                # node outputs
            "sys": system_vars or {},            # system variables
        }

        context.update(conv_vars)
        context["nodes"] = node_outputs
        context.update(node_outputs)
        
        try:
            # simpleeval supports safe operations:
            # arithmetic, comparisons, logical ops, attribute/dict/list access
            result = simple_eval(expression, names=context)
            return result
            
        except NameNotDefined as e:
            logger.error(f"Undefined variable in expression: {expression}, error: {e}")
            raise ValueError(f"Undefined variable: {e}")
            
        except InvalidExpression as e:
            logger.error(f"Invalid expression syntax: {expression}, error: {e}")
            raise ValueError(f"Invalid expression syntax: {e}")
            
        except SyntaxError as e:
            logger.error(f"Syntax error in expression: {expression}, error: {e}")
            raise ValueError(f"Syntax error: {e}")
            
        except Exception as e:
            logger.error(f"Expression evaluation failed: {expression}, error: {e}")
            raise ValueError(f"Expression evaluation failed: {e}")
    
    @staticmethod
    def evaluate_bool(
        expression: str,
        conv_var: dict[str, Any],
        node_outputs: dict[str, Any],
        system_vars: dict[str, Any] | None = None
    ) -> bool:
        """
        Evaluate a boolean expression (for conditions).

        Args:
            expression (str): Boolean expression
            conv_var (dict): Conversation variables
            node_outputs (dict): Node outputs
            system_vars (dict, optional): System variables

        Returns:
            bool: Boolean result
        """
        result = ExpressionEvaluator.evaluate(
            expression, conv_var, node_outputs, system_vars
        )
        return bool(result)
    
    @staticmethod
    def validate_variable_names(variables: list[dict]) -> list[str]:
        """
        Validate variable names for legality.

        Args:
            variables (list[dict]): List of variable definitions

        Returns:
            list[str]: List of error messages. Empty if all names are valid.
        """
        errors = []
        
        for var in variables:
            var_name = var.get("name", "")

            if var_name in ExpressionEvaluator.RESERVED_NAMESPACES:
                errors.append(
                    f"Variable name '{var_name}' is a reserved namespace, please use another name"
                )

            if not var_name.isidentifier():
                errors.append(
                    f"Variable name '{var_name}' is not a valid Python identifier"
                )
        
        return errors


# 便捷函数
def evaluate_expression(
    expression: str,
    conv_var: dict[str, Any],
    node_outputs: dict[str, Any],
    system_vars: dict[str, Any]
) -> Any:
    """Evaluate an expression (convenience function)."""
    return ExpressionEvaluator.evaluate(
        expression, conv_var, node_outputs, system_vars
    )


def evaluate_condition(
    expression: str,
    conv_var: dict[str, Any],
    node_outputs: dict[str, Any],
    system_vars: dict[str, Any] | None = None
) -> bool:
    """Evaluate a boolean condition expression (convenience function)."""
    return ExpressionEvaluator.evaluate_bool(
        expression, conv_var, node_outputs, system_vars
    )
