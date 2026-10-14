"""TextIn OCR text recognition tool."""
from typing import List

from ..base import ToolParameter, ParameterType
from .base import BuiltinTool


class TextInTool(BuiltinTool):
    """TextIn OCR tool - general OCR, handwriting, multilingual and high-accuracy recognition."""

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
