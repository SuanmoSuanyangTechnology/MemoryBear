import asyncio
import json
from datetime import datetime, timedelta


from langchain.tools import tool
from pydantic import BaseModel, Field


from app.core.memory.src.search import (
    search_by_temporal,
    search_by_keyword_temporal,
)

def extract_tool_message_content(response):
    """从agent响应中提取ToolMessage内容和工具名称"""
    messages = response.get('messages', [])

    for message in messages:
        if hasattr(message, 'tool_call_id') and hasattr(message, 'content'):
            # 这是一个ToolMessage
            tool_content = message.content
            tool_name = None

            # 尝试获取工具名称
            if hasattr(message, 'name'):
                tool_name = message.name
            elif hasattr(message, 'tool_name'):
                tool_name = message.tool_name

            try:
                # 解析JSON内容
                parsed_content = json.loads(tool_content)
                return {
                    'tool_name': tool_name,
                    'content': parsed_content
                }
            except json.JSONDecodeError:
                # 如果不是JSON格式，直接返回内容
                return {
                    'tool_name': tool_name,
                    'content': tool_content
                }

    return None


class TimeRetrievalInput(BaseModel):
    """时间检索工具的输入模式"""
    context: str = Field(description="用户输入的查询内容")
    end_user_id: str = Field(default="88a459f5_text09", description="组ID，用于过滤搜索结果")

def create_time_retrieval_tool(end_user_id: str):
    """
    创建一个带有特定end_user_id的TimeRetrieval工具（同步版本），用于按时间范围搜索语句(Statements)
    """
    
    def clean_temporal_result_fields(data):
        """
        清理时间搜索结果中不需要的字段，并修改结构
        
        Args:
            data: 要清理的数据
            
        Returns:
            清理后的数据
        """
        # 需要过滤的字段列表
        fields_to_remove = {
            'id', 'apply_id', 'user_id', 'chunk_id', 'created_at', 
            'valid_at', 'invalid_at', 'statement_ids'
        }
        
        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                if key == 'statements' and isinstance(value, dict) and 'statements' in value:
                    # 将 statements: {"statements": [...]} 改为 time_search: {"statements": [...]}
                    cleaned_value = clean_temporal_result_fields(value)
                    # 进一步将内部的 statements 改为 time_search
                    if 'statements' in cleaned_value:
                        cleaned['results'] = {
                            'time_search': cleaned_value['statements']
                        }
                    else:
                        cleaned['results'] = cleaned_value
                elif key not in fields_to_remove:
                    cleaned[key] = clean_temporal_result_fields(value)
            return cleaned
        elif isinstance(data, list):
            return [clean_temporal_result_fields(item) for item in data]
        else:
            return data
    
    @tool
    def TimeRetrievalWithGroupId(context: str, start_date: str = None, end_date: str = None, end_user_id_param: str = None, clean_output: bool = True) -> str:
        """
        优化的时间检索工具，只结合时间范围搜索（同步版本），自动过滤不需要的元数据字段
        显式接收参数：
        - context: 查询上下文内容
        - start_date: 开始时间（可选，格式：YYYY-MM-DD）
        - end_date: 结束时间（可选，格式：YYYY-MM-DD）
        - end_user_id_param: 组ID（可选，用于覆盖默认组ID）
        - clean_output: 是否清理输出中的元数据字段
        -end_date 需要根据用户的描述获取结束的时间，输出格式用strftime("%Y-%m-%d")
        """
        async def _async_search():
            # 使用传入的参数或默认值
            actual_end_user_id = end_user_id_param or end_user_id
            actual_end_date = end_date or datetime.now().strftime("%Y-%m-%d")
            actual_start_date = start_date or (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            
            # 基本时间搜索
            results = await search_by_temporal(
                end_user_id=actual_end_user_id,
                start_date=actual_start_date,
                end_date=actual_end_date,
                limit=10
            )
            
            # 清理结果中不需要的字段
            if clean_output:
                cleaned_results = clean_temporal_result_fields(results)
            else:
                cleaned_results = results

            return json.dumps(cleaned_results, ensure_ascii=False, indent=2)
        
        return asyncio.run(_async_search())

    @tool
    def KeywordTimeRetrieval(context: str, days_back: int = 7, start_date: str = None, end_date: str = None, clean_output: bool = True) -> str:
        """
        优化的关键词时间检索工具，结合关键词和时间范围搜索（同步版本），自动过滤不需要的元数据字段
        显式接收参数：
        - context: 查询内容
        - days_back: 向前搜索的天数，默认7天
        - start_date: 开始时间（可选，格式：YYYY-MM-DD）
        - end_date: 结束时间（可选，格式：YYYY-MM-DD）
        - clean_output: 是否清理输出中的元数据字段
        - end_date 需要根据用户的描述获取结束的时间，输出格式用strftime("%Y-%m-%d")
        """
        async def _async_search():
            actual_end_date = end_date or datetime.now().strftime("%Y-%m-%d")
            actual_start_date = start_date or (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

            # 关键词时间搜索
            results = await search_by_keyword_temporal(
                query_text=context,
                end_user_id=end_user_id,
                start_date=actual_start_date,
                end_date=actual_end_date,
                limit=15
            )
            
            # 清理结果中不需要的字段
            if clean_output:
                cleaned_results = clean_temporal_result_fields(results)
            else:
                cleaned_results = results

            return json.dumps(cleaned_results, ensure_ascii=False, indent=2)

        return asyncio.run(_async_search())
    
    return TimeRetrievalWithGroupId


def create_hybrid_retrieval_tool_async(memory_config, **search_params):
    """
    创建混合检索工具，使用run_hybrid_search进行混合检索，优化输出格式并过滤不需要的字段
    
    Args:
        memory_config: 内存配置对象
        **search_params: 搜索参数，包含end_user_id, limit, include等
    """
    
    def clean_result_fields(data):
        """
        递归清理结果中不需要的字段
        
        Args:
            data: 要清理的数据（可能是字典、列表或其他类型）
            
        Returns:
            清理后的数据
        """
        # 需要过滤的字段列表
        fields_to_remove = {
            'invalid_at', 'valid_at', 'chunk_id_from_rel', 'entity_ids', 
            'expired_at', 'created_at', 'chunk_id', 'id', 'apply_id', 
            'user_id', 'statement_ids', 'updated_at',"chunk_ids","fact_summary"
        }
        
        if isinstance(data, dict):
            # 对字典进行清理
            cleaned = {}
            for key, value in data.items():
                if key not in fields_to_remove:
                    cleaned[key] = clean_result_fields(value)  # 递归清理嵌套数据
            return cleaned
        elif isinstance(data, list):
            # 对列表中的每个元素进行清理
            return [clean_result_fields(item) for item in data]
        else:
            # 其他类型直接返回
            return data
    
    @tool
    async def HybridSearch(
        context: str, 
        search_type: str = "hybrid",
        limit: int = 10,
        end_user_id: str = None,
        rerank_alpha: float = 0.6,
        use_forgetting_rerank: bool = False,
        use_llm_rerank: bool = False,
        clean_output: bool = True  # 新增：是否清理输出字段
    ) -> str:
        """
        优化的混合检索工具，支持关键词、向量和混合搜索，自动过滤不需要的元数据字段
        
        Args:
            context: 查询内容
            search_type: 搜索类型 ('keyword', 'embedding', 'hybrid')
            limit: 结果数量限制
            end_user_id: 组ID，用于过滤搜索结果
            rerank_alpha: 重排序权重参数
            use_forgetting_rerank: 是否使用遗忘重排序
            use_llm_rerank: 是否使用LLM重排序
            clean_output: 是否清理输出中的元数据字段
        """
        try:
            # 导入run_hybrid_search函数
            from app.core.memory.src.search import run_hybrid_search
            
            # 合并参数，优先使用传入的参数
            final_params = {
                "query_text": context,
                "search_type": search_type,
                "end_user_id": end_user_id or search_params.get("end_user_id"),
                "limit": limit or search_params.get("limit", 10),
                "include": search_params.get("include", ["summaries", "statements", "chunks", "entities"]),
                "output_path": None,  # 不保存到文件
                "memory_config": memory_config,
                "rerank_alpha": rerank_alpha,
                "use_forgetting_rerank": use_forgetting_rerank,
                "use_llm_rerank": use_llm_rerank
            }
            
            # 执行混合检索
            raw_results = await run_hybrid_search(**final_params)
            
            # 清理结果中不需要的字段
            if clean_output:
                cleaned_results = clean_result_fields(raw_results)
            else:
                cleaned_results = raw_results
            
            # 格式化返回结果
            formatted_results = {
                "search_query": context,
                "search_type": search_type,
                "results": cleaned_results
            }
            
            return json.dumps(formatted_results, ensure_ascii=False, indent=2, default=str)
            
        except Exception as e:
            error_result = {
                "error": f"混合检索失败: {str(e)}",
                "search_query": context,
                "search_type": search_type,
                "timestamp": datetime.now().isoformat()
            }
            return json.dumps(error_result, ensure_ascii=False, indent=2)
    
    return HybridSearch


def create_hybrid_retrieval_tool_sync(memory_config, **search_params):
    """
    创建同步版本的混合检索工具，优化输出格式并过滤不需要的字段
    
    Args:
        memory_config: 内存配置对象
        **search_params: 搜索参数
    """
    @tool
    def HybridSearchSync(
        context: str, 
        search_type: str = "hybrid",
        limit: int = 10,
        end_user_id: str = None,
        clean_output: bool = True
    ) -> str:
        """
        优化的混合检索工具（同步版本），自动过滤不需要的元数据字段
        
        Args:
            context: 查询内容
            search_type: 搜索类型 ('keyword', 'embedding', 'hybrid')
            limit: 结果数量限制
            end_user_id: 组ID，用于过滤搜索结果
            clean_output: 是否清理输出中的元数据字段
        """
        async def _async_search():
            # 创建异步工具并执行
            async_tool = create_hybrid_retrieval_tool_async(memory_config, **search_params)
            return await async_tool.ainvoke({
                "context": context,
                "search_type": search_type,
                "limit": limit,
                "end_user_id": end_user_id,
                "clean_output": clean_output
            })
        
        return asyncio.run(_async_search())
    
    return HybridSearchSync