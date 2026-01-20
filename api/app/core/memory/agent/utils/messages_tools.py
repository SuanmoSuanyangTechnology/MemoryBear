from typing import List, Dict, Any
from app.core.logging_config import get_agent_logger

logger = get_agent_logger(__name__)
async def read_template_file(template_path: str) -> str:
    """
    读取模板文件

    Args:
        template_path: 模板文件路径

    Returns:
        模板内容字符串

    Note:
        建议使用 app.core.memory.utils.template_render 中的统一模板渲染功能
    """
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"模板文件未找到: {template_path}")
        raise
    except IOError as e:
        logger.error(f"读取模板文件失败: {template_path}, 错误: {str(e)}", exc_info=True)
        raise

def reorder_output_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    重新排序输出结果，将 retrieval_summary 类型的数据放到最后面

    Args:
        results: 原始输出结果列表

    Returns:
        重新排序后的结果列表
    """
    retrieval_summaries = []
    other_results = []

    # 分离 retrieval_summary 和其他类型的结果
    for result in results:
        if 'summary' in result.get('type'):
            retrieval_summaries.append(result)
        else:
            other_results.append(result)

    # 将 retrieval_summary 放到最后
    return other_results + retrieval_summaries

def optimize_search_results(intermediate_outputs):
    """
    优化检索结果，合并多个搜索结果，过滤空结果，统一格式

    Args:
        intermediate_outputs: 原始的中间输出列表

    Returns:
        优化后的检索结果列表
    """
    optimized_results = []

    for item in intermediate_outputs:
        if not item or item == [] or item == {}:
            continue

        # 检查是否是搜索结果类型
        if isinstance(item, dict) and item.get('type') == 'search_result':
            raw_results = item.get('raw_results', {})

            # 如果 raw_results 为空，跳过
            if not raw_results or raw_results == [] or raw_results == {}:
                continue

            # 创建优化后的结果结构
            optimized_item = {
                "type": "search_result",
                "title": f"检索结果 ({item.get('index', 1)}/{item.get('total', 1)})",
                "query": item.get('query', ''),
                "raw_results": {},
                "index": item.get('index', 1),
                "total": item.get('total', 1)
            }

            # 合并所有搜索结果类型到一个 raw_results 中
            merged_raw_results = {}

            # 处理 time_search
            if 'time_search' in raw_results and raw_results['time_search']:
                merged_raw_results['time_search'] = raw_results['time_search']

            # 处理 keyword_search
            if 'keyword_search' in raw_results and raw_results['keyword_search']:
                merged_raw_results['keyword_search'] = raw_results['keyword_search']

            # 处理 embedding_search
            if 'embedding_search' in raw_results and raw_results['embedding_search']:
                merged_raw_results['embedding_search'] = raw_results['embedding_search']

            # 处理 combined_summary
            if 'combined_summary' in raw_results and raw_results['combined_summary']:
                merged_raw_results['combined_summary'] = raw_results['combined_summary']

            # 处理 reranked_results
            if 'reranked_results' in raw_results and raw_results['reranked_results']:
                merged_raw_results['reranked_results'] = raw_results['reranked_results']

            # 如果合并后的结果不为空，添加到优化结果中
            if merged_raw_results:
                optimized_item['raw_results'] = merged_raw_results
                optimized_results.append(optimized_item)
        else:
            # 非搜索结果类型，直接添加
            optimized_results.append(item)

    return optimized_results


def merge_multiple_search_results(intermediate_outputs):
    """
    将多个搜索结果合并为一个统一的搜索结果

    Args:
        intermediate_outputs: 原始的中间输出列表

    Returns:
        合并后的结果列表
    """
    search_results = []
    other_results = []

    # 分离搜索结果和其他结果
    for item in intermediate_outputs:
        if isinstance(item, dict) and item.get('type') == 'search_result':
            raw_results = item.get('raw_results', {})
            # 只保留有内容的搜索结果
            if raw_results and raw_results != [] and raw_results != {}:
                search_results.append(item)
        else:
            other_results.append(item)

    # 如果没有搜索结果，返回原始结果
    if not search_results:
        return intermediate_outputs

    # 如果只有一个搜索结果，优化格式后返回
    if len(search_results) == 1:
        optimized = optimize_search_results(search_results)
        return other_results + optimized

    # 合并多个搜索结果
    merged_raw_results = {}
    all_queries = []

    for result in search_results:
        query = result.get('query', '')
        if query:
            all_queries.append(query)

        raw_results = result.get('raw_results', {})

        # 合并各种搜索类型的结果
        for search_type in ['time_search', 'keyword_search', 'embedding_search', 'combined_summary',
                            'reranked_results']:
            if search_type in raw_results and raw_results[search_type]:
                if search_type not in merged_raw_results:
                    merged_raw_results[search_type] = raw_results[search_type]
                else:
                    # 如果是字典类型，需要合并
                    if isinstance(raw_results[search_type], dict) and isinstance(merged_raw_results[search_type], dict):
                        for key, value in raw_results[search_type].items():
                            if key not in merged_raw_results[search_type]:
                                merged_raw_results[search_type][key] = value
                            elif isinstance(value, list) and isinstance(merged_raw_results[search_type][key], list):
                                merged_raw_results[search_type][key].extend(value)
                    elif isinstance(raw_results[search_type], list):
                        if isinstance(merged_raw_results[search_type], list):
                            merged_raw_results[search_type].extend(raw_results[search_type])
                        else:
                            merged_raw_results[search_type] = raw_results[search_type]

    # 创建合并后的结果
    if merged_raw_results:
        merged_result = {
            "type": "search_result",
            "title": f"合并检索结果 (共{len(search_results)}个查询)",
            "query": " | ".join(all_queries),
            "raw_results": merged_raw_results,
            "index": 1,
            "total": 1
        }
        return other_results + [merged_result]

    return other_results
