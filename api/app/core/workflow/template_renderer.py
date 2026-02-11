"""
模板渲染器

使用 Jinja2 提供安全的模板渲染功能，支持变量引用和表达式。
"""

import logging
from collections import defaultdict
from typing import Any

from jinja2 import TemplateSyntaxError, UndefinedError, Environment, StrictUndefined, Undefined

logger = logging.getLogger(__name__)


class SafeUndefined(Undefined):
    """访问未定义属性不会报错，返回空字符串"""
    __slots__ = ()

    def _fail_with_undefined_error(self, *args, **kwargs):
        return ""

    __add__ = __radd__ = __mul__ = __rmul__ = __div__ = __rdiv__ = __truediv__ = __rtruediv__ = _fail_with_undefined_error
    __getitem__ = __getattr__ = _fail_with_undefined_error
    __str__ = __repr__ = lambda self: ""


class TemplateRenderer:
    """模板渲染器"""

    def __init__(self, strict: bool = True):
        """初始化渲染器
        
        Args:
            strict: 是否使用严格模式（未定义变量会抛出异常）
        """
        self.strict = strict
        self.env = Environment(
            undefined=StrictUndefined if strict else SafeUndefined,
            autoescape=False  # 不自动转义，因为我们处理的是文本而非 HTML
        )

    def render(
            self,
            template: str,
            conv_vars: dict[str, Any],
            node_outputs: dict[str, Any],
            system_vars: dict[str, Any] | None = None
    ) -> str:
        """渲染模板
        
        Args:
            template: 模板字符串
            conv_vars: 会话变量
            node_outputs: 节点输出结果
            system_vars: 系统变量
        
        Returns:
            渲染后的字符串
        
        Raises:
            ValueError: 模板语法错误或变量未定义
        
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
            ...     "分析结果: {{node.analyze.output}}",
            ...     {},
            ...     {"analyze": {"output": "正面情绪"}},
            ...     {}
            ... )
            '分析结果: 正面情绪'
        """
        # 构建命名空间上下文
        context = {
            "conv": conv_vars,  # 会话变量：{{conv.user_name}}
            "node": node_outputs,  # 节点输出：{{node.node_1.output}}
            "sys": system_vars,  # 系统变量：{{sys.execution_id}}
        }

        # 支持直接通过节点ID访问节点输出：{{llm_qa.output}}
        # 将所有节点输出添加到顶层上下文
        if node_outputs:
            context.update(node_outputs)

        # 支持直接访问会话变量（不需要 conv. 前缀）：{{user_name}}
        if conv_vars:
            context.update(conv_vars)

        context["nodes"] = node_outputs or {}  # 旧语法兼容

        try:
            tmpl = self.env.from_string(template)
            return tmpl.render(**context)

        except TemplateSyntaxError as e:
            logger.error(f"模板语法错误: {template}, 错误: {e}")
            raise ValueError(f"模板语法错误: {e}")

        except UndefinedError as e:
            logger.error(f"模板中引用了未定义的变量: {template}, 错误: {e}")
            raise ValueError(f"未定义的变量: {e}")

        except Exception as e:
            logger.error(f"模板渲染异常: {template}, 错误: {e}")
            raise ValueError(f"模板渲染失败: {e}")

    def validate(self, template: str) -> list[str]:
        """验证模板语法
        
        Args:
            template: 模板字符串
        
        Returns:
            错误列表，如果为空则验证通过
        
        Examples:
            >>> renderer = TemplateRenderer()
            >>> renderer.validate("Hello {{var.name}}!")
            []
            
            >>> renderer.validate("Hello {{var.name")  # 缺少结束标记
            ['模板语法错误: ...']
        """
        errors = []

        try:
            self.env.from_string(template)
        except TemplateSyntaxError as e:
            errors.append(f"模板语法错误: {e}")
        except Exception as e:
            errors.append(f"模板验证失败: {e}")

        return errors


# 全局渲染器实例（严格模式）
_default_renderer = TemplateRenderer(strict=True)


def render_template(
        template: str,
        conv_vars: dict[str, Any],
        node_outputs: dict[str, Any],
        system_vars: dict[str, Any],
        strict: bool = True
) -> str:
    """渲染模板（便捷函数）
    
    Args:
        strict: 严格模式
        template: 模板字符串
        conv_vars: 会话变量
        node_outputs: 节点输出
        system_vars: 系统变量
    
    Returns:
        渲染后的字符串
    
    Examples:
        >>> render_template(
        ...     "请分析: {{var.text}}",
        ...     {"text": "这是一段文本"},
        ...     {},
        ...     {}
        ... )
        '请分析: 这是一段文本'
    """
    renderer = TemplateRenderer(strict=strict)
    return renderer.render(template, conv_vars, node_outputs, system_vars)


def validate_template(template: str) -> list[str]:
    """验证模板语法（便捷函数）
    
    Args:
        template: 模板字符串
    
    Returns:
        错误列表
    """
    return _default_renderer.validate(template)
