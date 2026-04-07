"""自定义工具基类"""
import json
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

        #===========OpenClaw特殊判断（取到OpenClaw特殊配置）==========
        schema = self.schema_content
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
                self.schema_content = schema
            except json.JSONDecodeError:
                schema = {}

        info = schema.get("info", {}) if isinstance(schema, dict) else {}
        self._is_openclaw = info.get("x-openclaw", False)

        if self._is_openclaw:
            # 从扩展字段读取 OpenClaw 配置
            self._openclaw_agent_id = info.get("x-openclaw-agent-id", "main")
            self._openclaw_model = info.get("x-openclaw-default-model", "openclaw")
            self._openclaw_session_strategy = info.get(
                "x-openclaw-session-strategy", "by_user")
            self._openclaw_timeout = info.get("x-openclaw-timeout", 60)
            self._openclaw_input_mode = info.get("x-openclaw-input-mode", "text")
            self._openclaw_output_mode = info.get("x-openclaw-output-mode", "text")

            # 从 servers 读取 base_url
            servers = schema.get("servers", [])
            if servers:
                self.base_url = servers[0].get("url", "")

            # 从 auth_config 读取 token（兼容 api_key 和 bearer_token 两种认证方式）
            self._openclaw_token = (
                self.auth_config.get("api_key")       # api_key 认证方式
                or self.auth_config.get("token")       # bearer_token 认证方式
                or ""
            )

            # 覆盖 timeout
            self.timeout = self._openclaw_timeout

            # 运行时上下文（后续注入）
            self._user_id = "anonymous"
            self._conversation_id = None
            self._uploaded_files = []  # 新增：用户上传的文件
            
            # 跳过 Schema 解析
            self._parsed_operations = {}

            logger.info(
                f"检测到 OpenClaw 工具: agent_id={self._openclaw_agent_id}, "
                f"base_url={self.base_url}, "
                f"input_mode={self._openclaw_input_mode}, "
                f"output_mode={self._openclaw_output_mode}")
        else:
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
         # ========== OpenClaw 特判 根据输入模式解析是否需要image_url ==========
        if self._is_openclaw:
            params = [
                ToolParameter(
                    name="message",
                    type=ParameterType.STRING,
                    description="发送给 OpenClaw Agent 的文本请求内容",
                    required=True
                )
            ]
            # 多模态输入模式下，增加 image_url 参数
            if self._openclaw_input_mode == "multimodal":
                params.append(ToolParameter(
                    name="image_url",
                    type=ParameterType.STRING,
                    description=(
                        "可选，附带的图片URL或base64 data URI"
                        "（如 data:image/png;base64,...）。"
                        "传入后 Agent 可以理解图片内容。"
                    ),
                    required=False
                ))
            return params
        # ========== 特判结束 ==========

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
        # ========== OpenClaw 特判 ==========
        if self._is_openclaw:
            return await self._execute_openclaw(**kwargs)
        # ========== 特判结束 ==========
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
    
    #=============openclaw执行函数开始===============
    async def _execute_openclaw(self, **kwargs) -> ToolResult:
        """OpenClaw 专属执行逻辑（支持多模态输入）"""
        start_time = time.time()
        try:
            message = kwargs.get("message", "")
            # 从用户实际上传的文件中提取图片 URL
            image_url = None
            if self._uploaded_files:
                for f in self._uploaded_files:
                    f_type = f.get("type", "")
                    if f_type == "image":
                        # Bedrock/Anthropic 格式：{"type": "image", "source": {"type": "base64", ...}}
                        source = f.get("source", {})
                        if source.get("type") == "base64":
                            media_type = source.get("media_type", "image/jpeg")
                            data = source.get("data", "")
                            image_url = f"data:{media_type};base64,{data}"
                        elif f.get("image"):
                            # DashScope 格式：{"type": "image", "image": "url"}
                            image_url = f.get("image")
                        elif f.get("url"):
                            # 其他格式：{"type": "image", "url": "https://..."}
                            image_url = f.get("url")
                        break
                    elif f_type == "image_url":
                        # OpenAI/Volcano 格式：{"type": "image_url", "image_url": {"url": "..."}}
                        image_url = f.get("image_url", {}).get("url", "")
                        break
   
            # 如果 image_url 是服务器中转 URL，直接下载图片转 base64
            # 避免 OSS 签名 URL 在重定向解析过程中被破坏
            if image_url and not image_url.startswith("data:"):
                try:
                    import base64
                    from io import BytesIO
                    from PIL import Image

                    MAX_RAW_SIZE = 4 * 1024 * 1024  # 超过 4MB 则压缩

                    async with aiohttp.ClientSession() as _session:
                        async with _session.get(image_url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=30)) as _resp:
                            if _resp.status == 200:
                                content_type = _resp.headers.get("Content-Type", "image/jpeg")
                                if content_type.startswith("image/"):
                                    img_bytes = await _resp.read()
                                    original_size = len(img_bytes)
                                    logger.info(f"OpenClaw 下载图片: size={original_size} bytes, type={content_type}")

                                    if original_size > MAX_RAW_SIZE:
                                        img = Image.open(BytesIO(img_bytes))
                                        if img.mode in ("RGBA", "P", "LA"):
                                            img = img.convert("RGB")
                                        max_side = 2048
                                        if max(img.size) > max_side:
                                            img.thumbnail((max_side, max_side), Image.LANCZOS)
                                        buf = BytesIO()
                                        img.save(buf, format="JPEG", quality=75, optimize=True)
                                        img_bytes = buf.getvalue()
                                        content_type = "image/jpeg"
                                        logger.info(f"OpenClaw 图片已压缩: {original_size} -> {len(img_bytes)} bytes")

                                    b64_data = base64.b64encode(img_bytes).decode("utf-8")
                                    image_url = f"data:{content_type};base64,{b64_data}"
                                    logger.info(f"OpenClaw 图片已转为 base64, size={len(img_bytes)} bytes")
                                else:
                                    logger.warning(f"OpenClaw 图片 URL 返回非图片类型: {content_type}")
                            else:
                                logger.warning(f"OpenClaw 下载图片失败: HTTP {_resp.status}")
                except Exception as e:
                    logger.warning(f"OpenClaw 下载图片失败，使用原始 URL: {e}")


            if not message:
                return ToolResult.error_result(
                    error="message 参数不能为空",
                    error_code="OPENCLAW_INVALID_INPUT",
                    execution_time=time.time() - start_time)

            url = f"{self.base_url.rstrip('/')}/v1/responses"
            #请求头
            headers = {
                "Authorization": f"Bearer {self._openclaw_token}",
                "Content-Type": "application/json",
                "x-openclaw-agent-id": self._openclaw_agent_id
            }

            # session 路由
            if (self._openclaw_session_strategy == "by_conversation"
                    and self._conversation_id):
                user_field = f"conv-{self._conversation_id}"
            else:
                user_field = f"user-{self._user_id}"

            # 根据 input_mode 和是否有图片构造 input
            input_field = self._build_openclaw_input(message, image_url)
            #请求体
            body = {
                "model": self._openclaw_model,
                "user": user_field,
                "input": input_field,
                "stream": False
            }

            logger.info(f"OpenClaw 请求体: {json.dumps(body, ensure_ascii=False)[:1000]}")

            timeout_config = aiohttp.ClientTimeout(total=self.timeout)
            #请求
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.post(url, json=body, headers=headers) as resp:
                    execution_time = time.time() - start_time

                    if resp.status >= 400:
                        error_text = await resp.text()
                        _img_preview2 = (image_url[:100] + "...") if image_url and len(image_url) > 100 else image_url
                        logger.error(
                            f"OpenClaw 调用失败: HTTP {resp.status}, "
                            f"url={url}, agent_id={self._openclaw_agent_id}, "
                            f"has_image={bool(image_url)}, image_url={_img_preview2}, "
                            f"input_type={'multimodal' if isinstance(input_field, list) else 'text'}, "
                            f"error_response={error_text[:1000]}"
                        )
                        return ToolResult.error_result(
                            error=f"OpenClaw HTTP {resp.status}: {error_text[:500]}",
                            error_code="OPENCLAW_HTTP_ERROR",
                            execution_time=execution_time)

                    data = await resp.json()

                    # 根据 output_mode 解析响应
                    result = self._extract_openclaw_response(
                        data, self._openclaw_output_mode)
                    display_text = self._format_openclaw_result(result)

                    logger.info(
                        "OpenClaw 调用成功",
                        extra={
                            "tool_id": self.tool_id,
                            "agent_id": self._openclaw_agent_id,
                            "has_images": len(result["images"]) > 0,
                            "execution_time": execution_time
                        })
                    return ToolResult.success_result(
                        data=display_text, execution_time=execution_time)

        except aiohttp.ClientError as e:
            return ToolResult.error_result(
                error=f"OpenClaw 网络连接失败: {str(e)}",
                error_code="OPENCLAW_NETWORK_ERROR",
                execution_time=time.time() - start_time)
        except Exception as e:
            return ToolResult.error_result(
                error=f"OpenClaw 调用失败: {str(e)}",
                error_code="OPENCLAW_EXECUTION_ERROR",
                execution_time=time.time() - start_time)

    def _build_openclaw_input(self, message: str, image_url: str = None):
        """根据 input_mode 和是否有图片构造 OpenClaw input 字段

        纯文本模式或无图片 → 返回字符串
        多模态模式且有图片 → 返回结构化 item 数组
        """
        if not image_url or self._openclaw_input_mode != "multimodal":
            return message

        # 构造多模态 content 数组
        content_parts = [
            {"type": "input_text", "text": message}
        ]

        if image_url.startswith("data:"):
            # base64 data URI: data:image/png;base64,iVBORw0KGgo...
            try:
                header, data = image_url.split(",", 1)
                media_type = header.split(":")[1].split(";")[0]
                content_parts.append({
                    "type": "input_image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data
                    }
                })
            except (ValueError, IndexError):
                logger.warning("无法解析 base64 data URI，回退为纯文本输入")
                return message
        else:
            # URL 引用
            content_parts.append({
                "type": "input_image",
                "source": {
                    "type": "url",
                    "url": image_url
                }
            })

        return [{
            "type": "message",
            "role": "user",
            "content": content_parts
        }]

    @staticmethod
    def _extract_openclaw_response(response_data: Dict[str, Any],
                                    output_mode: str = "text") -> Dict[str, Any]:
        """从 OpenClaw 响应中提取文本和图片

        响应格式：
        {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "..."},
            {"type": "output_image", "image_url": "..."}
        ]}]}

        返回:
        {"text": "文本内容", "images": [{"url": "...", "media_type": "image/png"}]}
        """
        output = response_data.get("output", [])
        texts = []
        images = []

        for item in output:
            if item.get("type") == "message":
                for content in item.get("content", []):
                    content_type = content.get("type")

                    if content_type == "output_text":
                        text = content.get("text", "")
                        if text:
                            texts.append(text)

                    elif content_type == "output_image" and output_mode == "multimodal":
                        image_url = content.get("image_url", "")
                        if image_url:
                            images.append({
                                "url": image_url,
                                "media_type": content.get("media_type", "image/png")
                            })

        text_result = "\n".join(texts) if texts else ""

        # text 模式下只返回文本（向后兼容）
        if output_mode == "text":
            return {"text": text_result or str(response_data), "images": []}

        return {"text": text_result, "images": images}

    @staticmethod
    def _format_openclaw_result(result: Dict[str, Any]) -> str:
        """将解析结果格式化为返回给 LLM 的字符串

        纯文本 → 直接返回
        有图片 → 将图片以 Markdown 格式嵌入文本
        """
        text = result.get("text", "")
        images = result.get("images", [])

        if not images:
            return text or "（OpenClaw 返回了空内容）"

        parts = []
        if text:
            parts.append(text)
        for i, img in enumerate(images):
            parts.append(f"![OpenClaw 生成的图片 {i+1}]({img['url']})")

        return "\n\n".join(parts)

   
    #=============openclaw执行函数结束================
    def _parse_openapi_schema(self) -> Dict[str, Any]:
        """解析OpenAPI schema"""
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