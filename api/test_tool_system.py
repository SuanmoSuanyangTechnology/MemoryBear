#!/usr/bin/env python3
"""
工具管理系统基础测试脚本
用于验证系统的基本功能是否正常
"""

import asyncio
import uuid
from datetime import datetime

# 测试导入
def test_imports():
    """测试模块导入"""
    print("测试模块导入...")
    
    try:
        from app.core.tools.base import BaseTool, ToolResult, ToolParameter, ParameterType
        print("✓ 基础工具模块导入成功")
    except ImportError as e:
        print(f"✗ 基础工具模块导入失败: {e}")
        return False
    
    try:
        from app.core.tools.builtin.datetime_tool import DateTimeTool
        from app.core.tools.builtin.json_tool import JsonTool
        print("✓ 内置工具模块导入成功")
    except ImportError as e:
        print(f"✗ 内置工具模块导入失败: {e}")
        return False
    
    try:
        from app.core.tools.langchain_adapter import LangchainAdapter
        print("✓ Langchain适配器导入成功")
    except ImportError as e:
        print(f"✗ Langchain适配器导入失败: {e}")
        return False
    
    try:
        from app.models.tool_model import ToolConfig, ToolType, ToolStatus
        print("✓ 工具模型导入成功")
    except ImportError as e:
        print(f"✗ 工具模型导入失败: {e}")
        return False
    
    try:
        from app.core.tools.custom import CustomTool, OpenAPISchemaParser, AuthManager
        print("✓ 自定义工具模块导入成功")
    except ImportError as e:
        print(f"✗ 自定义工具模块导入失败: {e}")
        return False
    
    try:
        from app.core.tools.mcp import MCPTool, MCPClient, MCPServiceManager
        print("✓ MCP工具模块导入成功")
    except ImportError as e:
        print(f"✗ MCP工具模块导入失败: {e}")
        return False
    
    return True


def test_tool_creation():
    """测试工具创建"""
    print("\n测试工具创建...")
    
    try:
        from app.core.tools.builtin.datetime_tool import DateTimeTool
        
        # 创建时间工具实例（全局工具）
        tool_id = str(uuid.uuid4())
        config = {
            "parameters": {"timezone": "UTC"},
            "tenant_id": None,  # 全局工具
            "version": "1.0.0",
            "tags": ["time", "utility", "builtin"]
        }
        
        datetime_tool = DateTimeTool(tool_id, config)
        
        # 验证工具属性
        assert datetime_tool.name == "datetime_tool"
        assert datetime_tool.tool_type.value == "builtin"
        assert len(datetime_tool.parameters) > 0
        
        print("✓ 时间工具创建成功（全局工具）")
        return True
        
    except Exception as e:
        print(f"✗ 工具创建失败: {e}")
        return False


async def test_tool_execution():
    """测试工具执行"""
    print("\n测试工具执行...")
    
    try:
        from app.core.tools.builtin.datetime_tool import DateTimeTool
        
        # 创建时间工具实例
        tool_id = str(uuid.uuid4())
        config = {
            "parameters": {"timezone": "UTC"},
            "tenant_id": None,  # 全局工具
            "version": "1.0.0"
        }
        
        datetime_tool = DateTimeTool(tool_id, config)
        
        # 测试获取当前时间
        result = await datetime_tool.safe_execute(operation="now")
        
        assert result.success == True
        assert "datetime" in result.data
        assert result.execution_time > 0
        
        print("✓ 工具执行成功")
        print(f"  执行时间: {result.execution_time:.3f}秒")
        print(f"  返回数据: {result.data}")
        
        return True
        
    except Exception as e:
        print(f"✗ 工具执行失败: {e}")
        return False


def test_langchain_adapter():
    """测试Langchain适配器"""
    print("\n测试Langchain适配器...")
    
    try:
        from app.core.tools.builtin.json_tool import JsonTool
        from app.core.tools.langchain_adapter import LangchainAdapter
        
        # 创建JSON工具实例
        tool_id = str(uuid.uuid4())
        config = {
            "parameters": {"indent": 2},
            "tenant_id": None,  # 全局工具
            "version": "1.0.0"
        }
        
        json_tool = JsonTool(tool_id, config)
        
        # 验证Langchain兼容性
        is_compatible, issues = LangchainAdapter.validate_langchain_compatibility(json_tool)
        
        if not is_compatible:
            print(f"✗ Langchain兼容性验证失败: {issues}")
            return False
        
        # 创建工具描述
        description = LangchainAdapter.create_tool_description(json_tool)
        
        assert "name" in description
        assert "parameters" in description
        assert description["langchain_compatible"] == True
        
        print("✓ Langchain适配器测试成功")
        return True
        
    except Exception as e:
        print(f"✗ Langchain适配器测试失败: {e}")
        return False


def test_config_manager():
    """测试配置管理器"""
    print("\n测试配置管理器...")
    
    try:
        from app.core.tools.config_manager import ConfigManager
        
        # 创建配置管理器
        config_manager = ConfigManager()
        
        # 获取配置摘要
        summary = config_manager.get_config_summary()
        
        assert "config_dir" in summary
        assert "total_configs" in summary
        
        print("✓ 配置管理器测试成功")
        print(f"  配置目录: {summary['config_dir']}")
        print(f"  总配置数: {summary['total_configs']}")
        
        return True
        
    except Exception as e:
        print(f"✗ 配置管理器测试失败: {e}")
        return False


def test_schema_parser():
    """测试OpenAPI Schema解析器"""
    print("\n测试OpenAPI Schema解析器...")
    
    try:
        from app.core.tools.custom.schema_parser import OpenAPISchemaParser
        
        # 创建解析器
        parser = OpenAPISchemaParser()
        
        # 测试简单的OpenAPI schema
        test_schema = {
            "openapi": "3.0.0",
            "info": {
                "title": "Test API",
                "version": "1.0.0",
                "description": "测试API"
            },
            "paths": {
                "/test": {
                    "get": {
                        "summary": "测试接口",
                        "operationId": "test_operation",
                        "responses": {
                            "200": {
                                "description": "成功"
                            }
                        }
                    }
                }
            }
        }
        
        # 验证schema
        is_valid, error_msg = parser.validate_schema(test_schema)
        assert is_valid, f"Schema验证失败: {error_msg}"
        
        # 提取工具信息
        tool_info = parser.extract_tool_info(test_schema)
        assert tool_info["name"] == "Test API"
        assert "test_operation" in tool_info["operations"]
        
        print("✓ OpenAPI Schema解析器测试成功")
        return True
        
    except Exception as e:
        print(f"✗ OpenAPI Schema解析器测试失败: {e}")
        return False


def test_auth_manager():
    """测试认证管理器"""
    print("\n测试认证管理器...")
    
    try:
        from app.core.tools.custom.auth_manager import AuthManager
        from app.models.tool_model import AuthType
        
        # 创建认证管理器
        auth_manager = AuthManager()
        
        # 测试API Key认证配置
        api_key_config = {
            "api_key": "test-key-123",
            "key_name": "X-API-Key",
            "location": "header"
        }
        
        is_valid, error_msg = auth_manager.validate_auth_config(AuthType.API_KEY, api_key_config)
        assert is_valid, f"API Key配置验证失败: {error_msg}"
        
        # 测试Bearer Token认证配置
        bearer_config = {
            "token": "bearer-token-123"
        }
        
        is_valid, error_msg = auth_manager.validate_auth_config(AuthType.BEARER_TOKEN, bearer_config)
        assert is_valid, f"Bearer Token配置验证失败: {error_msg}"
        
        # 测试认证应用
        url = "https://api.example.com/test"
        headers = {}
        params = {}
        
        new_url, new_headers, new_params = auth_manager.apply_authentication(
            AuthType.API_KEY, api_key_config, url, headers, params
        )
        
        assert "X-API-Key" in new_headers
        assert new_headers["X-API-Key"] == "test-key-123"
        
        print("✓ 认证管理器测试成功")
        return True
        
    except Exception as e:
        print(f"✗ 认证管理器测试失败: {e}")
        return False


def test_builtin_initializer():
    """测试内置工具初始化器"""
    print("\n测试内置工具初始化器...")
    
    try:
        from app.core.tools.builtin_initializer import BuiltinToolInitializer
        
        # 注意：这里不能真正初始化，因为需要数据库连接
        # 只测试类的创建和基本方法
        
        # 模拟数据库会话（实际使用中需要真实的数据库连接）
        class MockDB:
            def query(self, *args):
                return self
            def filter(self, *args):
                return self
            def first(self):
                return None
            def all(self):
                return []
        
        mock_db = MockDB()
        initializer = BuiltinToolInitializer(mock_db)
        
        # 测试获取内置工具状态（会返回空列表，因为没有真实数据）
        status = initializer.get_builtin_tools_status()
        assert isinstance(status, list)
        
        print("✓ 内置工具初始化器测试成功")
        return True
        
    except Exception as e:
        print(f"✗ 内置工具初始化器测试失败: {e}")
        return False


async def main():
    """主测试函数"""
    print("=" * 50)
    print("工具管理系统基础测试")
    print("=" * 50)
    
    tests = [
        ("模块导入", test_imports),
        ("工具创建", test_tool_creation),
        ("工具执行", test_tool_execution),
        ("Langchain适配", test_langchain_adapter),
        ("配置管理", test_config_manager),
        ("Schema解析器", test_schema_parser),
        ("认证管理器", test_auth_manager),
        ("内置工具初始化器", test_builtin_initializer)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func()
            else:
                result = test_func()
            
            if result:
                passed += 1
        except Exception as e:
            print(f"✗ {test_name}测试异常: {e}")
    
    print("\n" + "=" * 50)
    print(f"测试结果: {passed}/{total} 通过")
    
    if passed == total:
        print("🎉 所有基础测试通过！工具管理系统基本功能正常。")
        return True
    else:
        print("⚠️  部分测试失败，请检查相关模块。")
        return False


if __name__ == "__main__":
    asyncio.run(main())