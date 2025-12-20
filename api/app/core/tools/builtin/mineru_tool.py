"""MinerU PDF解析工具"""
import time
from typing import List, Dict, Any
import aiohttp

from app.core.tools.base import ToolParameter, ToolResult, ParameterType
from .base import BuiltinTool


class MinerUTool(BuiltinTool):
    """MinerU PDF解析工具 - 提供PDF解析、表格提取、图片识别、文本提取功能"""
    
    @property
    def name(self) -> str:
        return "mineru_tool"
    
    @property
    def description(self) -> str:
        return "MinerU - PDF解析工具：PDF解析、表格提取、图片识别、文本提取"
    
    def get_required_config_parameters(self) -> List[str]:
        return ["api_key", "api_url"]
    
    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="操作类型",
                required=True,
                enum=["parse_pdf", "extract_text", "extract_tables", "extract_images", "analyze_layout"]
            ),
            ToolParameter(
                name="file_content",
                type=ParameterType.STRING,
                description="PDF文件内容（Base64编码）",
                required=False
            ),
            ToolParameter(
                name="file_url",
                type=ParameterType.STRING,
                description="PDF文件URL",
                required=False
            ),
            ToolParameter(
                name="parse_mode",
                type=ParameterType.STRING,
                description="解析模式",
                required=False,
                default="auto",
                enum=["auto", "text_only", "table_priority", "image_priority", "layout_analysis"]
            ),
            ToolParameter(
                name="extract_images",
                type=ParameterType.BOOLEAN,
                description="是否提取图片",
                required=False,
                default=True
            ),
            ToolParameter(
                name="extract_tables",
                type=ParameterType.BOOLEAN,
                description="是否提取表格",
                required=False,
                default=True
            ),
            ToolParameter(
                name="page_range",
                type=ParameterType.STRING,
                description="页面范围（如：1-5, 1,3,5）",
                required=False
            ),
            ToolParameter(
                name="output_format",
                type=ParameterType.STRING,
                description="输出格式",
                required=False,
                default="json",
                enum=["json", "markdown", "html", "text"]
            )
        ]
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行MinerU PDF解析"""
        start_time = time.time()
        
        try:
            operation = kwargs.get("operation")
            file_content = kwargs.get("file_content")
            file_url = kwargs.get("file_url")
            
            if not file_content and not file_url:
                raise ValueError("必须提供 file_content 或 file_url 参数")
            
            if operation == "parse_pdf":
                result = await self._parse_pdf(kwargs)
            elif operation == "extract_text":
                result = await self._extract_text(kwargs)
            elif operation == "extract_tables":
                result = await self._extract_tables(kwargs)
            elif operation == "extract_images":
                result = await self._extract_images(kwargs)
            elif operation == "analyze_layout":
                result = await self._analyze_layout(kwargs)
            else:
                raise ValueError(f"不支持的操作类型: {operation}")
            
            execution_time = time.time() - start_time
            return ToolResult.success_result(
                data=result,
                execution_time=execution_time
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="MINERU_ERROR",
                execution_time=execution_time
            )
    
    async def _parse_pdf(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """完整PDF解析"""
        parse_mode = kwargs.get("parse_mode", "auto")
        extract_images = kwargs.get("extract_images", True)
        extract_tables = kwargs.get("extract_tables", True)
        page_range = kwargs.get("page_range")
        output_format = kwargs.get("output_format", "json")
        
        # 构建请求参数
        request_data = {
            "parse_mode": parse_mode,
            "extract_images": extract_images,
            "extract_tables": extract_tables,
            "output_format": output_format
        }
        
        if page_range:
            request_data["page_range"] = page_range
        
        # 添加文件数据
        if kwargs.get("file_content"):
            request_data["file_content"] = kwargs["file_content"]
        elif kwargs.get("file_url"):
            request_data["file_url"] = kwargs["file_url"]
        
        # 调用MinerU API
        result = await self._call_mineru_api("parse", request_data)
        
        return {
            "operation": "parse_pdf",
            "parse_mode": parse_mode,
            "total_pages": result.get("total_pages", 0),
            "processed_pages": result.get("processed_pages", 0),
            "text_content": result.get("text_content", ""),
            "tables": result.get("tables", []),
            "images": result.get("images", []),
            "layout_info": result.get("layout_info", {}),
            "metadata": result.get("metadata", {}),
            "processing_time": result.get("processing_time", 0)
        }
    
    async def _extract_text(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """提取文本"""
        page_range = kwargs.get("page_range")
        output_format = kwargs.get("output_format", "text")
        
        request_data = {
            "operation": "extract_text",
            "output_format": output_format
        }
        
        if page_range:
            request_data["page_range"] = page_range
        
        if kwargs.get("file_content"):
            request_data["file_content"] = kwargs["file_content"]
        elif kwargs.get("file_url"):
            request_data["file_url"] = kwargs["file_url"]
        
        result = await self._call_mineru_api("extract_text", request_data)
        
        return {
            "operation": "extract_text",
            "total_pages": result.get("total_pages", 0),
            "text_content": result.get("text_content", ""),
            "word_count": len(result.get("text_content", "").split()),
            "character_count": len(result.get("text_content", "")),
            "pages_text": result.get("pages_text", [])
        }
    
    async def _extract_tables(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """提取表格"""
        page_range = kwargs.get("page_range")
        output_format = kwargs.get("output_format", "json")
        
        request_data = {
            "operation": "extract_tables",
            "output_format": output_format
        }
        
        if page_range:
            request_data["page_range"] = page_range
        
        if kwargs.get("file_content"):
            request_data["file_content"] = kwargs["file_content"]
        elif kwargs.get("file_url"):
            request_data["file_url"] = kwargs["file_url"]
        
        result = await self._call_mineru_api("extract_tables", request_data)
        
        return {
            "operation": "extract_tables",
            "total_tables": result.get("total_tables", 0),
            "tables": result.get("tables", []),
            "table_locations": result.get("table_locations", [])
        }
    
    async def _extract_images(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """提取图片"""
        page_range = kwargs.get("page_range")
        
        request_data = {
            "operation": "extract_images"
        }
        
        if page_range:
            request_data["page_range"] = page_range
        
        if kwargs.get("file_content"):
            request_data["file_content"] = kwargs["file_content"]
        elif kwargs.get("file_url"):
            request_data["file_url"] = kwargs["file_url"]
        
        result = await self._call_mineru_api("extract_images", request_data)
        
        return {
            "operation": "extract_images",
            "total_images": result.get("total_images", 0),
            "images": result.get("images", []),
            "image_locations": result.get("image_locations", [])
        }
    
    async def _analyze_layout(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """分析布局"""
        page_range = kwargs.get("page_range")
        
        request_data = {
            "operation": "analyze_layout"
        }
        
        if page_range:
            request_data["page_range"] = page_range
        
        if kwargs.get("file_content"):
            request_data["file_content"] = kwargs["file_content"]
        elif kwargs.get("file_url"):
            request_data["file_url"] = kwargs["file_url"]
        
        result = await self._call_mineru_api("analyze_layout", request_data)
        
        return {
            "operation": "analyze_layout",
            "layout_info": result.get("layout_info", {}),
            "page_layouts": result.get("page_layouts", []),
            "text_blocks": result.get("text_blocks", []),
            "image_blocks": result.get("image_blocks", []),
            "table_blocks": result.get("table_blocks", [])
        }
    
    async def _call_mineru_api(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """调用MinerU API"""
        api_key = self.get_config_parameter("api_key")
        api_url = self.get_config_parameter("api_url")
        timeout_seconds = self.get_config_parameter("timeout", 60)
        
        if not api_key or not api_url:
            raise ValueError("MinerU API配置未完成")
        
        # 构建完整URL
        url = f"{api_url.rstrip('/')}/{endpoint}"
        
        # 构建请求头
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # 发送请求
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=data, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("success", True):
                        return result.get("data", result)
                    else:
                        raise Exception(f"MinerU API错误: {result.get('message', '未知错误')}")
                else:
                    error_text = await response.text()
                    raise Exception(f"HTTP错误 {response.status}: {error_text}")
    
    def test_connection(self) -> Dict[str, Any]:
        """测试连接"""
        try:
            api_key = self.get_config_parameter("api_key")
            api_url = self.get_config_parameter("api_url")
            
            if not api_key or not api_url:
                return {
                    "success": False,
                    "error": "API配置未完成"
                }
            
            return {
                "success": True,
                "message": "连接配置有效",
                "api_url": api_url,
                "api_key_masked": api_key[:8] + "***" if len(api_key) > 8 else "***"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }