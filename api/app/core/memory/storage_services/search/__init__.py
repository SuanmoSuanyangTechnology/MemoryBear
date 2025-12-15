# -*- coding: utf-8 -*-
"""搜索服务模块

本模块提供统一的搜索服务接口，支持关键词搜索、语义搜索和混合搜索。
"""

from app.core.memory.storage_services.search.search_strategy import SearchStrategy, SearchResult
from app.core.memory.storage_services.search.keyword_search import KeywordSearchStrategy
from app.core.memory.storage_services.search.semantic_search import SemanticSearchStrategy
from app.core.memory.storage_services.search.hybrid_search import HybridSearchStrategy

__all__ = [
    "SearchStrategy",
    "SearchResult",
    "KeywordSearchStrategy",
    "SemanticSearchStrategy",
    "HybridSearchStrategy",
]


# ============================================================================
# 向后兼容的函数式API
# ============================================================================
# 为了兼容旧代码，提供与 src/search.py 相同的函数式接口


async def run_hybrid_search(
    query_text: str,
    search_type: str = "hybrid",
    group_id: str | None = None,
    apply_id: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
    include: list[str] | None = None,
    alpha: float = 0.6,
    use_forgetting_curve: bool = False,
    embedding_id: str | None = None,
    **kwargs
) -> dict:
    """运行混合搜索（向后兼容的函数式API）
    
    这是一个向后兼容的包装函数，将旧的函数式API转换为新的基于类的API。
    
    Args:
        query_text: 查询文本
        search_type: 搜索类型（"hybrid", "keyword", "semantic"）
        group_id: 组ID过滤
        apply_id: 应用ID过滤
        user_id: 用户ID过滤
        limit: 每个类别的最大结果数
        include: 要包含的搜索类别列表
        alpha: BM25分数权重（0.0-1.0）
        use_forgetting_curve: 是否使用遗忘曲线
        embedding_id: 嵌入模型ID
        **kwargs: 其他参数
        
    Returns:
        dict: 搜索结果字典，格式与旧API兼容
    """
    from app.repositories.neo4j.neo4j_connector import Neo4jConnector
    from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
    from app.core.memory.utils.config.config_utils import get_embedder_config
    from app.core.memory.utils.config import definitions as config_defs
    from app.core.models.base import RedBearModelConfig
    
    # 使用提供的embedding_id或默认值
    emb_id = embedding_id or config_defs.SELECTED_EMBEDDING_ID
    
    # 初始化客户端
    connector = Neo4jConnector()
    embedder_config_dict = get_embedder_config(emb_id)
    embedder_config = RedBearModelConfig(**embedder_config_dict)
    embedder_client = OpenAIEmbedderClient(embedder_config)
    
    try:
        # 根据搜索类型选择策略
        if search_type == "keyword":
            strategy = KeywordSearchStrategy(connector=connector)
        elif search_type == "semantic":
            strategy = SemanticSearchStrategy(
                connector=connector,
                embedder_client=embedder_client
            )
        else:  # hybrid
            strategy = HybridSearchStrategy(
                connector=connector,
                embedder_client=embedder_client,
                alpha=alpha,
                use_forgetting_curve=use_forgetting_curve
            )
        
        # 执行搜索
        result = await strategy.search(
            query_text=query_text,
            group_id=group_id,
            limit=limit,
            include=include,
            alpha=alpha,
            use_forgetting_curve=use_forgetting_curve,
            **kwargs
        )
        
        # 转换为旧格式
        result_dict = result.to_dict()
        
        # 保存到文件（如果指定了output_path）
        output_path = kwargs.get('output_path', 'search_results.json')
        if output_path:
            import json
            import os
            from datetime import datetime
            
            try:
                # 确保目录存在
                out_dir = os.path.dirname(output_path)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                
                # 保存结果
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result_dict, f, ensure_ascii=False, indent=2, default=str)
                print(f"Search results saved to {output_path}")
            except Exception as e:
                print(f"Error saving search results: {e}")
        return result_dict
        
    finally:
        await connector.close()


__all__.append("run_hybrid_search")
