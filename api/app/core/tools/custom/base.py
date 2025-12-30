"""自定义工具基类"""
import time
from typing import Dict, Any, List, Optional
import aiohttp
from urllib.parse import urljoin

from app.models.tool_model import ToolType, AuthType
from app.core.tools.base import BaseTool
from app.schemas.tool_schema import ToolParameter, ToolResult, ParameterType
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class CustomTool(BaseTool):
    """自定义工具 - 基于OpenAPI schema的工具"""
    
    def __init__(self, tool_id: str, config: Dict[str, Any]):
        """初始化自定义工具
        
        Args:
            tool_id: 工具ID
            config: 工具配置
        """
        super().__init__(tool_id, config)
        self.schema_content = config.get("schema_content", {})
        self.schema_url = config.get("schema_url")
        self.auth_type = AuthType(config.get("auth_type", "none"))
        self.auth_config = config.get("auth_config", {})
        self.base_url = config.get("base_url", "")
        self.timeout = config.get("timeout", 30)
        
        # 解析schema
        self._parsed_operations = self._parse_openapi_schema()
    
    @property
    def name(self) -> str:
        """工具名称"""
        if self.schema_content:
            info = self.schema_content.get("info", {})
            return info.get("title", f"custom_tool_{self.tool_id[:8]}")
        return f"custom_tool_{self.tool_id[:8]}"
    
    @property
    def description(self) -> str:
        """工具描述"""
        if self.schema_content:
            info = self.schema_content.get("info", {})
            return info.get("description", "自定义API工具")
        return "自定义API工具"
    
    @property
    def tool_type(self) -> ToolType:
        """工具类型"""
        return ToolType.CUSTOM
    
    @property
    def parameters(self) -> List[ToolParameter]:
        """工具参数定义"""
        params = []
        
        # 添加操作选择参数
        if len(self._parsed_operations) > 1:
            params.append(ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="要执行的操作",
                required=True,
                enum=list(self._parsed_operations.keys())
            ))
        
        # 添加通用参数（基于第一个操作的参数）
        if self._parsed_operations:
            first_operation = next(iter(self._parsed_operations.values()))
            for param_name, param_info in first_operation.get("parameters", {}).items():
                params.append(ToolParameter(
                    name=param_name,
                    type=self._convert_openapi_type(param_info.get("type", "string")),
                    description=param_info.get("description", ""),
                    required=param_info.get("required", False),
                    default=param_info.get("default"),
                    enum=param_info.get("enum"),
                    minimum=param_info.get("minimum"),
                    maximum=param_info.get("maximum"),
                    pattern=param_info.get("pattern")
                ))
        
        return params
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行自定义工具"""
        start_time = time.time()
        
        try:
            # 确定要执行的操作
            operation_name = kwargs.get("operation")
            if not operation_name and len(self._parsed_operations) == 1:
                operation_name = next(iter(self._parsed_operations.keys()))
            
            if not operation_name or operation_name not in self._parsed_operations:
                raise ValueError(f"无效的操作: {operation_name}")
            
            operation = self._parsed_operations[operation_name]
            
            # 构建请求
            url = self._build_request_url(operation, kwargs)
            headers = self._build_request_headers(operation)
            data = self._build_request_data(operation, kwargs)
            
            # 发送HTTP请求
            result = await self._send_http_request(
                method=operation["method"],
                url=url,
                headers=headers,
                data=data
            )
            
            execution_time = time.time() - start_time
            return ToolResult.success_result(
                data=result,
                execution_time=execution_time
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="CUSTOM_TOOL_ERROR",
                execution_time=execution_time
            )
    
    def _parse_openapi_schema(self) -> Dict[str, Any]:
        """解析OpenAPI schema"""
        operations = {}
        
        if not self.schema_content:
            return operations
        
        paths = self.schema_content.get("paths", {})
        
        for path, path_item in paths.items():
            for method, operation in path_item.items():
                if method.lower() in ["get", "post", "put", "delete", "patch"]:
                    operation_id = operation.get("operationId", f"{method}_{path.replace('/', '_')}")
                    
                    # 解析参数
                    parameters = {}
                    if "parameters" in operation:
                        for param in operation["parameters"]:
                            param_name = param.get("name")
                            param_schema = param.get("schema", {})
                            parameters[param_name] = {
                                "type": param_schema.get("type", "string"),
                                "description": param.get("description", ""),
                                "required": param.get("required", False),
                                "in": param.get("in", "query"),
                                **param_schema
                            }
                    
                    # 解析请求体
                    request_body = None
                    if "requestBody" in operation:
                        content = operation["requestBody"].get("content", {})
                        if "application/json" in content:
                            request_body = content["application/json"].get("schema", {})
                    
                    operations[operation_id] = {
                        "method": method.upper(),
                        "path": path,
                        "summary": operation.get("summary", ""),
                        "description": operation.get("description", ""),
                        "parameters": parameters,
                        "request_body": request_body
                    }
        
        return operations

    @staticmethod
    def _convert_openapi_type(openapi_type: str) -> ParameterType:
        """转换OpenAPI类型到内部类型"""
        type_mapping = {
            "string": ParameterType.STRING,
            "integer": ParameterType.INTEGER,
            "number": ParameterType.NUMBER,
            "boolean": ParameterType.BOOLEAN,
            "array": ParameterType.ARRAY,
            "object": ParameterType.OBJECT
        }
        return type_mapping.get(openapi_type, ParameterType.STRING)
    
    def _build_request_url(self, operation: Dict[str, Any], params: Dict[str, Any]) -> str:
        """构建请求URL"""
        path = operation["path"]
        
        # 替换路径参数
        for param_name, param_info in operation.get("parameters", {}).items():
            if param_info.get("in") == "path" and param_name in params:
                path = path.replace(f"{{{param_name}}}", str(params[param_name]))
        
        # 构建完整URL
        if self.base_url:
            url = urljoin(self.base_url, path.lstrip("/"))
        else:
            # 从schema中获取服务器URL
            servers = self.schema_content.get("servers", [])
            if servers:
                base_url = servers[0].get("url", "")
                url = urljoin(base_url, path.lstrip("/"))
            else:
                url = path
        
        # 添加查询参数
        query_params = {}
        for param_name, param_info in operation.get("parameters", {}).items():
            if param_info.get("in") == "query" and param_name in params:
                query_params[param_name] = params[param_name]
        
        if query_params:
            from urllib.parse import urlencode
            url += "?" + urlencode(query_params)
        
        return url
    
    def _build_request_headers(self, operation: Dict[str, Any]) -> Dict[str, str]:
        """构建请求头"""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "CustomTool/1.0"
        }
        
        # 添加认证头
        if self.auth_type == AuthType.API_KEY:
            api_key = self.auth_config.get("api_key")
            key_name = self.auth_config.get("key_name", "X-API-Key")
            if api_key:
                headers[key_name] = api_key
        
        elif self.auth_type == AuthType.BEARER_TOKEN:
            token = self.auth_config.get("token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        
        return headers

    @staticmethod
    def _build_request_data(operation: Dict[str, Any], params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """构建请求数据"""
        if operation["method"] in ["POST", "PUT", "PATCH"]:
            request_body = operation.get("request_body")
            if request_body:
                # 构建请求体数据
                data = {}
                properties = request_body.get("properties", {})
                
                for prop_name, prop_schema in properties.items():
                    if prop_name in params:
                        data[prop_name] = params[prop_name]
                
                return data if data else None
        
        return None
    
    async def _send_http_request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Dict[str, Any]] = None
    ) -> Any:
        """发送HTTP请求"""
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            kwargs = {
                "headers": headers
            }
            
            if data and method in ["POST", "PUT", "PATCH"]:
                kwargs["json"] = data
            
            async with session.request(method, url, **kwargs) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    raise Exception(f"HTTP {response.status}: {error_text}")
                
                # 尝试解析JSON响应
                try:
                    return await response.json()
                except Exception as e:
                    logger.error(f"解析HTTP响应JSON失败: {str(e)}")
                    return await response.text()
    
    @classmethod
    def from_url(cls, schema_url: str, auth_config: Dict[str, Any], tool_id: str = None) -> 'CustomTool':
        """从URL导入OpenAPI schema创建工具"""
        import uuid
        if not tool_id:
            tool_id = str(uuid.uuid4())
        
        config = {
            "schema_url": schema_url,
            "auth_config": auth_config,
            "auth_type": auth_config.get("type", "none")
        }
        
        # 这里应该异步加载schema，为了简化暂时返回空配置
        return cls(tool_id, config)
    
    @classmethod
    def from_schema(cls, schema_dict: Dict[str, Any], auth_config: Dict[str, Any], tool_id: str = None) -> 'CustomTool':
        """从schema字典创建工具"""
        import uuid
        if not tool_id:
            tool_id = str(uuid.uuid4())
        
        config = {
            "schema_content": schema_dict,
            "auth_config": auth_config,
            "auth_type": auth_config.get("type", "none")
        }
        
        return cls(tool_id, config)