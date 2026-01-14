
"""
Problem_Extension优化配置

在应用启动时应用这些优化配置
"""

# 优化配置
PROBLEM_EXTENSION_CONFIG = {
    # 缓存配置
    "cache_enabled": True,
    "cache_ttl": 3600,  # 1小时
    
    # 超时配置
    "llm_timeout": 8.0,  # 8秒超时
    "max_retries": 1,    # 最多重试1次
    
    # 批处理配置
    "max_questions_per_batch": 10,
    "batch_timeout": 15.0,
    
    # 性能监控
    "monitoring_enabled": True,
    "slow_query_threshold": 10.0,  # 10秒为慢查询
    
    # 连接池配置
    "client_pool_size": 3,
    
    # 简化模式
    "use_simplified_prompt": True,
    "skip_history_for_simple_queries": True,
}

def apply_optimizations():
    """应用优化配置"""
    import os
    
    # 设置环境变量
    for key, value in PROBLEM_EXTENSION_CONFIG.items():
        env_key = f"PROBLEM_EXTENSION_{key.upper()}"
        os.environ[env_key] = str(value)
    
    print("✅ Problem_Extension优化配置已应用")

if __name__ == "__main__":
    apply_optimizations()
