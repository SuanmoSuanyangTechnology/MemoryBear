"""OpenAPI Schema解析器"""
import json
import yaml
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse
import aiohttp
import asyncio

from app.core.logging_config import get_business_logger

logger = get_business_logger()

# 为了兼容性，创建别名
# SchemaParser = OpenAPISchemaParser = None


class OpenAPISchemaParser:
    """OpenAPI Schema解析器 - 解析OpenAPI 3.0规范"""
    
    def __init__(self):
        """初始化解析器"""
        self.supported_versions = ["3.0.0", "3.0.1", "3.0.2", "3.0.3", "3.1.0"]
    
    async def parse_from_url(self, schema_url: str, timeout: int = 30) -> Tuple[bool, Dict[str, Any], str]:
        """从URL解析OpenAPI schema
        
        Args:
            schema_url: Schema URL
            timeout: 超时时间（秒）
            
        Returns:
            (是否成功, schema内容, 错误信息)
        """
        try:
            # 验证URL格式
            parsed_url = urlparse(schema_url)
            if not parsed_url.scheme or not parsed_url.netloc:
                return False, {}, "无效的URL格式"
            
            # 下载schema
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(schema_url) as response:
                    if response.status != 200:
                        return False, {}, f"HTTP错误: {response.status}"
                    
                    content_type = response.headers.get('content-type', '').lower()
                    content = await response.text()
                    
                    # 解析内容
                    schema_dict = self._parse_content(content, content_type)
                    if not schema_dict:
                        return False, {}, "无法解析schema内容"
                    
                    # 验证schema
                    is_valid, error_msg = self.validate_schema(schema_dict)
                    if not is_valid:
                        return False, {}, error_msg
                    
                    return True, schema_dict, ""
                    
        except asyncio.TimeoutError:
            return False, {}, "请求超时"
        except Exception as e:
            logger.error(f"从URL解析schema失败: {schema_url}, 错误: {e}")
            return False, {}, str(e)
    
    def parse_from_content(self, content: str, content_type: str = "application/json") -> Tuple[bool, Dict[str, Any], str]:
        """从内容解析OpenAPI schema
        
        Args:
            content: Schema内容
            content_type: 内容类型
            
        Returns:
            (是否成功, schema内容, 错误信息)
        """
        try:
            # 解析内容
            schema_dict = self._parse_content(content, content_type)
            if not schema_dict:
                return False, {}, "无法解析schema内容"
            
            # 验证schema
            is_valid, error_msg = self.validate_schema(schema_dict)
            if not is_valid:
                return False, {}, error_msg
            
            return True, schema_dict, ""
            
        except Exception as e:
            logger.error(f"解析schema内容失败: {e}")
            return False, {}, str(e)

    @staticmethod
    def _parse_content(content: str, content_type: str) -> Optional[Dict[str, Any]]:
        """解析内容为字典
        
        Args:
            content: 内容字符串
            content_type: 内容类型
            
        Returns:
            解析后的字典，失败返回None
        """
        try:
            # 根据内容类型解析
            if 'application/json' in content_type:
                return json.loads(content)
            elif 'yaml' in content_type or 'yml' in content_type:
                return yaml.safe_load(content)
            else:
                # 尝试自动检测格式
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    try:
                        return yaml.safe_load(content)
                    except yaml.YAMLError:
                        return None
        except Exception as e:
            logger.error(f"解析内容失败: {e}")
            return None
    
    def validate_schema(self, schema_dict: Dict[str, Any]) -> Tuple[bool, str]:
        """验证OpenAPI schema
        
        Args:
            schema_dict: Schema字典
            
        Returns:
            (是否有效, 错误信息)
        """
        try:
            # 检查基本结构
            if not isinstance(schema_dict, dict):
                return False, "Schema必须是JSON对象"
            
            # 检查OpenAPI版本
            openapi_version = schema_dict.get("openapi")
            if not openapi_version:
                return False, "缺少openapi版本字段"
            
            if openapi_version not in self.supported_versions:
                return False, f"不支持的OpenAPI版本: {openapi_version}"
            
            # 检查必需字段
            required_fields = ["info", "paths"]
            for field in required_fields:
                if field not in schema_dict:
                    return False, f"缺少必需字段: {field}"
            
            # 验证info字段
            info = schema_dict.get("info", {})
            if not isinstance(info, dict):
                return False, "info字段必须是对象"
            
            if "title" not in info:
                return False, "info.title字段是必需的"
            
            # 验证paths字段
            paths = schema_dict.get("paths", {})
            if not isinstance(paths, dict):
                return False, "paths字段必须是对象"
            
            # 验证至少有一个路径
            if not paths:
                return False, "至少需要定义一个API路径"
            
            return True, ""
            
        except Exception as e:
            return False, f"验证schema时出错: {e}"
    
    def extract_tool_info(self, schema_dict: Dict[str, Any]) -> Dict[str, Any]:
        """从schema提取工具信息
        
        Args:
            schema_dict: Schema字典
            
        Returns:
            工具信息字典
        """
        info = schema_dict.get("info", {})
        
        return {
            "name": info.get("title", "Custom API Tool"),
            "description": info.get("description", ""),
            "version": info.get("version", "1.0.0"),
            "servers": schema_dict.get("servers", []),
            "operations": self._extract_operations(schema_dict)
        }
    
    def _extract_operations(self, schema_dict: Dict[str, Any]) -> Dict[str, Any]:
        """提取API操作信息
        
        Args:
            schema_dict: Schema字典
            
        Returns:
            操作信息字典
        """
        operations = {}
        paths = schema_dict.get("paths", {})
        
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            
            for method, operation in path_item.items():
                if method.lower() not in ["get", "post", "put", "delete", "patch", "head", "options"]:
                    continue
                
                if not isinstance(operation, dict):
                    continue
                
                # 生成操作ID
                operation_id = operation.get("operationId")
                if not operation_id:
                    operation_id = f"{method.lower()}_{path.replace('/', '_').replace('{', '').replace('}', '')}"
                
                # 提取操作信息
                operations[operation_id] = {
                    "method": method.upper(),
                    "path": path,
                    "summary": operation.get("summary", ""),
                    "description": operation.get("description", ""),
                    "parameters": self._extract_parameters(operation),
                    "request_body": self._extract_request_body(operation),
                    "responses": self._extract_responses(operation),
                    "tags": operation.get("tags", [])
                }
        
        return operations

    @staticmethod
    def _extract_parameters(operation: Dict[str, Any]) -> Dict[str, Any]:
        """提取操作参数
        
        Args:
            operation: 操作定义
            
        Returns:
            参数信息字典
        """
        parameters = {}
        
        for param in operation.get("parameters", []):
            if not isinstance(param, dict):
                continue
            
            param_name = param.get("name")
            if not param_name:
                continue
            
            param_schema = param.get("schema", {})
            
            parameters[param_name] = {
                "name": param_name,
                "in": param.get("in", "query"),
                "description": param.get("description", ""),
                "required": param.get("required", False),
                "type": param_schema.get("type", "string"),
                "format": param_schema.get("format"),
                "enum": param_schema.get("enum"),
                "default": param_schema.get("default"),
                "minimum": param_schema.get("minimum"),
                "maximum": param_schema.get("maximum"),
                "pattern": param_schema.get("pattern"),
                "example": param.get("example") or param_schema.get("example")
            }
        
        return parameters

    @staticmethod
    def _extract_request_body(operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """提取请求体信息
        
        Args:
            operation: 操作定义
            
        Returns:
            请求体信息，如果没有返回None
        """
        request_body = operation.get("requestBody")
        if not request_body:
            return None
        
        content = request_body.get("content", {})
        
        # 优先使用application/json
        if "application/json" in content:
            schema = content["application/json"].get("schema", {})
        elif content:
            # 使用第一个可用的内容类型
            first_content_type = next(iter(content.keys()))
            schema = content[first_content_type].get("schema", {})
        else:
            return None
        
        return {
            "description": request_body.get("description", ""),
            "required": request_body.get("required", False),
            "schema": schema,
            "content_types": list(content.keys())
        }

    @staticmethod
    def _extract_responses(operation: Dict[str, Any]) -> Dict[str, Any]:
        """提取响应信息
        
        Args:
            operation: 操作定义
            
        Returns:
            响应信息字典
        """
        responses = {}
        
        for status_code, response in operation.get("responses", {}).items():
            if not isinstance(response, dict):
                continue
            
            content = response.get("content", {})
            schema = None
            
            # 尝试获取响应schema
            if "application/json" in content:
                schema = content["application/json"].get("schema")
            elif content:
                first_content_type = next(iter(content.keys()))
                schema = content[first_content_type].get("schema")
            
            responses[status_code] = {
                "description": response.get("description", ""),
                "schema": schema,
                "content_types": list(content.keys()) if content else []
            }
        
        return responses

    @staticmethod
    def generate_tool_parameters(operations: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成工具参数定义
        
        Args:
            operations: 操作信息字典
            
        Returns:
            参数定义列表
        """
        parameters = []
        
        # 如果有多个操作，添加操作选择参数
        if len(operations) > 1:
            parameters.append({
                "name": "operation",
                "type": "string",
                "description": "要执行的操作",
                "required": True,
                "enum": list(operations.keys())
            })
        
        # 收集所有参数（去重）
        all_params = {}
        
        for operation_id, operation in operations.items():
            # 路径参数和查询参数
            for param_name, param_info in operation.get("parameters", {}).items():
                if param_name not in all_params:
                    all_params[param_name] = {
                        "name": param_name,
                        "type": param_info.get("type", "string"),
                        "description": param_info.get("description", ""),
                        "required": param_info.get("required", False),
                        "enum": param_info.get("enum"),
                        "default": param_info.get("default"),
                        "minimum": param_info.get("minimum"),
                        "maximum": param_info.get("maximum"),
                        "pattern": param_info.get("pattern")
                    }
            
            # 请求体参数
            request_body = operation.get("request_body")
            if request_body:
                schema = request_body.get("schema", {})
                properties = schema.get("properties", {})
                
                for prop_name, prop_schema in properties.items():
                    if prop_name not in all_params:
                        all_params[prop_name] = {
                            "name": prop_name,
                            "type": prop_schema.get("type", "string"),
                            "description": prop_schema.get("description", ""),
                            "required": prop_name in schema.get("required", []),
                            "enum": prop_schema.get("enum"),
                            "default": prop_schema.get("default"),
                            "minimum": prop_schema.get("minimum"),
                            "maximum": prop_schema.get("maximum"),
                            "pattern": prop_schema.get("pattern")
                        }
        
        # 转换为参数列表
        parameters.extend(all_params.values())
        
        return parameters

    def validate_operation_parameters(self, operation: Dict[str, Any], params: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """验证操作参数
        
        Args:
            operation: 操作定义
            params: 输入参数
            
        Returns:
            (是否有效, 错误信息列表)
        """
        errors = []
        
        # 验证路径参数和查询参数
        for param_name, param_info in operation.get("parameters", {}).items():
            if param_info.get("required", False) and param_name not in params:
                errors.append(f"缺少必需参数: {param_name}")
            
            if param_name in params:
                value = params[param_name]
                param_type = param_info.get("type", "string")
                
                # 类型验证
                if not self._validate_parameter_type(value, param_type):
                    errors.append(f"参数 {param_name} 类型错误，期望: {param_type}")
                
                # 枚举验证
                enum_values = param_info.get("enum")
                if enum_values and value not in enum_values:
                    errors.append(f"参数 {param_name} 值无效，必须是: {enum_values}")
        
        # 验证请求体参数
        request_body = operation.get("request_body")
        if request_body:
            schema = request_body.get("schema", {})
            required_props = schema.get("required", [])
            properties = schema.get("properties", {})
            
            for prop_name in required_props:
                if prop_name not in params:
                    errors.append(f"缺少必需的请求体参数: {prop_name}")
            
            for prop_name, value in params.items():
                if prop_name in properties:
                    prop_schema = properties[prop_name]
                    prop_type = prop_schema.get("type", "string")
                    
                    if not self._validate_parameter_type(value, prop_type):
                        errors.append(f"请求体参数 {prop_name} 类型错误，期望: {prop_type}")
        
        return len(errors) == 0, errors

    @staticmethod
    def _validate_parameter_type(value: Any, expected_type: str) -> bool:
        """验证参数类型
        
        Args:
            value: 参数值
            expected_type: 期望类型
            
        Returns:
            是否类型匹配
        """
        if value is None:
            return True
        
        type_mapping = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict
        }
        
        expected_python_type = type_mapping.get(expected_type)
        if expected_python_type:
            return isinstance(value, expected_python_type)
        
        return True

# 为了兼容性，创建别名
SchemaParser = OpenAPISchemaParser