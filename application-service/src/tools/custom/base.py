"""Custom tool base class."""
import json
import logging
import time
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin

import aiohttp

from ...models.tool_model import ToolType, AuthType
from ..base import BaseTool
from ...schemas.tool_schema import ToolParameter, ToolResult, ParameterType

logger = logging.getLogger(__name__)


class CustomTool(BaseTool):
    """Custom tool - a tool based on an OpenAPI schema."""

    def __init__(self, tool_id: str, config: Dict[str, Any]):
        """Initialize the custom tool

        Args:
            tool_id: Tool ID
            config: Tool configuration
        """
        super().__init__(tool_id, config)
        self.schema_content = config.get("schema_content", {})
        self.schema_url = config.get("schema_url")
        self.auth_type = AuthType(config.get("auth_type", "none"))
        self.auth_config = config.get("auth_config", {})
        self.base_url = config.get("base_url", "")
        self.timeout = config.get("timeout", 30)

        # Parse the schema
        self._parsed_operations = self._parse_openapi_schema()

    @property
    def name(self) -> str:
        """Tool name."""
        if self.schema_content:
            info = self.schema_content.get("info", {})
            return info.get("title", f"custom_tool_{self.tool_id[:8]}")
        return f"custom_tool_{self.tool_id[:8]}"

    @property
    def description(self) -> str:
        """Tool description."""
        if self.schema_content:
            info = self.schema_content.get("info", {})
            return info.get("description", "自定义API工具")
        return "自定义API工具"

    @property
    def tool_type(self) -> ToolType:
        """Tool type."""
        return ToolType.CUSTOM

    @property
    def parameters(self) -> List[ToolParameter]:
        """Tool parameter definitions."""
        params = []

        # Add the operation selection parameter
        if len(self._parsed_operations) > 1:
            params.append(ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="要执行的操作",
                required=True,
                enum=list(self._parsed_operations.keys())
            ))

        # Add common parameters (based on the first operation's parameters)
        if self._parsed_operations:
            first_operation = next(iter(self._parsed_operations.values()))
            # path/query parameters
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
            # requestBody parameters - flatten body fields into individual parameters for the model
            request_body = first_operation.get("request_body")
            if request_body:
                body_schema = request_body.get("properties", {})
                required_fields = request_body.get("required", [])
                for prop_name, prop_schema in body_schema.items():
                    params.append(ToolParameter(
                        name=prop_name,
                        type=self._convert_openapi_type(prop_schema.get("type", "string")),
                        description=prop_schema.get("description", ""),
                        required=prop_name in required_fields,
                        default=prop_schema.get("default"),
                        enum=prop_schema.get("enum"),
                        minimum=prop_schema.get("minimum"),
                        maximum=prop_schema.get("maximum"),
                        pattern=prop_schema.get("pattern")
                    ))

        return params

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the custom tool."""
        start_time = time.time()

        try:
            # Determine the operation to execute
            operation_name = kwargs.get("operation")
            if not operation_name and len(self._parsed_operations) == 1:
                operation_name = next(iter(self._parsed_operations.keys()))

            if not operation_name or operation_name not in self._parsed_operations:
                raise ValueError(f"无效的操作: {operation_name}")

            operation = self._parsed_operations[operation_name]

            # Build the request
            url = self._build_request_url(operation, kwargs)
            headers = self._build_request_headers(operation)
            data = self._build_request_data(operation, kwargs)

            # Send the HTTP request
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
        """Parse the OpenAPI schema."""
        operations = {}

        if not self.schema_content:
            return operations

        if isinstance(self.schema_content, str):
            try:
                self.schema_content = json.loads(self.schema_content)
            except json.JSONDecodeError:
                logger.error(f"无效的OpenAPI schema: {self.schema_content}")
                return operations

        paths = self.schema_content.get("paths", {})

        for path, path_item in paths.items():
            for method, operation in path_item.items():
                if method.lower() in ["get", "post", "put", "delete", "patch"]:
                    operation_id = operation.get("operationId", f"{method}_{path.replace('/', '_')}")

                    # Parse parameters
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

                    # Parse the request body
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
        """Convert an OpenAPI type to the internal type."""
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
        """Build the request URL."""
        path = operation["path"]

        # Substitute path parameters
        for param_name, param_info in operation.get("parameters", {}).items():
            if param_info.get("in") == "path" and param_name in params:
                path = path.replace(f"{{{param_name}}}", str(params[param_name]))

        # Build the full URL
        if self.base_url:
            url = urljoin(self.base_url, path.lstrip("/"))
        else:
            # Take the server URL from the schema
            servers = self.schema_content.get("servers", [])
            if servers:
                base_url = servers[0].get("url", "")
                url = urljoin(base_url, path.lstrip("/"))
            else:
                url = path

        # Add query parameters
        query_params = {}
        for param_name, param_info in operation.get("parameters", {}).items():
            if param_info.get("in") == "query" and param_name in params:
                query_params[param_name] = params[param_name]

        if query_params:
            from urllib.parse import urlencode
            url += "?" + urlencode(query_params)

        return url

    def _build_request_headers(self, operation: Dict[str, Any]) -> Dict[str, str]:
        """Build the request headers."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "CustomTool/1.0"
        }

        # Add auth headers
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
        """Build the request body data."""
        if operation["method"] in ["POST", "PUT", "PATCH"]:
            request_body = operation.get("request_body")
            if request_body:
                # Build the request body data
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
        """Send an HTTP request."""
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

                # Try to parse the response as JSON
                try:
                    return await response.json()
                except Exception as e:
                    logger.error(f"解析HTTP响应JSON失败: {str(e)}")
                    return await response.text()

    @classmethod
    def from_url(cls, schema_url: str, auth_config: Dict[str, Any], tool_id: str = None) -> 'CustomTool':
        """Create a tool by importing an OpenAPI schema from a URL."""
        import uuid
        if not tool_id:
            tool_id = str(uuid.uuid4())

        config = {
            "schema_url": schema_url,
            "auth_config": auth_config,
            "auth_type": auth_config.get("type", "none")
        }

        # The schema should be loaded asynchronously here; an empty config is returned for now
        return cls(tool_id, config)

    @classmethod
    def from_schema(cls, schema_dict: Dict[str, Any], auth_config: Dict[str, Any], tool_id: str = None) -> 'CustomTool':
        """Create a tool from a schema dict."""
        import uuid
        if not tool_id:
            tool_id = str(uuid.uuid4())

        config = {
            "schema_content": schema_dict,
            "auth_config": auth_config,
            "auth_type": auth_config.get("type", "none")
        }

        return cls(tool_id, config)
