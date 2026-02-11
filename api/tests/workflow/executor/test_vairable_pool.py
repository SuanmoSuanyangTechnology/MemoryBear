# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/6
import pytest

from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool, VariableSelector


# ==================== VariableSelector 测试 ====================
def test_variable_selector_from_string():
    """测试从字符串创建变量选择器"""
    selector = VariableSelector.from_string("sys.message")
    
    assert selector.namespace == "sys"
    assert selector.key == "message"
    assert selector.path == ["sys", "message"]


def test_variable_selector_from_list():
    """测试从列表创建变量选择器"""
    selector = VariableSelector(["conv", "username"])
    
    assert selector.namespace == "conv"
    assert selector.key == "username"
    assert str(selector) == "conv.username"


def test_variable_selector_empty_path():
    """测试空路径抛出异常"""
    with pytest.raises(ValueError) as exc_info:
        VariableSelector([])
    
    assert "变量路径不能为空" in str(exc_info.value)


def test_variable_selector_single_element():
    """测试单元素路径"""
    selector = VariableSelector(["sys"])
    
    assert selector.namespace == "sys"
    assert selector.key is None


# ==================== VariablePool 基础测试 ====================
@pytest.mark.asyncio
async def test_variable_pool_new_variable():
    """测试创建新变量"""
    pool = VariablePool()
    
    await pool.new("conv", "username", "Alice", VariableType.STRING, mut=True)
    
    assert pool.has("conv.username")
    assert pool.get_value("conv.username") == "Alice"


@pytest.mark.asyncio
async def test_variable_pool_new_multiple_variables():
    """测试创建多个变量"""
    pool = VariablePool()
    
    await pool.new("conv", "name", "Bob", VariableType.STRING, mut=True)
    await pool.new("conv", "age", 25, VariableType.NUMBER, mut=True)
    await pool.new("conv", "active", True, VariableType.BOOLEAN, mut=True)
    
    assert pool.get_value("conv.name") == "Bob"
    assert pool.get_value("conv.age") == 25
    assert pool.get_value("conv.active") is True


@pytest.mark.asyncio
async def test_variable_pool_different_namespaces():
    """测试不同命名空间的变量"""
    pool = VariablePool()
    
    await pool.new("sys", "message", "Hello", VariableType.STRING, mut=False)
    await pool.new("conv", "message", "World", VariableType.STRING, mut=True)
    await pool.new("node1", "output", "Result", VariableType.STRING, mut=False)
    
    assert pool.get_value("sys.message") == "Hello"
    assert pool.get_value("conv.message") == "World"
    assert pool.get_value("node1.output") == "Result"


# ==================== get_value 测试 ====================
@pytest.mark.asyncio
async def test_get_value_with_template():
    """测试使用模板语法获取值"""
    pool = VariablePool()
    
    await pool.new("conv", "test", "value", VariableType.STRING, mut=True)
    
    # 支持模板语法
    assert pool.get_value("{{ conv.test }}") == "value"
    assert pool.get_value("{{conv.test}}") == "value"
    assert pool.get_value("{{ conv.test}}") == "value"


@pytest.mark.asyncio
async def test_get_value_not_exist_strict():
    """测试获取不存在的变量（严格模式）"""
    pool = VariablePool()
    
    with pytest.raises(KeyError) as exc_info:
        pool.get_value("conv.nonexistent")
    
    assert "not exist" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_value_not_exist_with_default():
    """测试获取不存在的变量（使用默认值）"""
    pool = VariablePool()
    
    result = pool.get_value("conv.nonexistent", default="default_value", strict=False)
    
    assert result == "default_value"


@pytest.mark.asyncio
async def test_get_value_different_types():
    """测试获取不同类型的变量值"""
    pool = VariablePool()
    
    await pool.new("conv", "str", "text", VariableType.STRING, mut=True)
    await pool.new("conv", "num", 42, VariableType.NUMBER, mut=True)
    await pool.new("conv", "bool", False, VariableType.BOOLEAN, mut=True)
    await pool.new("conv", "arr", [1, 2, 3], VariableType.ARRAY_NUMBER, mut=True)
    await pool.new("conv", "obj", {"key": "value"}, VariableType.OBJECT, mut=True)
    
    assert pool.get_value("conv.str") == "text"
    assert pool.get_value("conv.num") == 42
    assert pool.get_value("conv.bool") is False
    assert pool.get_value("conv.arr") == [1, 2, 3]
    assert pool.get_value("conv.obj") == {"key": "value"}


# ==================== set 测试 ====================
@pytest.mark.asyncio
async def test_set_mutable_variable():
    """测试设置可变变量"""
    pool = VariablePool()
    
    await pool.new("conv", "counter", 0, VariableType.NUMBER, mut=True)
    await pool.set("conv.counter", 10)
    
    assert pool.get_value("conv.counter") == 10


@pytest.mark.asyncio
async def test_set_immutable_variable():
    """测试设置不可变变量（应该失败）"""
    pool = VariablePool()
    
    await pool.new("sys", "message", "original", VariableType.STRING, mut=False)
    
    with pytest.raises(KeyError) as exc_info:
        await pool.set("sys.message", "modified")
    
    assert "cannot be modified" in str(exc_info.value)


@pytest.mark.asyncio
async def test_set_nonexistent_variable():
    """测试设置不存在的变量"""
    pool = VariablePool()
    
    with pytest.raises(KeyError) as exc_info:
        await pool.set("conv.nonexistent", "value")
    
    assert "is not defined" in str(exc_info.value)


@pytest.mark.asyncio
async def test_set_multiple_times():
    """测试多次设置变量"""
    pool = VariablePool()
    
    await pool.new("conv", "value", "first", VariableType.STRING, mut=True)
    await pool.set("conv.value", "second")
    await pool.set("conv.value", "third")
    
    assert pool.get_value("conv.value") == "third"


# ==================== has 测试 ====================
@pytest.mark.asyncio
async def test_has_existing_variable():
    """测试检查存在的变量"""
    pool = VariablePool()
    
    await pool.new("conv", "test", "value", VariableType.STRING, mut=True)
    
    assert pool.has("conv.test") is True


@pytest.mark.asyncio
async def test_has_nonexistent_variable():
    """测试检查不存在的变量"""
    pool = VariablePool()
    
    assert pool.has("conv.nonexistent") is False


# ==================== get_literal 测试 ====================
@pytest.mark.asyncio
async def test_get_literal():
    """测试获取变量的字面量表示"""
    pool = VariablePool()
    
    await pool.new("conv", "num", 42, VariableType.NUMBER, mut=True)
    
    literal = pool.get_literal("conv.num")
    
    assert isinstance(literal, str)


# ==================== 命名空间操作测试 ====================
@pytest.mark.asyncio
async def test_get_all_system_vars():
    """测试获取所有系统变量"""
    pool = VariablePool()
    
    await pool.new("sys", "message", "Hello", VariableType.STRING, mut=False)
    await pool.new("sys", "user_id", "user123", VariableType.STRING, mut=False)
    await pool.new("conv", "other", "value", VariableType.STRING, mut=True)
    
    sys_vars = pool.get_all_system_vars()
    
    assert "message" in sys_vars
    assert "user_id" in sys_vars
    assert "other" not in sys_vars
    assert sys_vars["message"] == "Hello"
    assert sys_vars["user_id"] == "user123"


@pytest.mark.asyncio
async def test_get_all_conversation_vars():
    """测试获取所有会话变量"""
    pool = VariablePool()
    
    await pool.new("conv", "username", "Alice", VariableType.STRING, mut=True)
    await pool.new("conv", "score", 100, VariableType.NUMBER, mut=True)
    await pool.new("sys", "message", "Hello", VariableType.STRING, mut=False)
    
    conv_vars = pool.get_all_conversation_vars()
    
    assert "username" in conv_vars
    assert "score" in conv_vars
    assert "message" not in conv_vars
    assert conv_vars["username"] == "Alice"
    assert conv_vars["score"] == 100


@pytest.mark.asyncio
async def test_get_all_node_outputs():
    """测试获取所有节点输出"""
    pool = VariablePool()
    
    await pool.new("node1", "output", "result1", VariableType.STRING, mut=False)
    await pool.new("node2", "output", "result2", VariableType.STRING, mut=False)
    await pool.new("sys", "message", "Hello", VariableType.STRING, mut=False)
    await pool.new("conv", "var", "value", VariableType.STRING, mut=True)
    
    node_outputs = pool.get_all_node_outputs()
    
    assert "node1" in node_outputs
    assert "node2" in node_outputs
    assert "sys" not in node_outputs
    assert "conv" not in node_outputs
    assert node_outputs["node1"]["output"] == "result1"
    assert node_outputs["node2"]["output"] == "result2"


@pytest.mark.asyncio
async def test_get_node_output():
    """测试获取指定节点的输出"""
    pool = VariablePool()
    
    await pool.new("node1", "output", "result", VariableType.STRING, mut=False)
    await pool.new("node1", "status", "success", VariableType.STRING, mut=False)
    
    node_output = pool.get_node_output("node1")
    
    assert node_output["output"] == "result"
    assert node_output["status"] == "success"


@pytest.mark.asyncio
async def test_get_node_output_not_exist_strict():
    """测试获取不存在的节点输出（严格模式）"""
    pool = VariablePool()
    
    with pytest.raises(KeyError) as exc_info:
        pool.get_node_output("nonexistent_node")
    
    assert "output not exist" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_node_output_not_exist_with_default():
    """测试获取不存在的节点输出（使用默认值）"""
    pool = VariablePool()
    
    result = pool.get_node_output("nonexistent_node", defalut=None, strict=False)
    
    assert result is None


# ==================== 复杂场景测试 ====================
@pytest.mark.asyncio
async def test_variable_pool_new_existing_mutable():
    """测试创建已存在的可变变量（应该更新值）"""
    pool = VariablePool()
    
    await pool.new("conv", "counter", 0, VariableType.NUMBER, mut=True)
    await pool.new("conv", "counter", 10, VariableType.NUMBER, mut=True)
    
    assert pool.get_value("conv.counter") == 10


@pytest.mark.asyncio
async def test_variable_pool_new_existing_immutable():
    """测试创建已存在的不可变变量（应该为新值）"""
    pool = VariablePool()
    
    await pool.new("sys", "message", "original", VariableType.STRING, mut=False)
    await pool.new("sys", "message", "modified", VariableType.STRING, mut=False)
    
    # 不可变变量被更新
    assert pool.get_value("sys.message") == "modified"


@pytest.mark.asyncio
async def test_variable_pool_zero_and_false_values():
    """测试零值和 False 值"""
    pool = VariablePool()
    
    await pool.new("conv", "zero", 0, VariableType.NUMBER, mut=True)
    await pool.new("conv", "false", False, VariableType.BOOLEAN, mut=True)
    await pool.new("conv", "empty_str", "", VariableType.STRING, mut=True)
    await pool.new("conv", "empty_arr", [], VariableType.ARRAY_NUMBER, mut=True)
    await pool.new("conv", "empty_obj", {}, VariableType.OBJECT, mut=True)
    
    assert pool.get_value("conv.zero") == 0
    assert pool.get_value("conv.false") is False
    assert pool.get_value("conv.empty_str") == ""
    assert pool.get_value("conv.empty_arr") == []
    assert pool.get_value("conv.empty_obj") == {}


@pytest.mark.asyncio
async def test_variable_pool_nested_objects():
    """测试嵌套对象"""
    pool = VariablePool()
    
    nested_obj = {
        "user": {
            "name": "Alice",
            "age": 25,
            "address": {
                "city": "Beijing"
            }
        },
        "items": [1, 2, 3]
    }
    
    await pool.new("conv", "data", nested_obj, VariableType.OBJECT, mut=True)
    
    result = pool.get_value("conv.data")
    assert result["user"]["name"] == "Alice"
    assert result["user"]["address"]["city"] == "Beijing"
    assert result["items"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_variable_pool_array_of_objects():
    """测试对象数组"""
    pool = VariablePool()
    
    users = [
        {"name": "Alice", "age": 25},
        {"name": "Bob", "age": 30}
    ]
    
    await pool.new("conv", "users", users, VariableType.ARRAY_OBJECT, mut=True)
    
    result = pool.get_value("conv.users")
    assert len(result) == 2
    assert result[0]["name"] == "Alice"
    assert result[1]["age"] == 30


@pytest.mark.asyncio
async def test_variable_pool_to_dict():
    """测试导出为字典"""
    pool = VariablePool()
    
    await pool.new("sys", "message", "Hello", VariableType.STRING, mut=False)
    await pool.new("conv", "username", "Alice", VariableType.STRING, mut=True)
    await pool.new("node1", "output", "result", VariableType.STRING, mut=False)
    
    result = pool.to_dict()
    
    assert "system" in result
    assert "conversation" in result
    assert "nodes" in result
    assert result["system"]["message"] == "Hello"
    assert result["conversation"]["username"] == "Alice"
    assert result["nodes"]["node1"]["output"] == "result"


@pytest.mark.asyncio
async def test_variable_pool_copy():
    """测试复制变量池"""
    pool1 = VariablePool()
    
    await pool1.new("conv", "test", "value", VariableType.STRING, mut=True)
    
    pool2 = VariablePool()
    pool2.copy(pool1)
    
    assert pool2.get_value("conv.test") == "value"
    
    # 修改 pool2 不应影响 pool1
    await pool2.set("conv.test", "modified")
    assert pool2.get_value("conv.test") == "modified"
    assert pool1.get_value("conv.test") == "value"


@pytest.mark.asyncio
async def test_variable_pool_repr():
    """测试字符串表示"""
    pool = VariablePool()
    
    await pool.new("sys", "message", "Hello", VariableType.STRING, mut=False)
    await pool.new("conv", "username", "Alice", VariableType.STRING, mut=True)
    await pool.new("node1", "output", "result", VariableType.STRING, mut=False)
    
    repr_str = repr(pool)
    
    assert "VariablePool" in repr_str
    assert "system_vars=1" in repr_str
    assert "conversation_vars=1" in repr_str
    assert "runtime_vars=1" in repr_str


# ==================== 并发测试 ====================
@pytest.mark.asyncio
async def test_variable_pool_concurrent_set():
    """测试并发设置变量"""
    import asyncio
    
    pool = VariablePool()
    await pool.new("conv", "counter", 0, VariableType.NUMBER, mut=True)
    
    async def increment():
        for _ in range(100):
            current = pool.get_value("conv.counter")
            await pool.set("conv.counter", current + 1)
    
    # 并发执行多个增量操作
    await asyncio.gather(increment(), increment())
    
    # 由于有锁保护，最终值应该是 200
    assert pool.get_value("conv.counter") == 200


# ==================== 边界情况测试 ====================
@pytest.mark.asyncio
async def test_variable_pool_empty():
    """测试空变量池"""
    pool = VariablePool()
    
    assert pool.get_all_system_vars() == {}
    assert pool.get_all_conversation_vars() == {}
    assert pool.get_all_node_outputs() == {}


@pytest.mark.asyncio
async def test_variable_selector_invalid():
    """测试无效的变量选择器"""
    pool = VariablePool()
    
    await pool.new("conv", "test", "value", VariableType.STRING, mut=True)
    
    # 选择器格式错误
    with pytest.raises(ValueError):
        pool.get_value("conv.test.extra")


@pytest.mark.asyncio
async def test_variable_pool_special_characters():
    """测试包含特殊字符的变量名"""
    pool = VariablePool()
    
    # 变量名可以包含下划线、数字等
    await pool.new("conv", "user_name_123", "Alice", VariableType.STRING, mut=True)
    await pool.new("node_1", "output_data", "result", VariableType.STRING, mut=False)
    
    assert pool.get_value("conv.user_name_123") == "Alice"
    assert pool.get_value("node_1.output_data") == "result"


@pytest.mark.asyncio
async def test_variable_pool_large_data():
    """测试大数据量"""
    pool = VariablePool()
    
    # 创建大量变量
    for i in range(100):
        await pool.new("conv", f"var_{i}", i, VariableType.NUMBER, mut=True)
    
    # 验证所有变量都存在
    for i in range(100):
        assert pool.get_value(f"conv.var_{i}") == i
    
    conv_vars = pool.get_all_conversation_vars()
    assert len(conv_vars) == 100


@pytest.mark.asyncio
async def test_variable_pool_different_types_same_name():
    """测试不同命名空间中相同名称的变量"""
    pool = VariablePool()
    
    await pool.new("sys", "value", "system", VariableType.STRING, mut=False)
    await pool.new("conv", "value", "conversation", VariableType.STRING, mut=True)
    await pool.new("node1", "value", "node", VariableType.STRING, mut=False)
    
    assert pool.get_value("sys.value") == "system"
    assert pool.get_value("conv.value") == "conversation"
    assert pool.get_value("node1.value") == "node"


@pytest.mark.asyncio
async def test_variable_pool_update_type():
    """测试更新变量类型"""
    pool = VariablePool()
    
    # 创建字符串变量
    await pool.new("conv", "data", "text", VariableType.STRING, mut=True)
    assert pool.get_value("conv.data") == "text"
    
    # 更新为数字类型变量类型不可变
    with pytest.raises(TypeError):
        await pool.new("conv", "data", 123, VariableType.NUMBER, mut=True)
    assert pool.get_value("conv.data") == "text"


@pytest.mark.asyncio
async def test_variable_pool_array_types():
    """测试不同类型的数组"""
    pool = VariablePool()
    
    await pool.new("conv", "arr_str", ["a", "b", "c"], VariableType.ARRAY_STRING, mut=True)
    await pool.new("conv", "arr_num", [1, 2, 3], VariableType.ARRAY_NUMBER, mut=True)
    await pool.new("conv", "arr_bool", [True, False], VariableType.ARRAY_BOOLEAN, mut=True)
    await pool.new("conv", "arr_obj", [{"id": 1}, {"id": 2}], VariableType.ARRAY_OBJECT, mut=True)
    
    assert pool.get_value("conv.arr_str") == ["a", "b", "c"]
    assert pool.get_value("conv.arr_num") == [1, 2, 3]
    assert pool.get_value("conv.arr_bool") == [True, False]
    assert pool.get_value("conv.arr_obj") == [{"id": 1}, {"id": 2}]


@pytest.mark.asyncio
async def test_variable_pool_namespace_isolation():
    """测试命名空间隔离"""
    pool = VariablePool()
    
    # 在不同命名空间创建变量
    await pool.new("sys", "var1", "sys_value", VariableType.STRING, mut=False)
    await pool.new("conv", "var2", "conv_value", VariableType.STRING, mut=True)
    await pool.new("node1", "var3", "node_value", VariableType.STRING, mut=False)
    
    # 获取各命名空间的变量
    sys_vars = pool.get_all_system_vars()
    conv_vars = pool.get_all_conversation_vars()
    node_outputs = pool.get_all_node_outputs()
    
    # 验证隔离性
    assert "var1" in sys_vars and "var2" not in sys_vars and "var3" not in sys_vars
    assert "var2" in conv_vars and "var1" not in conv_vars and "var3" not in conv_vars
    assert "node1" in node_outputs and "var3" in node_outputs["node1"]


@pytest.mark.asyncio
async def test_variable_pool_mutability_rules():
    """测试可变性规则"""
    pool = VariablePool()
    
    # 系统变量应该是不可变的
    await pool.new("sys", "immutable", "value", VariableType.STRING, mut=False)
    with pytest.raises(KeyError):
        await pool.set("sys.immutable", "new_value")
    
    # 会话变量应该是可变的
    await pool.new("conv", "mutable", "value", VariableType.STRING, mut=True)
    await pool.set("conv.mutable", "new_value")
    assert pool.get_value("conv.mutable") == "new_value"
    
    # 节点输出应该是不可变的
    await pool.new("node1", "output", "value", VariableType.STRING, mut=False)
    with pytest.raises(KeyError):
        await pool.set("node1.output", "new_value")


@pytest.mark.asyncio
async def test_variable_pool_template_variations():
    """测试模板语法的各种变体"""
    pool = VariablePool()
    
    await pool.new("conv", "test", "value", VariableType.STRING, mut=True)
    
    # 各种模板格式都应该工作
    assert pool.get_value("{{conv.test}}") == "value"
    assert pool.get_value("{{ conv.test }}") == "value"
    assert pool.get_value("{{  conv.test  }}") == "value"
    assert pool.get_value("{{ conv.test}}") == "value"
    assert pool.get_value("{{conv.test }}") == "value"
