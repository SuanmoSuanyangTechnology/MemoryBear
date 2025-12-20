"""百度搜索工具 - 搜索引擎服务"""
import time
from typing import List, Dict, Any
import aiohttp

from app.core.tools.base import ToolParameter, ToolResult, ParameterType
from .base import BuiltinTool


class BaiduSearchTool(BuiltinTool):
    """百度搜索工具 - 提供网页搜索、新闻搜索、图片搜索、实时结果"""
    
    @property
    def name(self) -> str:
        return "baidu_search_tool"
    
    @property
    def description(self) -> str:
        return "百度搜索 - 搜索引擎服务：网页搜索、新闻搜索、图片搜索、实时结果"
    
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
                description="搜索类型",
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
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行百度搜索"""
        start_time = time.time()
        
        try:
            query = kwargs.get("query")
            search_type = kwargs.get("search_type", "web")
            page_size = kwargs.get("page_size", 10)
            page_num = kwargs.get("page_num", 1)
            safe_search = kwargs.get("safe_search", True)
            region = kwargs.get("region", "cn")
            time_filter = kwargs.get("time_filter")
            
            if not query:
                raise ValueError("query 参数是必需的")
            
            # 根据搜索类型调用不同的API
            if search_type == "web":
                result = await self._web_search(query, page_size, page_num, safe_search, region, time_filter)
            elif search_type == "news":
                result = await self._news_search(query, page_size, page_num, region, time_filter)
            elif search_type == "image":
                result = await self._image_search(query, page_size, page_num, safe_search)
            elif search_type == "video":
                result = await self._video_search(query, page_size, page_num, safe_search)
            else:
                raise ValueError(f"不支持的搜索类型: {search_type}")
            
            execution_time = time.time() - start_time
            return ToolResult.success_result(
                data=result,
                execution_time=execution_time
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="BAIDU_SEARCH_ERROR",
                execution_time=execution_time
            )
    
    async def _web_search(self, query: str, page_size: int, page_num: int, 
                         safe_search: bool, region: str, time_filter: str = None) -> Dict[str, Any]:
        """网页搜索"""
        payload = {
            "messages": [{"role": "user", "content": query}],
            "edition": "standard",
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": min(page_size, 50)}],
            "enable_full_content": True
        }
        
        if time_filter:
            time_map = {"day": "now-1d/d", "week": "now-1w/d", "month": "now-1M/d", "year": "now-1y/d"}
            if time_filter in time_map:
                payload["search_filter"] = {"range": {"page_time": {"gte": time_map[time_filter], "lt": "now/d"}}}
                payload["search_recency_filter"] = time_filter
        
        results = await self._call_baidu_ai_search_api(payload)
        
        search_results = []
        if "references" in results:
            for item in results["references"]:
                search_results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                    "display_url": item.get("url", ""),
                    "rank": len(search_results) + 1
                })
        
        return {
            "search_type": "web",
            "query": query,
            "total_results": len(search_results),
            "page_num": page_num,
            "page_size": page_size,
            "results": search_results,
            "answer": results.get("result", ""),
            "references": results.get("references", [])
        }

    async def _news_search(self, query: str, page_size: int, page_num: int,
                          region: str, time_filter: str = None) -> Dict[str, Any]:
        """新闻搜索"""
        payload = {
            "messages": [{"role": "user", "content": query}],
            "edition": "standard",
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "new", "top_k": min(page_size, 50)}],
            "enable_full_content": True
        }

        if time_filter:
            time_map = {"day": "now-1d/d", "week": "now-1w/d", "month": "now-1M/d", "year": "now-1y/d"}
            if time_filter in time_map:
                payload["search_filter"] = {"range": {"page_time": {"gte": time_map[time_filter], "lt": "now/d"}}}
                payload["search_recency_filter"] = time_filter

        results = await self._call_baidu_ai_search_api(payload)

        search_results = []
        if "references" in results:
            for item in results["references"]:
                search_results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                    "display_url": item.get("url", ""),
                    "rank": len(search_results) + 1
                })

        return {
            "search_type": "new",
            "query": query,
            "total_results": len(search_results),
            "page_num": page_num,
            "page_size": page_size,
            "results": search_results,
            "answer": results.get("result", ""),
            "references": results.get("references", [])
        }

    async def _image_search(self, query: str, page_size: int, page_num: int,
                           safe_search: bool) -> Dict[str, Any]:
        """图片搜索"""
        payload = {
            "messages": [{"role": "user", "content": query}],
            "edition": "standard",
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "image", "top_k": min(page_size, 30)}],
            "enable_full_content": True
        }

        results = await self._call_baidu_ai_search_api(payload)

        search_results = []
        if "references" in results:
            for item in results["references"]:
                search_results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                    "display_url": item.get("url", ""),
                    "rank": len(search_results) + 1
                })

        return {
            "search_type": "image",
            "query": query,
            "total_results": len(search_results),
            "page_num": page_num,
            "page_size": page_size,
            "results": search_results,
            "answer": results.get("result", ""),
            "references": results.get("references", [])
        }

    async def _video_search(self, query: str, page_size: int, page_num: int,
                           safe_search: bool) -> Dict[str, Any]:
        """视频搜索"""
        payload = {
            "messages": [{"role": "user", "content": query}],
            "edition": "standard",
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "video", "top_k": min(page_size, 10)}],
            "enable_full_content": True
        }

        results = await self._call_baidu_ai_search_api(payload)

        search_results = []
        if "references" in results:
            for item in results["references"]:
                search_results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                    "display_url": item.get("url", ""),
                    "rank": len(search_results) + 1
                })

        return {
            "search_type": "video",
            "query": query,
            "total_results": len(search_results),
            "page_num": page_num,
            "page_size": page_size,
            "results": search_results,
            "answer": results.get("result", ""),
            "references": results.get("references", [])
        }

    async def _call_baidu_ai_search_api(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """调用百度AI搜索API"""
        api_key = self.get_config_parameter("api_key")
        
        if not api_key:
            raise ValueError("百度搜索API密钥未配置")
        
        url = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }
        
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    raise Exception(f"HTTP错误: {response.status}")

    async def test_connection(self) -> Dict[str, Any]:
        """测试连接"""
        try:
            api_key = self.get_config_parameter("api_key")

            if not api_key:
                return {
                    "success": False,
                    "error": "API密钥未配置"
                }

            # 发送测试请求验证API key是否有效
            test_payload = {
                "messages": [{"role": "user", "content": "test"}],
                "edition": "standard",
                "search_source": "baidu_search_v2",
                "resource_type_filter": [{"type": "web", "top_k": 1}]
            }

            try:
                await self._call_baidu_ai_search_api(test_payload)
                return {
                    "success": True,
                    "message": "连接测试成功",
                    "api_key_masked": api_key[:8] + "***" if len(api_key) > 8 else "***"
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"API连接失败: {str(e)}"
                }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }