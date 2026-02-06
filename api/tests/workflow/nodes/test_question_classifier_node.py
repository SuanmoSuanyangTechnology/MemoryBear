# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/6
import pytest

from app.core.workflow.nodes import QuestionClassifierNode
from app.core.workflow.variable.base_variable import VariableType
from tests.workflow.nodes.base import TEST_MODEL_ID, simple_state, simple_vairable_pool


# 基础分类配置 - 两个类别
BASIC_TWO_CATEGORIES_CONFIG = {
    "id": "classifier_test",
    "type": "question-classifier",
    "name": "问题分类测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "input_variable": "我想买一台笔记本电脑",
        "categories": [
            {
                "class_name": "产品咨询"
            },
            {
                "class_name": "售后服务"
            }
        ],
        "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
        "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
        "user_supplement_prompt": None
    }
}

# 多类别配置
MULTI_CATEGORIES_CONFIG = {
    "id": "classifier_test",
    "type": "question-classifier",
    "name": "问题分类测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "input_variable": "我的订单什么时候能到？",
        "categories": [
            {
                "class_name": "产品咨询"
            },
            {
                "class_name": "订单查询"
            },
            {
                "class_name": "售后服务"
            },
            {
                "class_name": "投诉建议"
            }
        ],
        "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
        "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
        "user_supplement_prompt": None
    }
}

# 带补充提示的配置
WITH_SUPPLEMENT_PROMPT_CONFIG = {
    "id": "classifier_test",
    "type": "question-classifier",
    "name": "问题分类测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "input_variable": "这个产品怎么样？",
        "categories": [
            {
                "class_name": "产品咨询"
            },
            {
                "class_name": "用户评价"
            }
        ],
        "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
        "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
        "user_supplement_prompt": "如果用户在询问产品信息或特性，归类为产品咨询；如果是评价或反馈，归类为用户评价"
    }
}

# 使用变量的配置
VARIABLE_INPUT_CONFIG = {
    "id": "classifier_test",
    "type": "question-classifier",
    "name": "问题分类测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "input_variable": "{{ conv.user_question }}",
        "categories": [
            {
                "class_name": "技术支持"
            },
            {
                "class_name": "账号问题"
            }
        ],
        "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
        "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
        "user_supplement_prompt": None
    }
}

# 使用系统消息的配置
SYS_MESSAGE_CONFIG = {
    "id": "classifier_test",
    "type": "question-classifier",
    "name": "问题分类测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "input_variable": "{{ sys.message }}",
        "categories": [
            {
                "class_name": "产品咨询"
            },
            {
                "class_name": "售后服务"
            }
        ],
        "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
        "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
        "user_supplement_prompt": None
    }
}

# 空问题配置
EMPTY_QUESTION_CONFIG = {
    "id": "classifier_test",
    "type": "question-classifier",
    "name": "问题分类测试节点",
    "config": {
        "model_id": TEST_MODEL_ID,
        "input_variable": "",
        "categories": [
            {
                "class_name": "产品咨询"
            },
            {
                "class_name": "售后服务"
            }
        ],
        "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
        "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
        "user_supplement_prompt": None
    }
}


# ==================== 基础分类测试 ====================
@pytest.mark.asyncio
async def test_classify_product_inquiry():
    """测试产品咨询分类"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await QuestionClassifierNode(BASIC_TWO_CATEGORIES_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "class_name" in result
    assert "output" in result
    assert result["class_name"] == "产品咨询"
    assert result["output"] == "CASE1"


@pytest.mark.asyncio
async def test_classify_after_sales():
    """测试售后服务分类"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "我的产品坏了，怎么维修？",
            "categories": [
                {
                    "class_name": "产品咨询"
                },
                {
                    "class_name": "售后服务"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "售后服务"
    assert result["output"] == "CASE2"


# ==================== 多类别分类测试 ====================
@pytest.mark.asyncio
async def test_classify_order_inquiry():
    """测试订单查询分类"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await QuestionClassifierNode(MULTI_CATEGORIES_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "订单查询"
    assert result["output"] == "CASE2"


@pytest.mark.asyncio
async def test_classify_complaint():
    """测试投诉建议分类"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "你们的服务态度太差了！",
            "categories": [
                {
                    "class_name": "产品咨询"
                },
                {
                    "class_name": "订单查询"
                },
                {
                    "class_name": "售后服务"
                },
                {
                    "class_name": "投诉建议"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "投诉建议"
    assert result["output"] == "CASE4"


# ==================== 补充提示测试 ====================
@pytest.mark.asyncio
async def test_classify_with_supplement_prompt():
    """测试使用补充提示进行分类"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await QuestionClassifierNode(WITH_SUPPLEMENT_PROMPT_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "class_name" in result
    assert "output" in result
    assert result["class_name"] in ["产品咨询", "用户评价"]
    assert result["output"] in ["CASE1", "CASE2"]


# ==================== 变量输入测试 ====================
@pytest.mark.asyncio
async def test_classify_with_conv_variable():
    """测试使用 conv 变量作为输入"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "user_question", "我忘记密码了", VariableType.STRING, mut=True)
    
    result = await QuestionClassifierNode(VARIABLE_INPUT_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "账号问题"
    assert result["output"] == "CASE2"


@pytest.mark.asyncio
async def test_classify_with_sys_message():
    """测试使用系统消息变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("我想了解一下你们的产品功能")
    
    result = await QuestionClassifierNode(SYS_MESSAGE_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "产品咨询"
    assert result["output"] == "CASE1"


# ==================== 边界情况测试 ====================
@pytest.mark.asyncio
async def test_classify_empty_question():
    """测试空问题输入"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await QuestionClassifierNode(EMPTY_QUESTION_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "class_name" in result
    assert "output" in result
    # 空问题应该返回默认分类（第一个分类）
    assert result["class_name"] == "产品咨询"
    assert result["output"] == "CASE1"


@pytest.mark.asyncio
async def test_classify_single_category():
    """测试只有一个分类的情况"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "任何问题",
            "categories": [
                {
                    "class_name": "通用咨询"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "通用咨询"
    assert result["output"] == "CASE1"


# ==================== 复杂场景测试 ====================
@pytest.mark.asyncio
async def test_classify_ambiguous_question():
    """测试模糊问题分类"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "你好",
            "categories": [
                {
                    "class_name": "产品咨询"
                },
                {
                    "class_name": "售后服务"
                },
                {
                    "class_name": "闲聊"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] in ["产品咨询", "售后服务", "闲聊"]
    assert result["output"] in ["CASE1", "CASE2", "CASE3"]


@pytest.mark.asyncio
async def test_classify_long_question():
    """测试长问题分类"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "我在上个月购买了你们的产品，使用了一段时间后发现有一些问题，想咨询一下售后政策和维修流程，请问应该怎么办？",
            "categories": [
                {
                    "class_name": "产品咨询"
                },
                {
                    "class_name": "售后服务"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "售后服务"
    assert result["output"] == "CASE2"


@pytest.mark.asyncio
async def test_classify_technical_support():
    """测试技术支持分类"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "软件安装失败，报错代码0x80070005",
            "categories": [
                {
                    "class_name": "技术支持"
                },
                {
                    "class_name": "账号问题"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "技术支持"
    assert result["output"] == "CASE1"


@pytest.mark.asyncio
async def test_classify_multiple_categories():
    """测试多个类别的详细分类"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "我想申请退款",
            "categories": [
                {
                    "class_name": "产品咨询"
                },
                {
                    "class_name": "订单查询"
                },
                {
                    "class_name": "退换货"
                },
                {
                    "class_name": "售后服务"
                },
                {
                    "class_name": "投诉建议"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "退换货"
    assert result["output"] == "CASE3"


@pytest.mark.asyncio
async def test_classify_with_detailed_supplement():
    """测试使用详细补充提示"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "这个功能怎么用？",
            "categories": [
                {
                    "class_name": "产品使用"
                },
                {
                    "class_name": "产品介绍"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": "如果用户询问如何使用某个功能，归类为产品使用；如果询问功能是什么或有什么功能，归类为产品介绍"
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "产品使用"
    assert result["output"] == "CASE1"


@pytest.mark.asyncio
async def test_classify_chinese_categories():
    """测试中文类别名称"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "我要投诉",
            "categories": [
                {
                    "class_name": "咨询类"
                },
                {
                    "class_name": "投诉类"
                },
                {
                    "class_name": "建议类"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] == "投诉类"
    assert result["output"] == "CASE2"


@pytest.mark.asyncio
async def test_classify_case_mapping():
    """测试分类到 CASE 的映射关系"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "测试问题",
            "categories": [
                {
                    "class_name": "类别A"
                },
                {
                    "class_name": "类别B"
                },
                {
                    "class_name": "类别C"
                },
                {
                    "class_name": "类别D"
                },
                {
                    "class_name": "类别E"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "class_name" in result
    assert "output" in result
    
    # 验证 CASE 映射关系
    category_names = ["类别A", "类别B", "类别C", "类别D", "类别E"]
    if result["class_name"] in category_names:
        expected_case = f"CASE{category_names.index(result['class_name']) + 1}"
        assert result["output"] == expected_case


@pytest.mark.asyncio
async def test_classify_with_special_characters():
    """测试包含特殊字符的问题"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "classifier_test",
        "type": "question-classifier",
        "name": "问题分类测试节点",
        "config": {
            "model_id": TEST_MODEL_ID,
            "input_variable": "产品价格是多少？有优惠吗？",
            "categories": [
                {
                    "class_name": "价格咨询"
                },
                {
                    "class_name": "促销活动"
                }
            ],
            "system_prompt": "你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
            "user_prompt": "问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
            "user_supplement_prompt": None
        }
    }
    
    result = await QuestionClassifierNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["class_name"] in ["价格咨询", "促销活动"]
    assert result["output"] in ["CASE1", "CASE2"]
