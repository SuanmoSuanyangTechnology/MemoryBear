"""
手动测试工具函数

由于循环导入问题，使用独立脚本测试工具函数
直接加载模块文件而不通过包导入
"""
import sys
import os

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

def test_text_utils():
    """测试文本工具"""
    print("\n=== 测试文本工具 ===")
    
    # 直接加载模块
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "text_utils",
        os.path.join(project_root, "app/core/memory/utils/text_utils.py")
    )
    text_utils = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(text_utils)
    
    # 测试 escape_lucene_query
    query = "user:admin AND status:active"
    escaped = text_utils.escape_lucene_query(query)
    print(f"✓ escape_lucene_query: '{query}' -> '{escaped}'")
    assert "\\:" in escaped
    
    # 测试 None 输入
    result = text_utils.escape_lucene_query(None)
    print(f"✓ escape_lucene_query(None) -> '{result}'")
    assert result == ""
    
    # 测试 extract_plain_query
    query = "  simple query  "
    result = text_utils.extract_plain_query(query)
    print(f"✓ extract_plain_query: '{query}' -> '{result}'")
    assert result == "simple query"
    
    # 测试字典输入
    query = {"original": "test query", "context": {}}
    result = text_utils.extract_plain_query(query)
    print(f"✓ extract_plain_query(dict) -> '{result}'")
    assert result == "test query"
    
    print("✅ 文本工具测试通过")


def test_time_utils():
    """测试时间工具"""
    print("\n=== 测试时间工具 ===")
    
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "time_utils",
        os.path.join(project_root, "app/core/memory/utils/time_utils.py")
    )
    time_utils = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(time_utils)
    
    # 测试 validate_date_format
    assert time_utils.validate_date_format("2025-10-28") is True
    print("✓ validate_date_format('2025-10-28') -> True")
    
    assert time_utils.validate_date_format("2025/10/28") is False
    print("✓ validate_date_format('2025/10/28') -> False")
    
    # 测试 normalize_date
    result = time_utils.normalize_date("2025/10/28")
    print(f"✓ normalize_date('2025/10/28') -> '{result}'")
    assert result == "2025-10-28"
    
    result = time_utils.normalize_date("2025.10.28")
    print(f"✓ normalize_date('2025.10.28') -> '{result}'")
    assert result == "2025-10-28"
    
    result = time_utils.normalize_date("20251028")
    print(f"✓ normalize_date('20251028') -> '{result}'")
    assert result == "2025-10-28"
    
    # 测试 normalize_date_safe
    # 注意：normalize_date 在无法解析时返回原始字符串，不抛出异常
    # 所以 normalize_date_safe 只有在真正抛出异常时才会返回默认值
    result = time_utils.normalize_date_safe("2025/10/28")
    print(f"✓ normalize_date_safe('2025/10/28') -> '{result}'")
    assert result == "2025-10-28"
    
    print("✅ 时间工具测试通过")


def test_ontology():
    """测试本体定义"""
    print("\n=== 测试本体定义 ===")
    
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ontology",
        os.path.join(project_root, "app/core/memory/utils/ontology.py")
    )
    ontology = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ontology)
    
    # 测试 PREDICATE_DEFINITIONS
    assert isinstance(ontology.PREDICATE_DEFINITIONS, dict)
    assert len(ontology.PREDICATE_DEFINITIONS) > 0
    assert "IS_A" in ontology.PREDICATE_DEFINITIONS
    print(f"✓ PREDICATE_DEFINITIONS 包含 {len(ontology.PREDICATE_DEFINITIONS)} 个谓语")
    
    # 测试 LABEL_DEFINITIONS
    assert isinstance(ontology.LABEL_DEFINITIONS, dict)
    assert "statement_labelling" in ontology.LABEL_DEFINITIONS
    assert "temporal_labelling" in ontology.LABEL_DEFINITIONS
    print("✓ LABEL_DEFINITIONS 包含 statement_labelling 和 temporal_labelling")
    
    # 测试枚举
    assert ontology.Predicate.IS_A == "IS_A"
    assert ontology.StatementType.FACT == "FACT"
    assert ontology.TemporalInfo.STATIC == "STATIC"
    print("✓ 枚举类型正常工作")
    
    print("✅ 本体定义测试通过")


def test_json_schema():
    """测试 JSON Schema"""
    print("\n=== 测试 JSON Schema ===")
    
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "json_schema",
        os.path.join(project_root, "app/core/memory/utils/json_schema.py")
    )
    json_schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(json_schema)
    
    # 测试 BaseDataSchema
    data = json_schema.BaseDataSchema(
        id="test_id",
        statement="test statement",
        group_id="group_1",
        chunk_id="chunk_1",
        created_at="2025-10-28T10:00:00",
    )
    assert data.id == "test_id"
    assert data.statement == "test statement"
    print(f"✓ BaseDataSchema 创建成功: id={data.id}")
    
    # 测试带可选字段
    data = json_schema.BaseDataSchema(
        id="test_id",
        statement="test statement",
        group_id="group_1",
        chunk_id="chunk_1",
        created_at="2025-10-28T10:00:00",
        expired_at="2025-12-31T23:59:59",
        entity_ids=["entity_1", "entity_2"],
    )
    assert data.expired_at == "2025-12-31T23:59:59"
    assert len(data.entity_ids) == 2
    print(f"✓ BaseDataSchema 可选字段正常: entity_ids={len(data.entity_ids)}")
    
    # 测试 ReflexionSchema
    reflexion = json_schema.ReflexionSchema(
        reason="测试原因",
        solution="测试解决方案",
    )
    assert reflexion.reason == "测试原因"
    print(f"✓ ReflexionSchema 创建成功: reason={reflexion.reason}")
    
    print("✅ JSON Schema 测试通过")


def test_messages():
    """测试 API 消息模型"""
    print("\n=== 测试 API 消息模型 ===")
    
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "messages",
        os.path.join(project_root, "app/core/memory/utils/messages.py")
    )
    messages = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(messages)
    
    # 测试 ConfigKey
    key = messages.ConfigKey(
        config_id=123,
        user_id="user_1",
        apply_id="app_1",
    )
    assert key.config_id == 123
    print(f"✓ ConfigKey 创建成功: config_id={key.config_id}")
    
    # 测试 ok 响应
    response = messages.ok(msg="操作成功", data={"result": "success"})
    assert response.code == 0
    assert response.msg == "操作成功"
    assert response.error == ""
    print(f"✓ ok() 响应正常: code={response.code}, msg={response.msg}")
    
    # 测试 fail 响应
    response = messages.fail(msg="操作失败", error_code="ERROR_001")
    assert response.code == 1
    assert response.msg == "操作失败"
    assert response.error == "ERROR_001"
    print(f"✓ fail() 响应正常: code={response.code}, error={response.error}")
    
    print("✅ API 消息模型测试通过")


def test_runtime_overrides():
    """测试运行时配置覆写"""
    print("\n=== 测试运行时配置覆写 ===")
    
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "runtime_overrides_unified",
        os.path.join(project_root, "app/core/memory/utils/runtime_overrides_unified.py")
    )
    runtime_overrides = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime_overrides)
    
    # 测试 _to_bool
    assert runtime_overrides._to_bool(True) is True
    assert runtime_overrides._to_bool(False) is False
    print("✓ _to_bool(bool) 正常")
    
    assert runtime_overrides._to_bool(1) is True
    assert runtime_overrides._to_bool(0) is False
    print("✓ _to_bool(int) 正常")
    
    assert runtime_overrides._to_bool("true") is True
    assert runtime_overrides._to_bool("false") is False
    assert runtime_overrides._to_bool("yes") is True
    assert runtime_overrides._to_bool("no") is False
    print("✓ _to_bool(str) 正常")
    
    # 测试 _set_if_present
    target = {}
    source = {"key": "value"}
    runtime_overrides._set_if_present(target, "target_key", source, "key", str)
    assert target["target_key"] == "value"
    print("✓ _set_if_present() 正常设置值")
    
    target = {}
    source = {}
    runtime_overrides._set_if_present(target, "target_key", source, "key", str)
    assert "target_key" not in target
    print("✓ _set_if_present() 正常处理缺失键")
    
    print("✅ 运行时配置覆写测试通过")


def test_output_paths():
    """测试输出路径"""
    print("\n=== 测试输出路径 ===")
    
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "output_paths",
        os.path.join(project_root, "app/core/memory/utils/output_paths.py")
    )
    output_paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(output_paths)
    
    # 测试 get_output_dir
    output_dir = output_paths.get_output_dir()
    assert "logs" in output_dir or "memory-output" in output_dir
    print(f"✓ get_output_dir() -> {output_dir}")
    
    # 测试 get_output_path
    file_path = output_paths.get_output_path("test.txt")
    assert "memory-output" in file_path
    assert "test.txt" in file_path
    print(f"✓ get_output_path('test.txt') -> {file_path}")
    
    print("✅ 输出路径测试通过")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Memory 工具函数手动测试")
    print("=" * 60)
    
    try:
        test_text_utils()
        test_time_utils()
        test_ontology()
        test_json_schema()
        test_messages()
        test_runtime_overrides()
        test_output_paths()
        
        print("\n" + "=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
