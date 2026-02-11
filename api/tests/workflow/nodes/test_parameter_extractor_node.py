# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/6 14:10
import pytest

from app.core.workflow.nodes import ParameterExtractorNode
from app.core.workflow.variable.base_variable import VariableType
from tests.workflow.nodes.base import TEST_MODEL_ID, simple_state, simple_vairable_pool


# 基础参数提取配置 - 单个字符串参数
SINGLE_STRING_PARAM_CONFIG = {
    "id": "param_extractor_test",
    "type": "parameter-extractor",
    "name": "参数提取测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "text": "我的名字是张三，今年25岁",
        "params": [
            {
                "name": "name",
                "type": "string",
                "desc": "用户的姓名",
                "required": True
            }
        ],
        "prompt": ""
    }
}

# 多参数提取配置
MULTI_PARAMS_CONFIG = {
    "id": "param_extractor_test",
    "type": "parameter-extractor",
    "name": "参数提取测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "text": "我的名字是李四，今年30岁，住在北京",
        "params": [
            {
                "name": "name",
                "type": "string",
                "desc": "用户的姓名",
                "required": True
            },
            {
                "name": "age",
                "type": "number",
                "desc": "用户的年龄",
                "required": True
            },
            {
                "name": "city",
                "type": "string",
                "desc": "用户所在的城市",
                "required": False
            }
        ],
        "prompt": ""
    }
}

# 数字参数提取配置
NUMBER_PARAM_CONFIG = {
    "id": "param_extractor_test",
    "type": "parameter-extractor",
    "name": "参数提取测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "text": "这个产品的价格是99.99元，库存有100件",
        "params": [
            {
                "name": "price",
                "type": "number",
                "desc": "产品价格",
                "required": True
            },
            {
                "name": "stock",
                "type": "number",
                "desc": "库存数量",
                "required": True
            }
        ],
        "prompt": ""
    }
}

# 布尔参数提取配置
BOOLEAN_PARAM_CONFIG = {
    "id": "param_extractor_test",
    "type": "parameter-extractor",
    "name": "参数提取测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "text": "这个用户已经完成了实名认证，但还没有绑定手机号",
        "params": [
            {
                "name": "verified",
                "type": "boolean",
                "desc": "是否完成实名认证",
                "required": True
            },
            {
                "name": "phone_bound",
                "type": "boolean",
                "desc": "是否绑定手机号",
                "required": True
            }
        ],
        "prompt": ""
    }
}

# 数组参数提取配置
ARRAY_STRING_PARAM_CONFIG = {
    "id": "param_extractor_test",
    "type": "parameter-extractor",
    "name": "参数提取测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "text": "我喜欢的水果有苹果、香蕉、橙子",
        "params": [
            {
                "name": "fruits",
                "type": "array[string]",
                "desc": "喜欢的水果列表",
                "required": True
            }
        ],
        "prompt": ""
    }
}

# 数字数组参数提取配置
ARRAY_NUMBER_PARAM_CONFIG = {
    "id": "param_extractor_test",
    "type": "parameter-extractor",
    "name": "参数提取测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "text": "这个月的销售额分别是：第一周10000，第二周12000，第三周15000，第四周18000",
        "params": [
            {
                "name": "weekly_sales",
                "type": "array[number]",
                "desc": "每周的销售额",
                "required": True
            }
        ],
        "prompt": ""
    }
}

# 带自定义提示的配置
CUSTOM_PROMPT_CONFIG = {
    "id": "param_extractor_test",
    "type": "parameter-extractor",
    "name": "参数提取测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "text": "订单号：ORD123456，金额：299元",
        "params": [
            {
                "name": "order_id",
                "type": "string",
                "desc": "订单编号",
                "required": True
            },
            {
                "name": "amount",
                "type": "number",
                "desc": "订单金额",
                "required": True
            }
        ],
        "prompt": "请仔细提取订单信息，确保订单号和金额准确无误"
    }
}

# 使用变量的配置
VARIABLE_INPUT_CONFIG = {
    "id": "param_extractor_test",
    "type": "parameter-extractor",
    "name": "参数提取测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "text": "{{ conv.user_input }}",
        "params": [
            {
                "name": "name",
                "type": "string",
                "desc": "用户姓名",
                "required": True
            },
            {
                "name": "age",
                "type": "number",
                "desc": "用户年龄",
                "required": True
            }
        ],
        "prompt": ""
    }
}


# ==================== 基础参数提取测试 ====================
@pytest.mark.asyncio
async def test_extract_single_string_param():
    """测试提取单个字符串参数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await ParameterExtractorNode(SINGLE_STRING_PARAM_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "name" in result
    assert isinstance(result["name"], str)
    assert "张三" in result["name"]


@pytest.mark.asyncio
async def test_extract_multi_params():
    """测试提取多个参数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await ParameterExtractorNode(MULTI_PARAMS_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "name" in result
    assert "age" in result
    assert "city" in result
    assert isinstance(result["name"], str)
    assert isinstance(result["age"], (int, float))
    assert "李四" in result["name"]
    assert result["age"] == 30
    assert "北京" in result["city"]


# ==================== 数字参数提取测试 ====================
@pytest.mark.asyncio
async def test_extract_number_params():
    """测试提取数字参数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await ParameterExtractorNode(NUMBER_PARAM_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "price" in result
    assert "stock" in result
    assert isinstance(result["price"], (int, float))
    assert isinstance(result["stock"], (int, float))
    assert abs(result["price"] - 99.99) < 0.1
    assert result["stock"] == 100


# ==================== 布尔参数提取测试 ====================
@pytest.mark.asyncio
async def test_extract_boolean_params():
    """测试提取布尔参数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await ParameterExtractorNode(BOOLEAN_PARAM_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "verified" in result
    assert "phone_bound" in result
    assert isinstance(result["verified"], bool)
    assert isinstance(result["phone_bound"], bool)
    assert result["verified"] is True
    assert result["phone_bound"] is False


# ==================== 数组参数提取测试 ====================
@pytest.mark.asyncio
async def test_extract_array_string_param():
    """测试提取字符串数组参数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await ParameterExtractorNode(ARRAY_STRING_PARAM_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "fruits" in result
    assert isinstance(result["fruits"], list)
    assert len(result["fruits"]) >= 3
    assert "苹果" in result["fruits"]
    assert "香蕉" in result["fruits"]
    assert "橙子" in result["fruits"]


@pytest.mark.asyncio
async def test_extract_array_number_param():
    """测试提取数字数组参数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await ParameterExtractorNode(ARRAY_NUMBER_PARAM_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "weekly_sales" in result
    assert isinstance(result["weekly_sales"], list)
    assert len(result["weekly_sales"]) == 4
    assert 10000 in result["weekly_sales"]
    assert 12000 in result["weekly_sales"]
    assert 15000 in result["weekly_sales"]
    assert 18000 in result["weekly_sales"]


# ==================== 自定义提示测试 ====================
@pytest.mark.asyncio
async def test_extract_with_custom_prompt():
    """测试使用自定义提示提取参数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await ParameterExtractorNode(CUSTOM_PROMPT_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "order_id" in result
    assert "amount" in result
    assert "ORD123456" in result["order_id"]
    assert isinstance(result["amount"], (int, float))
    assert result["amount"] == 299


# ==================== 变量输入测试 ====================
@pytest.mark.asyncio
async def test_extract_with_variable_input():
    """测试使用变量作为输入文本"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "user_input", "我叫王五，今年28岁", VariableType.STRING, mut=True)
    
    result = await ParameterExtractorNode(VARIABLE_INPUT_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "name" in result
    assert "age" in result
    assert "王五" in result["name"]
    assert result["age"] == 28


# ==================== 复杂场景测试 ====================
@pytest.mark.asyncio
async def test_extract_from_complex_text():
    """测试从复杂文本中提取参数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "param_extractor_test",
        "type": "parameter-extractor",
        "name": "参数提取测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "text": """
            客户信息：
            姓名：赵六
            年龄：35岁
            职业：软件工程师
            城市：上海
            邮箱：zhaoliu@example.com
            是否VIP：是
            """,
            "params": [
                {
                    "name": "name",
                    "type": "string",
                    "desc": "客户姓名",
                    "required": True
                },
                {
                    "name": "age",
                    "type": "number",
                    "desc": "客户年龄",
                    "required": True
                },
                {
                    "name": "occupation",
                    "type": "string",
                    "desc": "客户职业",
                    "required": False
                },
                {
                    "name": "city",
                    "type": "string",
                    "desc": "所在城市",
                    "required": False
                },
                {
                    "name": "is_vip",
                    "type": "boolean",
                    "desc": "是否为VIP客户",
                    "required": False
                }
            ],
            "prompt": ""
        }
    }
    
    result = await ParameterExtractorNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "name" in result
    assert "age" in result
    assert "赵六" in result["name"]
    assert result["age"] == 35
    if "occupation" in result:
        assert "工程师" in result["occupation"]
    if "city" in result:
        assert "上海" in result["city"]
    if "is_vip" in result:
        assert result["is_vip"] is True


@pytest.mark.asyncio
async def test_extract_optional_params():
    """测试提取可选参数"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "param_extractor_test",
        "type": "parameter-extractor",
        "name": "参数提取测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "text": "我叫小明",
            "params": [
                {
                    "name": "name",
                    "type": "string",
                    "desc": "用户姓名",
                    "required": True
                },
                {
                    "name": "age",
                    "type": "number",
                    "desc": "用户年龄",
                    "required": False
                },
                {
                    "name": "city",
                    "type": "string",
                    "desc": "所在城市",
                    "required": False
                }
            ],
            "prompt": ""
        }
    }
    
    result = await ParameterExtractorNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "name" in result
    assert "小明" in result["name"]
    # 可选参数可能不存在或为 None


@pytest.mark.asyncio
async def test_extract_with_sys_message():
    """测试使用系统消息变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("我叫小红，今年22岁")
    
    config = {
        "id": "param_extractor_test",
        "type": "parameter-extractor",
        "name": "参数提取测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "text": "{{ sys.message }}",
            "params": [
                {
                    "name": "name",
                    "type": "string",
                    "desc": "用户姓名",
                    "required": True
                },
                {
                    "name": "age",
                    "type": "number",
                    "desc": "用户年龄",
                    "required": True
                }
            ],
            "prompt": ""
        }
    }
    
    result = await ParameterExtractorNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "name" in result
    assert "age" in result
    assert "小红" in result["name"]
    assert result["age"] == 22
