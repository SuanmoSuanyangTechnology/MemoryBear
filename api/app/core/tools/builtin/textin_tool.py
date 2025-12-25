"""TextIn OCR文字识别工具"""
import time
from typing import List, Dict, Any
import aiohttp

from app.core.tools.base import ToolParameter, ToolResult, ParameterType
from .base import BuiltinTool


class TextInTool(BuiltinTool):
    """TextIn OCR工具 - 提供通用OCR、手写识别、多语言支持、高精度识别"""
    
    @property
    def name(self) -> str:
        return "textin_tool"
    
    @property
    def description(self) -> str:
        return "TextIn - OCR文字识别：通用OCR、手写识别、多语言支持、高精度识别"
    
    def get_required_config_parameters(self) -> List[str]:
        return ["app_id", "secret_key", "api_url"]
    
    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="image_content",
                type=ParameterType.STRING,
                description="图片内容（Base64编码）",
                required=False
            ),
            ToolParameter(
                name="image_url",
                type=ParameterType.STRING,
                description="图片URL",
                required=False
            ),
            ToolParameter(
                name="language",
                type=ParameterType.STRING,
                description="识别语言",
                required=False,
                default="auto",
                enum=["auto", "zh-cn", "zh-tw", "en", "ja", "ko", "fr", "de", "es", "ru"]
            ),
            ToolParameter(
                name="recognition_mode",
                type=ParameterType.STRING,
                description="识别模式",
                required=False,
                default="general",
                enum=["general", "accurate", "handwriting", "formula", "table", "document"]
            ),
            ToolParameter(
                name="return_location",
                type=ParameterType.BOOLEAN,
                description="是否返回文字位置信息",
                required=False,
                default=False
            ),
            ToolParameter(
                name="return_confidence",
                type=ParameterType.BOOLEAN,
                description="是否返回置信度",
                required=False,
                default=True
            ),
            ToolParameter(
                name="merge_lines",
                type=ParameterType.BOOLEAN,
                description="是否合并行",
                required=False,
                default=True
            ),
            ToolParameter(
                name="output_format",
                type=ParameterType.STRING,
                description="输出格式",
                required=False,
                default="text",
                enum=["text", "json", "structured"]
            )
        ]
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行TextIn OCR识别"""
        start_time = time.time()
        
        try:
            image_content = kwargs.get("image_content")
            image_url = kwargs.get("image_url")
            
            if not image_content and not image_url:
                raise ValueError("必须提供 image_content 或 image_url 参数")
            
            language = kwargs.get("language", "auto")
            recognition_mode = kwargs.get("recognition_mode", "general")
            return_location = kwargs.get("return_location", False)
            return_confidence = kwargs.get("return_confidence", True)
            merge_lines = kwargs.get("merge_lines", True)
            output_format = kwargs.get("output_format", "text")
            
            # 根据识别模式调用不同的API
            if recognition_mode == "general":
                result = await self._general_ocr(kwargs)
            elif recognition_mode == "accurate":
                result = await self._accurate_ocr(kwargs)
            elif recognition_mode == "handwriting":
                result = await self._handwriting_ocr(kwargs)
            elif recognition_mode == "formula":
                result = await self._formula_ocr(kwargs)
            elif recognition_mode == "table":
                result = await self._table_ocr(kwargs)
            elif recognition_mode == "document":
                result = await self._document_ocr(kwargs)
            else:
                raise ValueError(f"不支持的识别模式: {recognition_mode}")
            
            execution_time = time.time() - start_time
            return ToolResult.success_result(
                data=result,
                execution_time=execution_time
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="TEXTIN_ERROR",
                execution_time=execution_time
            )
    
    async def _general_ocr(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """通用OCR识别"""
        request_data = {
            "language": kwargs.get("language", "auto"),
            "return_location": kwargs.get("return_location", False),
            "return_confidence": kwargs.get("return_confidence", True),
            "merge_lines": kwargs.get("merge_lines", True)
        }
        
        if kwargs.get("image_content"):
            request_data["image"] = kwargs["image_content"]
        elif kwargs.get("image_url"):
            request_data["image_url"] = kwargs["image_url"]
        
        result = await self._call_textin_api("general_ocr", request_data)
        
        return self._format_ocr_result(result, kwargs.get("output_format", "text"))
    
    async def _accurate_ocr(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """高精度OCR识别"""
        request_data = {
            "language": kwargs.get("language", "auto"),
            "return_location": kwargs.get("return_location", False),
            "return_confidence": kwargs.get("return_confidence", True),
            "merge_lines": kwargs.get("merge_lines", True)
        }
        
        if kwargs.get("image_content"):
            request_data["image"] = kwargs["image_content"]
        elif kwargs.get("image_url"):
            request_data["image_url"] = kwargs["image_url"]
        
        result = await self._call_textin_api("accurate_ocr", request_data)
        
        return self._format_ocr_result(result, kwargs.get("output_format", "text"))
    
    async def _handwriting_ocr(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """手写体识别"""
        request_data = {
            "language": kwargs.get("language", "auto"),
            "return_location": kwargs.get("return_location", False),
            "return_confidence": kwargs.get("return_confidence", True)
        }
        
        if kwargs.get("image_content"):
            request_data["image"] = kwargs["image_content"]
        elif kwargs.get("image_url"):
            request_data["image_url"] = kwargs["image_url"]
        
        result = await self._call_textin_api("handwriting_ocr", request_data)
        
        return self._format_ocr_result(result, kwargs.get("output_format", "text"))
    
    async def _formula_ocr(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """公式识别"""
        request_data = {
            "return_location": kwargs.get("return_location", False),
            "return_confidence": kwargs.get("return_confidence", True),
            "output_latex": True
        }
        
        if kwargs.get("image_content"):
            request_data["image"] = kwargs["image_content"]
        elif kwargs.get("image_url"):
            request_data["image_url"] = kwargs["image_url"]
        
        result = await self._call_textin_api("formula_ocr", request_data)
        
        return self._format_formula_result(result, kwargs.get("output_format", "text"))
    
    async def _table_ocr(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """表格识别"""
        request_data = {
            "language": kwargs.get("language", "auto"),
            "return_location": kwargs.get("return_location", False),
            "return_confidence": kwargs.get("return_confidence", True),
            "output_excel": True
        }
        
        if kwargs.get("image_content"):
            request_data["image"] = kwargs["image_content"]
        elif kwargs.get("image_url"):
            request_data["image_url"] = kwargs["image_url"]
        
        result = await self._call_textin_api("table_ocr", request_data)
        
        return self._format_table_result(result, kwargs.get("output_format", "text"))
    
    async def _document_ocr(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """文档识别"""
        request_data = {
            "language": kwargs.get("language", "auto"),
            "return_location": kwargs.get("return_location", False),
            "return_confidence": kwargs.get("return_confidence", True),
            "layout_analysis": True
        }
        
        if kwargs.get("image_content"):
            request_data["image"] = kwargs["image_content"]
        elif kwargs.get("image_url"):
            request_data["image_url"] = kwargs["image_url"]
        
        result = await self._call_textin_api("document_ocr", request_data)
        
        return self._format_document_result(result, kwargs.get("output_format", "text"))
    
    def _format_ocr_result(self, result: Dict[str, Any], output_format: str) -> Dict[str, Any] | None:
        """格式化OCR结果"""
        lines = result.get("lines", [])
        
        if output_format == "text":
            text_content = "\n".join([line.get("text", "") for line in lines])
            return {
                "recognition_mode": "ocr",
                "text_content": text_content,
                "line_count": len(lines),
                "total_confidence": result.get("confidence", 0),
                "processing_time": result.get("processing_time", 0)
            }
        
        elif output_format == "json":
            return {
                "recognition_mode": "ocr",
                "lines": lines,
                "total_confidence": result.get("confidence", 0),
                "processing_time": result.get("processing_time", 0)
            }
        
        elif output_format == "structured":
            return {
                "recognition_mode": "ocr",
                "text_content": "\n".join([line.get("text", "") for line in lines]),
                "structured_data": {
                    "lines": lines,
                    "paragraphs": self._group_lines_to_paragraphs(lines),
                    "statistics": {
                        "line_count": len(lines),
                        "word_count": sum(len(line.get("text", "").split()) for line in lines),
                        "character_count": sum(len(line.get("text", "")) for line in lines)
                    }
                },
                "total_confidence": result.get("confidence", 0),
                "processing_time": result.get("processing_time", 0)
            }

    @staticmethod
    def _format_formula_result( result: Dict[str, Any], output_format: str) -> Dict[str, Any]:
        """格式化公式识别结果"""
        formulas = result.get("formulas", [])
        
        return {
            "recognition_mode": "formula",
            "formula_count": len(formulas),
            "formulas": formulas,
            "latex_content": "\n".join([f.get("latex", "") for f in formulas]),
            "total_confidence": result.get("confidence", 0),
            "processing_time": result.get("processing_time", 0)
        }

    @staticmethod
    def _format_table_result(result: Dict[str, Any], output_format: str) -> Dict[str, Any]:
        """格式化表格识别结果"""
        tables = result.get("tables", [])
        
        return {
            "recognition_mode": "table",
            "table_count": len(tables),
            "tables": tables,
            "excel_data": result.get("excel_data"),
            "total_confidence": result.get("confidence", 0),
            "processing_time": result.get("processing_time", 0)
        }

    @staticmethod
    def _format_document_result(result: Dict[str, Any], output_format: str) -> Dict[str, Any]:
        """格式化文档识别结果"""
        return {
            "recognition_mode": "document",
            "layout_info": result.get("layout_info", {}),
            "text_blocks": result.get("text_blocks", []),
            "image_blocks": result.get("image_blocks", []),
            "table_blocks": result.get("table_blocks", []),
            "full_text": result.get("full_text", ""),
            "total_confidence": result.get("confidence", 0),
            "processing_time": result.get("processing_time", 0)
        }

    @staticmethod
    def _group_lines_to_paragraphs(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将行分组为段落"""
        paragraphs = []
        current_paragraph = []
        
        for line in lines:
            text = line.get("text", "").strip()
            if text:
                current_paragraph.append(line)
            else:
                if current_paragraph:
                    paragraphs.append({
                        "text": " ".join([l.get("text", "") for l in current_paragraph]),
                        "lines": current_paragraph
                    })
                    current_paragraph = []
        
        if current_paragraph:
            paragraphs.append({
                "text": " ".join([l.get("text", "") for l in current_paragraph]),
                "lines": current_paragraph
            })
        
        return paragraphs
    
    async def _call_textin_api(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """调用TextIn API"""
        app_id = self.get_config_parameter("app_id")
        secret_key = self.get_config_parameter("secret_key")
        api_url = self.get_config_parameter("api_url")
        
        if not app_id or not secret_key or not api_url:
            raise ValueError("TextIn API配置未完成")
        
        # 构建完整URL
        url = f"{api_url.rstrip('/')}/{endpoint}"
        
        # 构建请求头
        headers = {
            "X-App-Id": app_id,
            "X-Secret-Key": secret_key,
            "Content-Type": "application/json"
        }
        
        # 发送请求
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=data, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("code") == 200:
                        return result.get("data", result)
                    else:
                        raise Exception(f"TextIn API错误: {result.get('message', '未知错误')}")
                else:
                    error_text = await response.text()
                    raise Exception(f"HTTP错误 {response.status}: {error_text}")
    
    def test_connection(self) -> Dict[str, Any]:
        """测试连接"""
        try:
            app_id = self.get_config_parameter("app_id")
            secret_key = self.get_config_parameter("secret_key")
            api_url = self.get_config_parameter("api_url")
            
            if not app_id or not secret_key or not api_url:
                return {
                    "success": False,
                    "error": "API配置未完成"
                }
            
            return {
                "success": True,
                "message": "连接配置有效",
                "api_url": api_url,
                "app_id": app_id,
                "secret_key_masked": secret_key[:8] + "***" if len(secret_key) > 8 else "***"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }