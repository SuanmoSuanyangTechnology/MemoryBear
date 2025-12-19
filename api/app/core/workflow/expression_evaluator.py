"""
安全的表达式求值器

使用 simpleeval 库提供安全的表达式评估，避免代码注入攻击。
"""

import logging
import re
from typing import Any

from simpleeval import simple_eval, NameNotDefined, InvalidExpression

logger = logging.getLogger(__name__)


class ExpressionEvaluator:
    """安全的表达式求值器"""
    
    # 保留的命名空间
    RESERVED_NAMESPACES = {"var", "node", "sys", "nodes"}
    
    @staticmethod
    def evaluate(
        expression: str,
        variables: dict[str, Any],
        node_outputs: dict[str, Any],
        system_vars: dict[str, Any] | None = None
    ) -> Any:
        """安全地评估表达式
        
        Args:
            expression: 表达式字符串，如 "{{var.score}} > 0.8"
            variables: 用户定义的变量
            node_outputs: 节点输出结果
            system_vars: 系统变量
        
        Returns:
            表达式求值结果
        
        Raises:
            ValueError: 表达式无效或求值失败
        
        Examples:
            >>> evaluator = ExpressionEvaluator()
            >>> evaluator.evaluate(
            ...     "var.score > 0.8",
            ...     {"score": 0.9},
            ...     {},
            ...     {}
            ... )
            True
            
            >>> evaluator.evaluate(
            ...     "node.intent.output == '售前咨询'",
            ...     {},
            ...     {"intent": {"output": "售前咨询"}},
            ...     {}
            ... )
            True
        """
        # 移除 Jinja2 模板语法的花括号（如果存在）
        expression = expression.strip()
        # "{{system.message}} == {{ user.messge }}" -> "system.message == user.message"
        pattern = r"\{\{\s*(.*?)\s*\}\}"
        expression = re.sub(pattern, r"\1", expression).strip()

        # 构建命名空间上下文
        context = {
            "var": variables,                    # 用户变量
            "node": node_outputs,                # 节点输出
            "sys": system_vars or {},            # 系统变量
        }
        
        # 为了向后兼容，也支持直接访问（但会在日志中警告）
        context.update(variables)
        context["nodes"] = node_outputs
        
        try:
            # simpleeval 只支持安全的操作：
            # - 算术运算: +, -, *, /, //, %, **
            # - 比较运算: ==, !=, <, <=, >, >=
            # - 逻辑运算: and, or, not
            # - 成员运算: in, not in
            # - 属性访问: obj.attr
            # - 字典/列表访问: obj["key"], obj[0]
            # 不支持：函数调用、导入、赋值等危险操作
            result = simple_eval(expression, names=context)
            return result
            
        except NameNotDefined as e:
            logger.error(f"表达式中引用了未定义的变量: {expression}, 错误: {e}")
            raise ValueError(f"未定义的变量: {e}")
            
        except InvalidExpression as e:
            logger.error(f"表达式语法无效: {expression}, 错误: {e}")
            raise ValueError(f"表达式语法无效: {e}")
            
        except SyntaxError as e:
            logger.error(f"表达式语法错误: {expression}, 错误: {e}")
            raise ValueError(f"表达式语法错误: {e}")
            
        except Exception as e:
            logger.error(f"表达式求值异常: {expression}, 错误: {e}")
            raise ValueError(f"表达式求值失败: {e}")
    
    @staticmethod
    def evaluate_bool(
        expression: str,
        variables: dict[str, Any],
        node_outputs: dict[str, Any],
        system_vars: dict[str, Any] | None = None
    ) -> bool:
        """评估布尔表达式（用于条件判断）
        
        Args:
            expression: 布尔表达式
            variables: 用户变量
            node_outputs: 节点输出
            system_vars: 系统变量
        
        Returns:
            布尔值结果
        
        Examples:
            >>> ExpressionEvaluator.evaluate_bool(
            ...     "var.count >= 10 and var.status == 'active'",
            ...     {"count": 15, "status": "active"},
            ...     {},
            ...     {}
            ... )
            True
        """
        result = ExpressionEvaluator.evaluate(
            expression, variables, node_outputs, system_vars
        )
        return bool(result)
    
    @staticmethod
    def validate_variable_names(variables: list[dict]) -> list[str]:
        """验证变量名是否合法
        
        Args:
            variables: 变量定义列表
        
        Returns:
            错误列表，如果为空则验证通过
        
        Examples:
            >>> ExpressionEvaluator.validate_variable_names([
            ...     {"name": "user_input"},
            ...     {"name": "var"}  # 保留字
            ... ])
            ["变量名 'var' 是保留的命名空间，请使用其他名称"]
        """
        errors = []
        
        for var in variables:
            var_name = var.get("name", "")
            
            # 检查是否为保留命名空间
            if var_name in ExpressionEvaluator.RESERVED_NAMESPACES:
                errors.append(
                    f"变量名 '{var_name}' 是保留的命名空间，请使用其他名称"
                )
            
            # 检查是否为有效的 Python 标识符
            if not var_name.isidentifier():
                errors.append(
                    f"变量名 '{var_name}' 不是有效的标识符"
                )
        
        return errors


# 便捷函数
def evaluate_expression(
    expression: str,
    variables: dict[str, Any],
    node_outputs: dict[str, Any],
    system_vars: dict[str, Any] | None = None
) -> Any:
    """评估表达式（便捷函数）"""
    return ExpressionEvaluator.evaluate(
        expression, variables, node_outputs, system_vars
    )


def evaluate_condition(
    expression: str,
    variables: dict[str, Any],
    node_outputs: dict[str, Any],
    system_vars: dict[str, Any] | None = None
) -> bool:
    """评估条件表达式（便捷函数）"""
    return ExpressionEvaluator.evaluate_bool(
        expression, variables, node_outputs, system_vars
    )
