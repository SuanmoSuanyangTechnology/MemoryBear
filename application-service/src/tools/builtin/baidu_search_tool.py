"""Baidu search tool - search engine service."""
from typing import List

from ..base import ToolParameter, ParameterType
from .base import BuiltinTool


class BaiduSearchTool(BuiltinTool):
    """Baidu search tool - web, news and image search with real-time results."""

    @property
    def name(self) -> str:
        return "baidu_search_tool"

    @property
    def description(self) -> str:
        return "百度搜索 - 搜索引擎服务：网页搜索、新闻搜索、图片搜索、视频搜索"

    def get_required_config_parameters(self) -> List[str]:
        return ["api_key"]

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="搜索关键词",
                required=True
            ),
            ToolParameter(
                name="search_type",
                type=ParameterType.STRING,
                description="搜索类型, web: 网页搜索；news：新闻搜索；image：图片搜索；video视频搜索",
                required=False,
                default="web",
                enum=["web", "news", "image", "video"]
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="每页结果数",
                required=False,
                default=10,
                minimum=1,
                maximum=50
            ),
            ToolParameter(
                name="page_num",
                type=ParameterType.INTEGER,
                description="页码（从1开始）",
                required=False,
                default=1,
                minimum=1,
                maximum=10
            ),
            ToolParameter(
                name="safe_search",
                type=ParameterType.BOOLEAN,
                description="是否启用安全搜索",
                required=False,
                default=True
            ),
            ToolParameter(
                name="region",
                type=ParameterType.STRING,
                description="搜索地区",
                required=False,
                default="cn",
                enum=["cn", "hk", "tw", "us", "jp", "kr"]
            ),
            ToolParameter(
                name="time_filter",
                type=ParameterType.STRING,
                description="时间过滤",
                required=False,
                enum=["all", "day", "week", "month", "year"]
            )
        ]
