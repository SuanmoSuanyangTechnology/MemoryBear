# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/6
import pytest

from app.core.workflow.nodes import JinjaRenderNode
from app.core.workflow.variable.base_variable import VariableType
from tests.workflow.nodes.base import simple_state, simple_vairable_pool


# 基础模板渲染配置
SIMPLE_TEMPLATE_CONFIG = {
    "id": "jinja_test",
    "type": "jinja-render",
    "name": "Jinja渲染测试节点",
    "config": {
        "template": "Hello, {{ name }}!",
        "mapping": [
            {
                "name": "name",
                "value": "conv.username"
            }
        ]
    }
}

# 多变量模板配置
MULTI_VARIABLE_CONFIG = {
    "id": "jinja_test",
    "type": "jinja-render",
    "name": "Jinja渲染测试节点",
    "config": {
        "template": "{{ greeting }}, {{ name }}! You are {{ age }} years old.",
        "mapping": [
            {
                "name": "greeting",
                "value": "conv.greeting"
            },
            {
                "name": "name",
                "value": "conv.name"
            },
            {
                "name": "age",
                "value": "conv.age"
            }
        ]
    }
}

# 条件渲染配置
CONDITIONAL_TEMPLATE_CONFIG = {
    "id": "jinja_test",
    "type": "jinja-render",
    "name": "Jinja渲染测试节点",
    "config": {
        "template": "{% if is_admin %}Admin{% else %}User{% endif %}",
        "mapping": [
            {
                "name": "is_admin",
                "value": "conv.is_admin"
            }
        ]
    }
}

# 循环渲染配置
LOOP_TEMPLATE_CONFIG = {
    "id": "jinja_test",
    "type": "jinja-render",
    "name": "Jinja渲染测试节点",
    "config": {
        "template": "{% for item in items %}{{ item }}{% if not loop.last %}, {% endif %}{% endfor %}",
        "mapping": [
            {
                "name": "items",
                "value": "conv.items"
            }
        ]
    }
}

# 过滤器配置
FILTER_TEMPLATE_CONFIG = {
    "id": "jinja_test",
    "type": "jinja-render",
    "name": "Jinja渲染测试节点",
    "config": {
        "template": "{{ text | upper }}",
        "mapping": [
            {
                "name": "text",
                "value": "conv.text"
            }
        ]
    }
}

# 对象属性访问配置
OBJECT_TEMPLATE_CONFIG = {
    "id": "jinja_test",
    "type": "jinja-render",
    "name": "Jinja渲染测试节点",
    "config": {
        "template": "Name: {{ user.name }}, Age: {{ user.age }}",
        "mapping": [
            {
                "name": "user",
                "value": "conv.user"
            }
        ]
    }
}

# 数学运算配置
MATH_TEMPLATE_CONFIG = {
    "id": "jinja_test",
    "type": "jinja-render",
    "name": "Jinja渲染测试节点",
    "config": {
        "template": "{{ a }} + {{ b }} = {{ a + b }}",
        "mapping": [
            {
                "name": "a",
                "value": "conv.a"
            },
            {
                "name": "b",
                "value": "conv.b"
            }
        ]
    }
}

# 默认值配置
DEFAULT_VALUE_CONFIG = {
    "id": "jinja_test",
    "type": "jinja-render",
    "name": "Jinja渲染测试节点",
    "config": {
        "template": "{{ name | default('Guest') }}",
        "mapping": [
            {
                "name": "name",
                "value": "conv.name"
            }
        ]
    }
}


# ==================== 基础模板渲染测试 ====================
@pytest.mark.asyncio
async def test_jinja_simple_template():
    """测试简单模板渲染"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "username", "Alice", VariableType.STRING, mut=True)
    
    result = await JinjaRenderNode(SIMPLE_TEMPLATE_CONFIG, {}).execute(state, variable_pool)
    assert result == "Hello, Alice!"


@pytest.mark.asyncio
async def test_jinja_multi_variable():
    """测试多变量模板渲染"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "greeting", "Hi", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "name", "Bob", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "age", 25, VariableType.NUMBER, mut=True)
    
    result = await JinjaRenderNode(MULTI_VARIABLE_CONFIG, {}).execute(state, variable_pool)
    assert result == "Hi, Bob! You are 25 years old."


# ==================== 条件渲染测试 ====================
@pytest.mark.asyncio
async def test_jinja_conditional_true():
    """测试条件渲染为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "is_admin", True, VariableType.BOOLEAN, mut=True)
    
    result = await JinjaRenderNode(CONDITIONAL_TEMPLATE_CONFIG, {}).execute(state, variable_pool)
    assert result == "Admin"


@pytest.mark.asyncio
async def test_jinja_conditional_false():
    """测试条件渲染为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "is_admin", False, VariableType.BOOLEAN, mut=True)
    
    result = await JinjaRenderNode(CONDITIONAL_TEMPLATE_CONFIG, {}).execute(state, variable_pool)
    assert result == "User"


# ==================== 循环渲染测试 ====================
@pytest.mark.asyncio
async def test_jinja_loop_array():
    """测试数组循环渲染"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "items", ["apple", "banana", "cherry"], VariableType.ARRAY_STRING, mut=True)
    
    result = await JinjaRenderNode(LOOP_TEMPLATE_CONFIG, {}).execute(state, variable_pool)
    assert result == "apple, banana, cherry"


@pytest.mark.asyncio
async def test_jinja_loop_empty_array():
    """测试空数组循环渲染"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "items", [], VariableType.ARRAY_STRING, mut=True)
    
    result = await JinjaRenderNode(LOOP_TEMPLATE_CONFIG, {}).execute(state, variable_pool)
    assert result == ""


@pytest.mark.asyncio
async def test_jinja_loop_single_item():
    """测试单元素数组循环渲染"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "items", ["apple"], VariableType.ARRAY_STRING, mut=True)
    
    result = await JinjaRenderNode(LOOP_TEMPLATE_CONFIG, {}).execute(state, variable_pool)
    assert result == "apple"


# ==================== 过滤器测试 ====================
@pytest.mark.asyncio
async def test_jinja_filter_upper():
    """测试大写过滤器"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "text", "hello world", VariableType.STRING, mut=True)
    
    result = await JinjaRenderNode(FILTER_TEMPLATE_CONFIG, {}).execute(state, variable_pool)
    assert result == "HELLO WORLD"


@pytest.mark.asyncio
async def test_jinja_filter_lower():
    """测试小写过滤器"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "text", "HELLO WORLD", VariableType.STRING, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ text | lower }}",
            "mapping": [
                {
                    "name": "text",
                    "value": "conv.text"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "hello world"


@pytest.mark.asyncio
async def test_jinja_filter_title():
    """测试标题化过滤器"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "text", "hello world", VariableType.STRING, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ text | title }}",
            "mapping": [
                {
                    "name": "text",
                    "value": "conv.text"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Hello World"


@pytest.mark.asyncio
async def test_jinja_filter_length():
    """测试长度过滤器"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "items", [1, 2, 3, 4, 5], VariableType.ARRAY_NUMBER, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "Length: {{ items | length }}",
            "mapping": [
                {
                    "name": "items",
                    "value": "conv.items"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Length: 5"


# ==================== 对象属性访问测试 ====================
@pytest.mark.asyncio
async def test_jinja_object_access():
    """测试对象属性访问"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "user", {"name": "Alice", "age": 30}, VariableType.OBJECT, mut=True)
    
    result = await JinjaRenderNode(OBJECT_TEMPLATE_CONFIG, {}).execute(state, variable_pool)
    assert result == "Name: Alice, Age: 30"


@pytest.mark.asyncio
async def test_jinja_nested_object():
    """测试嵌套对象访问"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "data", {
        "user": {
            "name": "Bob",
            "address": {
                "city": "Beijing"
            }
        }
    }, VariableType.OBJECT, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ data.user.name }} lives in {{ data.user.address.city }}",
            "mapping": [
                {
                    "name": "data",
                    "value": "conv.data"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Bob lives in Beijing"


# ==================== 数学运算测试 ====================
@pytest.mark.asyncio
async def test_jinja_math_addition():
    """测试加法运算"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "a", 10, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "b", 20, VariableType.NUMBER, mut=True)
    
    result = await JinjaRenderNode(MATH_TEMPLATE_CONFIG, {}).execute(state, variable_pool)
    assert result == "10 + 20 = 30"


@pytest.mark.asyncio
async def test_jinja_math_subtraction():
    """测试减法运算"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "a", 30, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "b", 10, VariableType.NUMBER, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ a }} - {{ b }} = {{ a - b }}",
            "mapping": [
                {
                    "name": "a",
                    "value": "conv.a"
                },
                {
                    "name": "b",
                    "value": "conv.b"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "30 - 10 = 20"


@pytest.mark.asyncio
async def test_jinja_math_multiplication():
    """测试乘法运算"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "a", 5, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "b", 6, VariableType.NUMBER, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ a }} * {{ b }} = {{ a * b }}",
            "mapping": [
                {
                    "name": "a",
                    "value": "conv.a"
                },
                {
                    "name": "b",
                    "value": "conv.b"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "5 * 6 = 30"


@pytest.mark.asyncio
async def test_jinja_math_division():
    """测试除法运算"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "a", 20, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "b", 4, VariableType.NUMBER, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ a }} / {{ b }} = {{ a / b }}",
            "mapping": [
                {
                    "name": "a",
                    "value": "conv.a"
                },
                {
                    "name": "b",
                    "value": "conv.b"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "20 / 4 = 5.0"


# ==================== 默认值测试 ====================
@pytest.mark.asyncio
async def test_jinja_default_value_missing():
    """测试变量缺失时使用默认值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    # 不创建 name 变量
    
    result = await JinjaRenderNode(DEFAULT_VALUE_CONFIG, {}).execute(state, variable_pool)
    assert result == "Guest"


@pytest.mark.asyncio
async def test_jinja_default_value_present():
    """测试变量存在时不使用默认值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "name", "Alice", VariableType.STRING, mut=True)
    
    result = await JinjaRenderNode(DEFAULT_VALUE_CONFIG, {}).execute(state, variable_pool)
    assert result == "Alice"


# ==================== 字符串拼接测试 ====================
@pytest.mark.asyncio
async def test_jinja_string_concatenation():
    """测试字符串拼接"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "first", "Hello", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "second", "World", VariableType.STRING, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ first ~ ' ' ~ second }}",
            "mapping": [
                {
                    "name": "first",
                    "value": "conv.first"
                },
                {
                    "name": "second",
                    "value": "conv.second"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Hello World"


# ==================== 比较运算测试 ====================
@pytest.mark.asyncio
async def test_jinja_comparison():
    """测试比较运算"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "score", 85, VariableType.NUMBER, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{% if score >= 90 %}A{% elif score >= 80 %}B{% elif score >= 70 %}C{% else %}D{% endif %}",
            "mapping": [
                {
                    "name": "score",
                    "value": "conv.score"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "B"


# ==================== 数组操作测试 ====================
@pytest.mark.asyncio
async def test_jinja_array_index():
    """测试数组索引访问"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "items", ["first", "second", "third"], VariableType.ARRAY_STRING, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "First: {{ items[0] }}, Last: {{ items[-1] }}",
            "mapping": [
                {
                    "name": "items",
                    "value": "conv.items"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "First: first, Last: third"


@pytest.mark.asyncio
async def test_jinja_array_slice():
    """测试数组切片"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "numbers", [1, 2, 3, 4, 5], VariableType.ARRAY_NUMBER, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{% for n in numbers[1:4] %}{{ n }}{% endfor %}",
            "mapping": [
                {
                    "name": "numbers",
                    "value": "conv.numbers"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "234"


# ==================== 复杂模板测试 ====================
@pytest.mark.asyncio
async def test_jinja_complex_template():
    """测试复杂模板"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "users", [
        {"name": "Alice", "age": 25},
        {"name": "Bob", "age": 30},
        {"name": "Charlie", "age": 35}
    ], VariableType.ARRAY_OBJECT, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{% for user in users %}{{ user.name }} ({{ user.age }}){% if not loop.last %}, {% endif %}{% endfor %}",
            "mapping": [
                {
                    "name": "users",
                    "value": "conv.users"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Alice (25), Bob (30), Charlie (35)"


# ==================== 空值处理测试 ====================
@pytest.mark.asyncio
async def test_jinja_empty_string():
    """测试空字符串"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "text", "", VariableType.STRING, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{% if text %}{{ text }}{% else %}Empty{% endif %}",
            "mapping": [
                {
                    "name": "text",
                    "value": "conv.text"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Empty"


@pytest.mark.asyncio
async def test_jinja_zero_value():
    """测试零值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "count", 0, VariableType.NUMBER, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "Count: {{ count }}",
            "mapping": [
                {
                    "name": "count",
                    "value": "conv.count"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Count: 0"


# ==================== 特殊字符测试 ====================
@pytest.mark.asyncio
async def test_jinja_special_characters():
    """测试特殊字符"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "text", "Hello \"World\"", VariableType.STRING, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ text }}",
            "mapping": [
                {
                    "name": "text",
                    "value": "conv.text"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Hello \"World\""


@pytest.mark.asyncio
async def test_jinja_newline():
    """测试换行符"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "line1", "First line", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "line2", "Second line", VariableType.STRING, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ line1 }}\n{{ line2 }}",
            "mapping": [
                {
                    "name": "line1",
                    "value": "conv.line1"
                },
                {
                    "name": "line2",
                    "value": "conv.line2"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "First line\nSecond line"


# ==================== 错误处理测试 ====================
@pytest.mark.asyncio
async def test_jinja_invalid_template():
    """测试无效模板语法"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "name", "Alice", VariableType.STRING, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ name",  # 缺少闭合括号
            "mapping": [
                {
                    "name": "name",
                    "value": "conv.name"
                }
            ]
        }
    }
    with pytest.raises(RuntimeError) as exc_info:
        await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert "render failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_jinja_undefined_variable_strict_false():
    """测试未定义变量（非严格模式）"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    # 不创建任何变量
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "Hello, {{ undefined_var }}!",
            "mapping": [
                {
                    "name": "undefined_var",
                    "value": "conv.undefined"
                }
            ]
        }
    }
    # 非严格模式下，未定义变量会被渲染为空字符串
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Hello, !"


# ==================== 布尔值测试 ====================
@pytest.mark.asyncio
async def test_jinja_boolean_true():
    """测试布尔值 True"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "flag", True, VariableType.BOOLEAN, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "Flag is {{ flag }}",
            "mapping": [
                {
                    "name": "flag",
                    "value": "conv.flag"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Flag is True"


@pytest.mark.asyncio
async def test_jinja_boolean_false():
    """测试布尔值 False"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "flag", False, VariableType.BOOLEAN, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "Flag is {{ flag }}",
            "mapping": [
                {
                    "name": "flag",
                    "value": "conv.flag"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Flag is False"


# ==================== 浮点数测试 ====================
@pytest.mark.asyncio
async def test_jinja_float_number():
    """测试浮点数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "price", 19.99, VariableType.NUMBER, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "Price: ${{ price }}",
            "mapping": [
                {
                    "name": "price",
                    "value": "conv.price"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "Price: $19.99"


@pytest.mark.asyncio
async def test_jinja_float_formatting():
    """测试浮点数格式化"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "value", 3.14159, VariableType.NUMBER, mut=True)
    
    config = {
        "id": "jinja_test",
        "type": "jinja-render",
        "name": "Jinja渲染测试节点",
        "config": {
            "template": "{{ '%.2f' | format(value) }}",
            "mapping": [
                {
                    "name": "value",
                    "value": "conv.value"
                }
            ]
        }
    }
    result = await JinjaRenderNode(config, {}).execute(state, variable_pool)
    assert result == "3.14"
