# RAG Chunk 分析工具

这个模块提供了对 RAG chunk 内容进行分析的工具函数，包括：

## 功能模块

### 1. chunk_summary.py - Chunk 摘要生成
- `generate_chunk_summary(chunks, max_chunks=10)`: 为给定的 chunk 列表生成简洁摘要
- 使用 LLM 提取核心信息和关键要点
- 摘要长度控制在 100-150 字

### 2. chunk_tags.py - 标签提取
- `extract_chunk_tags(chunks, max_tags=10, max_chunks=10)`: 从 chunk 中提取关键标签
- `extract_chunk_tags_with_frequency(chunks, max_tags=10)`: 提取标签并统计频率
- 使用 LLM 识别核心概念和专业术语
- 自动过滤无意义词汇

### 3. chunk_insight.py - 洞察分析
- `generate_chunk_insight(chunks, max_chunks=15)`: 生成深度洞察报告
- `classify_chunk_domain(chunk)`: 对 chunk 进行领域分类
- `analyze_domain_distribution(chunks, max_chunks=20)`: 分析领域分布
- 提供内容的主题、特点和价值分析

## 使用示例

```python
from app.core.rag_utils import (
    generate_chunk_summary,
    extract_chunk_tags,
    generate_chunk_insight
)

# 示例 chunk 数据
chunks = [
    "机器学习是人工智能的一个重要分支...",
    "深度学习使用神经网络进行特征学习...",
    # ...
]

# 生成摘要
summary = await generate_chunk_summary(chunks, max_chunks=10)
print(f"摘要: {summary}")

# 提取标签
tags = await extract_chunk_tags(chunks, max_tags=10)
print(f"标签: {tags}")

# 生成洞察
insight = await generate_chunk_insight(chunks, max_chunks=15)
print(f"洞察: {insight}")
```

## API 接口

在 `memory_dashboard_controller.py` 中提供了两个对外接口：

### 1. GET /dashboard/chunk_summary_tag
获取 chunk 总结和提取的标签

**参数:**
- `end_user_id` (必填): 宿主ID
- `limit` (可选, 默认15): 返回的chunk数量
- `max_tags` (可选, 默认10): 最大标签数量

**返回:**
```json
{
    "code": 200,
    "msg": "chunk摘要和标签获取成功",
    "data": {
        "summary": "chunk内容的总结...",
        "tags": [
            {"tag": "机器学习", "frequency": 5},
            {"tag": "深度学习", "frequency": 3}
        ]
    }
}
```

### 2. GET /dashboard/chunk_insight
获取 chunk 的洞察内容

**参数:**
- `end_user_id` (必填): 宿主ID
- `limit` (可选, 默认15): 返回的chunk数量

**返回:**
```json
{
    "code": 200,
    "msg": "chunk洞察获取成功",
    "data": {
        "insight": "该知识库主要聚焦于技术领域(60%)..."
    }
}
```

## 技术特点

1. **异步处理**: 所有函数都是异步的，支持高并发
2. **LLM 驱动**: 使用大语言模型进行智能分析
3. **可配置**: 支持自定义处理的 chunk 数量和标签数量
4. **错误处理**: 完善的异常处理和日志记录
5. **模块化设计**: 每个功能独立，易于维护和扩展

## 依赖

- `app.core.memory.utils.llm_utils`: LLM 客户端
- `app.core.logging_config`: 日志配置
- `pydantic`: 数据验证和结构化输出

## 注意事项

1. 所有函数都需要在异步上下文中调用（使用 `await`）
2. 处理大量 chunk 时建议设置合理的 `max_chunks` 参数以控制 token 消耗
3. LLM 调用可能需要一定时间，建议在前端显示加载状态
