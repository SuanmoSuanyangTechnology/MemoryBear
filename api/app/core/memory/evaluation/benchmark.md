# 1.数据集下载地址
Locomo10.json : https://github.com/snap-research/locomo/tree/main/data
LongMemEval_oracle.json : https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
msc_self_instruct.jsonl : https://huggingface.co/datasets/MemGPT/MSC-Self-Instruct

数据集下载之后保存至api\app\core\memory\evaluation\dataset目录下
# 2.配置说明
文件api\app\core\memory\evaluation\.env.evaluation.example对三个基准测试所需配置有着详细的说明
**实际配置文件**：api\app\core\memory\evaluation\.env.evaluation
```python 
# 当使用不带配置参数的命令行执行基准测试，基准测试所需的配置参数根据.env.evaluation中的参数执行
python -m app.core.memory.evaluation.locomo.locomo_benchmark
```
**检查neo4j指定的grou_id是否摄入数据**
```python
# 1. 进入交互模式
python -m app.core.memory.evaluation.check_enduser_data

# 2. 选择 "1" 检查指定 group
# 3. 输入 group_id，例如: locomo_benchmark
# 4. 选择是否显示详细统计 (y/n)
```
# 3.locomo

### (1)locomo执行命令
```python
# 首先进入api目录
cd api

# 只摄入前5条消息，评估3个问题（最小测试）
python -m app.core.memory.evaluation.locomo.locomo_benchmark --sample_size 3 --max_ingest_messages 5

# 如果数据已经摄入，跳过摄入阶段直接测试(使用skip_ingest参数)
python -m app.core.memory.evaluation.locomo.locomo_benchmark --sample_size 5 --skip_ingest
```
### (2)locomo结果说明

#### 结果示例
```json
{
  "dataset": "locomo",
  "sample_size": 0,
  "timestamp": "2026-01-26T11:24:28.239156",
  "params": {
    "group_id": "locomo_benchmark",
    "search_type": "hybrid",
    "search_limit": 12,
    "context_char_budget": 8000,
    "llm_id": "2c9b0782-7a85-4740-ba84-4baf77f256c4",
    "embedding_id": "e2a6392d-ca63-4d59-a523-647420b59cb2"
  },
  "overall_metrics": {
    "f1": 0.0,
    "bleu1": 0.0,
    "jaccard": 0.0,
    "locomo_f1": 0.0
  },
  "by_category": {},
  "latency": {
    "search": {
      "mean": 0.0,
      "p50": 0.0,
      "p95": 0.0,
      "iqr": 0.0
    },
    "llm": {
      "mean": 0.0,
      "p50": 0.0,
      "p95": 0.0,
      "iqr": 0.0
    }
  },
  "context_stats": {
    "avg_retrieved_docs": 0.0,
    "avg_context_chars": 0.0,
    "avg_context_tokens": 0.0
  },
  "samples": []
}
```

#### 参数详解

##### 1. 核心评估指标 (overall_metrics)

**🎯 关键进步指标：**

- **`f1`** (F1 Score): 精确率和召回率的调和平均值
  - 范围：0.0 - 1.0
  - **越高越好**，衡量检索和生成答案的准确性
  - 这是最重要的综合性能指标
  - 优秀标准：> 0.85

- **`bleu1`** (BLEU-1): 单词级别的匹配度
  - 范围：0.0 - 1.0
  - **越高越好**，衡量生成答案与标准答案的词汇重叠度
  - 关注词汇层面的准确性

- **`jaccard`** (Jaccard 相似度): 集合相似度
  - 范围：0.0 - 1.0
  - **越高越好**，衡量答案集合的相似性
  - 计算公式：交集大小 / 并集大小

- **`locomo_f1`**: Locomo 特定的 F1 分数
  - 范围：0.0 - 1.0
  - **越高越好**，针对 Locomo 数据集优化的评估指标
  - 考虑了长对话记忆的特殊性

##### 2. 性能指标 (latency)

**⚡ 关键效率指标：**

- **`search`**: 检索延迟统计（单位：毫秒）
  - `mean`: 平均延迟
  - `p50`: 中位数延迟（50%的请求在此时间内完成）
  - `p95`: 95分位数延迟（95%的请求在此时间内完成）
  - `iqr`: 四分位距（Q3-Q1，衡量稳定性）
  - **越低越好**，衡量记忆检索速度
  - 优秀标准：p95 < 2000ms

- **`llm`**: LLM 推理延迟统计（单位：毫秒）
  - `mean`: 平均推理时间
  - `p50`: 中位数推理时间
  - `p95`: 95分位数推理时间
  - `iqr`: 四分位距（越小越稳定）
  - **越低越好**，衡量答案生成速度
  - 优秀标准：p95 < 3000ms

##### 3. 上下文统计 (context_stats)

**📊 资源效率指标：**

- **`avg_retrieved_docs`**: 平均检索文档数
  - 反映检索策略的广度
  - 需要平衡：太少可能信息不足，太多增加噪音和延迟
  - 建议范围：8-15 个文档

- **`avg_context_chars`**: 平均上下文字符数
  - 反映检索内容的总量
  - 应在满足准确性前提下尽量精简
  - 受 `context_char_budget` 参数限制

- **`avg_context_tokens`**: 平均上下文 token 数
  - **越低越好**（在保持准确性前提下）
  - 直接影响 API 调用成本和推理速度
  - 成本效益比 = f1 / avg_context_tokens

##### 4. 分类统计 (by_category)

- 按问题类型分类的性能指标
- 帮助识别系统在不同场景下的强弱项
- 可针对性优化特定类型的问题

#### 系统进步衡量标准

**一级指标（最重要）：**
- `f1` 和 `locomo_f1` 提升 → 核心能力提升
- 目标：f1 > 0.85

**二级指标（重要）：**
- `latency.p95` 降低 → 用户体验提升
- 目标：search.p95 < 2000ms, llm.p95 < 3000ms

**三级指标（辅助）：**
- `avg_context_tokens` 降低（在保持 f1 前提下）→ 成本优化
- `iqr` 降低 → 性能稳定性提升
# 4.longmemeval
支持时间推理问题的增强检索
### (1)执行命令
```python 
# 首先进入api目录
cd api

# 不带参数运行 - 使用环境变量 
python -m app.core.memory.evaluation.longmemeval.longmemeval_benchmark

# 命令行参数覆盖环境变量
python -m app.core.memory.evaluation.longmemeval.longmemeval_benchmark --sample-size 2

# 如果数据已经摄入，跳过摄入阶段直接测试(使用skip_ingest参数)
python -m app.core.memory.evaluation.longmemeval.longmemeval_benchmark --skip_ingest
```
### (2)结果说明

#### 结果示例
```json
{
  "dataset": "longmemeval",
  "items": 1,
  "accuracy_by_type": {
    "single-session-user": 1.0
  },
  "f1_by_type": {
    "single-session-user": 1.0
  },
  "jaccard_by_type": {
    "single-session-user": 1.0
  },
  "samples": [
    {
      "question": "What degree did I graduate with?",
      "prediction": "Business Administration",
      "answer": "Business Administration",
      "question_type": "single-session-user",
      "is_temporal": false,
      "question_id": "e47becba",
      "options": [],
      "context_count": 13,
      "context_chars": 1268,
      "retrieved_dialogue_count": 0,
      "retrieved_statement_count": 12,
      "metrics": {
        "exact_match": true,
        "f1": 1.0,
        "jaccard": 1.0
      },
      "timing": {
        "search_ms": 1483.100175857544,
        "llm_ms": 995.8682060241699
      }
    }
  ],
  "latency": {
    "search": {
      "mean": 1483.100175857544,
      "p50": 1483.100175857544,
      "p95": 1483.100175857544,
      "iqr": 0.0
    },
    "llm": {
      "mean": 995.8682060241699,
      "p50": 995.8682060241699,
      "p95": 995.8682060241699,
      "iqr": 0.0
    }
  },
  "context": {
    "avg_tokens": 204.0,
    "avg_chars": 1268,
    "count_avg": 13
  },
  "params": {
    "group_id": "longmemeval_zh_bak_3",
    "search_limit": 8,
    "context_char_budget": 4000,
    "search_type": "hybrid",
    "llm_id": "6dc52e1b-9cec-4194-af66-a74c6307fc3f",
    "embedding_id": "e2a6392d-ca63-4d59-a523-647420b59cb2",
    "sample_size": 1,
    "start_index": 0
  },
  "timestamp": "2026-01-24T21:36:10.818308",
  "metric_summary": {
    "score_accuracy": 100.0,
    "latency_median_s": 2.478968381881714,
    "latency_iqr_s": 0.0,
    "avg_context_tokens_k": 0.204
  },
  "diagnostics": {
    "duplicate_previews_top": [],
    "unique_preview_count": 1
  }
}
```

#### 参数详解

##### 1. 核心评估指标

**🎯 关键进步指标：**

- **`accuracy_by_type`**: 按问题类型分类的准确率
  - 范围：0.0 - 1.0
  - **越高越好**，1.0 表示 100% 准确
  - 问题类型包括：
    - `single-session-user`: 单会话用户信息
    - `single-session-event`: 单会话事件信息
    - `multi-session-user`: 多会话用户信息
    - `multi-session-event`: 多会话事件信息
  - 可以识别系统在不同场景下的强弱项

- **`f1_by_type`**: 按问题类型的 F1 分数
  - 范围：0.0 - 1.0
  - **越高越好**，综合评估精确率和召回率
  - 比单纯的准确率更全面

- **`jaccard_by_type`**: 按问题类型的 Jaccard 相似度
  - 范围：0.0 - 1.0
  - **越高越好**，衡量答案集合匹配度
  - 对于集合类答案特别有用

##### 2. 样本级指标 (samples)

**详细诊断指标：**

- **`metrics.exact_match`**: 精确匹配（布尔值）
  - **true 越多越好**，最严格的评估标准
  - 要求预测答案与标准答案完全一致

- **`metrics.f1`**: 单个样本的 F1 分数
  - 范围：0.0 - 1.0
  - **越高越好**，衡量单个问题的回答质量

- **`is_temporal`**: 是否为时间推理问题
  - 布尔值，标识问题是否涉及时间推理
  - 时间推理问题通常更具挑战性

- **`context_count`**: 检索到的上下文数量
  - 反映检索策略的有效性
  - 建议范围：8-15 个上下文片段

- **`retrieved_dialogue_count`**: 检索到的对话数
- **`retrieved_statement_count`**: 检索到的陈述数
  - 这两个指标帮助理解检索的内容类型分布
  - 可用于优化检索策略

- **`timing.search_ms`**: 单个问题的检索延迟（毫秒）
- **`timing.llm_ms`**: 单个问题的 LLM 推理延迟（毫秒）
  - **越低越好**，反映单次查询的响应速度

##### 3. 汇总指标 (metric_summary)

**📊 关键 KPI：**

- **`score_accuracy`**: 总体准确率百分比
  - 范围：0.0 - 100.0
  - **越高越好**，最直观的性能指标
  - 优秀标准：> 90.0

- **`latency_median_s`**: 中位延迟（秒）
  - **越低越好**，反映真实响应速度
  - 优秀标准：< 3.0 秒

- **`latency_iqr_s`**: 延迟四分位距（秒）
  - **越低越好**，反映性能稳定性
  - 越小说明响应时间越稳定

- **`avg_context_tokens_k`**: 平均上下文 token 数（千）
  - **越低越好**（在保持准确性前提下）
  - 直接影响 API 调用成本
  - 成本效益比 = score_accuracy / (avg_context_tokens_k * 1000)

##### 4. 上下文统计 (context)

- **`avg_tokens`**: 平均 token 数
- **`avg_chars`**: 平均字符数
- **`count_avg`**: 平均上下文片段数
  - 这些指标反映检索内容的规模
  - 需要在准确性和效率之间平衡

##### 5. 性能指标 (latency)

**⚡ 效率指标：**

- **`search`**: 检索延迟统计（单位：毫秒）
  - `mean`: 平均延迟
  - `p50`: 中位数延迟
  - `p95`: 95分位数延迟
  - `iqr`: 四分位距
  - **越低越好**，衡量记忆检索速度

- **`llm`**: LLM 推理延迟统计（单位：毫秒）
  - `mean`: 平均推理时间
  - `p50`: 中位数推理时间
  - `p95`: 95分位数推理时间
  - `iqr`: 四分位距
  - **越低越好**，衡量答案生成速度

##### 6. 诊断信息 (diagnostics)

- **`duplicate_previews_top`**: 重复预览统计
  - 列出出现频率最高的重复内容
  - 帮助发现检索冗余问题
  - 应该尽量减少重复

- **`unique_preview_count`**: 唯一预览数量
  - 反映检索多样性
  - **越高越好**，说明检索到的内容更丰富

#### 系统进步衡量标准

**一级指标（最重要）：**
- `score_accuracy` 提升 → 核心能力提升
- 目标：> 90.0%
- 各类型的 `accuracy_by_type` 均衡提升 → 全面能力提升

**二级指标（重要）：**
- `latency_median_s` 降低 → 用户体验提升
- 目标：< 3.0 秒
- `exact_match` 比例提升 → 精确度提升

**三级指标（辅助）：**
- `avg_context_tokens_k` 降低（在保持准确性前提下）→ 成本优化
- `unique_preview_count` 提升 → 检索多样性提升
- `latency_iqr_s` 降低 → 性能稳定性提升

**特殊关注：**
- 时间推理问题（`is_temporal: true`）的准确率
- 多会话问题的准确率（通常更具挑战性）
# 5.memsciqa
对话记忆检索评估
### (1)执行命令
```python
# 首先进入api目录
cd api

# 不带参数运行 - 使用环境变量 
python -m app.core.memory.evaluation.memsciqa.memsciqa_benchmark

# 命令行参数覆盖环境变量
python -m app.core.memory.evaluation.memsciqa.memsciqa_benchmark --sample-size 100

# 如果数据已经摄入，跳过摄入阶段直接测试(使用skip_ingest参数)
python -m app.core.memory.evaluation.memsciqa.memsciqa_benchmark --skip_ingest
```
### (2)结果说明

#### 结果示例
```json
{
  "dataset": "memsciqa",
  "items": 1,
  "metrics": {
    "accuracy": 0.0,
    "f1": 0.0,
    "bleu1": 0.0,
    "jaccard": 0.0
  },
  "latency": {
    "search": {
      "mean": 0.0,
      "p50": 0.0,
      "p95": 0.0,
      "iqr": 0.0
    },
    "llm": {
      "mean": 3067.7285194396973,
      "p50": 3067.7285194396973,
      "p95": 3067.7285194396973,
      "iqr": 0.0
    }
  },
  "avg_context_tokens": 4.0
}
```

#### 参数详解

##### 1. 核心评估指标 (metrics)

**🎯 关键进步指标：**

- **`accuracy`**: 准确率
  - 范围：0.0 - 1.0
  - **越高越好**，最直接的性能指标
  - 衡量系统回答正确的问题比例
  - 优秀标准：> 0.85

- **`f1`**: F1 分数
  - 范围：0.0 - 1.0
  - **越高越好**，平衡精确率和召回率
  - 计算公式：2 * (precision * recall) / (precision + recall)
  - 比单纯的准确率更全面，特别适合不平衡数据集

- **`bleu1`**: BLEU-1 分数
  - 范围：0.0 - 1.0
  - **越高越好**，衡量词汇级别的匹配度
  - 关注生成答案与标准答案的单词重叠
  - 源自机器翻译评估，适用于自然语言生成

- **`jaccard`**: Jaccard 相似度
  - 范围：0.0 - 1.0
  - **越高越好**，衡量集合相似性
  - 计算公式：|A ∩ B| / |A ∪ B|
  - 对于多答案或集合类问题特别有用

##### 2. 性能指标 (latency)

**⚡ 效率指标：**

- **`search`**: 检索延迟统计（单位：毫秒）
  - `mean`: 平均检索延迟
  - `p50`: 中位数延迟（50%的请求在此时间内完成）
  - `p95`: 95分位数延迟（95%的请求在此时间内完成）
  - `iqr`: 四分位距（Q3-Q1，衡量稳定性）
  - **越低越好**，衡量记忆检索效率
  - 优秀标准：p95 < 2000ms

- **`llm`**: LLM 推理延迟统计（单位：毫秒）
  - `mean`: 平均推理时间
  - `p50`: 中位数推理时间
  - `p95`: 95分位数推理时间
  - `iqr`: 四分位距（越小越稳定）
  - **越低越好**，衡量答案生成速度
  - 优秀标准：p95 < 3000ms
  - 注意：LLM 延迟通常占总延迟的大部分

##### 3. 资源指标

- **`avg_context_tokens`**: 平均上下文 token 数
  - **越低越好**（在保持准确性前提下）
  - 直接影响：
    - API 调用成本（按 token 计费）
    - 推理速度（token 越多越慢）
    - 上下文窗口占用
  - 成本效益比 = accuracy / avg_context_tokens
  - 建议范围：根据模型上下文窗口和成本预算调整

##### 4. 数据集特点

- **`items`**: 评估的问题数量
  - 样本量越大，评估结果越可靠
  - 建议至少 100 个样本以获得稳定的评估结果

- **对话记忆特性**：
  - MemSciQA 专注于对话历史中的记忆检索
  - 评估系统从多轮对话中提取和回忆信息的能力
  - 模拟真实的对话场景

#### 系统进步衡量标准

**一级指标（最重要）：**
- `accuracy` 提升 → 核心能力提升
- 目标：> 0.85
- `f1` 提升 → 综合性能提升
- 目标：> 0.80

**二级指标（重要）：**
- `latency.p95` 降低 → 用户体验提升
  - search.p95 目标：< 2000ms
  - llm.p95 目标：< 3000ms
- `iqr` 降低 → 性能稳定性提升

**三级指标（辅助）：**
- `avg_context_tokens` 降低（在保持准确性前提下）→ 成本优化
- `bleu1` 和 `jaccard` 提升 → 答案质量提升

**综合评估：**
- 成本效益比 = accuracy / avg_context_tokens
  - 该比值越高，说明系统在相同成本下性能越好
- 总延迟 = search.p95 + llm.p95
  - 应控制在 5 秒以内以保证良好的用户体验

#### 优化建议

**提升准确性：**
- 优化检索算法（调整 hybrid search 参数）
- 改进 embedding 模型质量
- 增加检索上下文数量（`search_limit`）
- 优化 prompt 工程

**提升效率：**
- 减少不必要的检索文档
- 使用更快的 LLM 模型或量化版本
- 实施缓存策略（相似问题复用结果）
- 优化数据库索引

**平衡性能：**
- 监控 accuracy vs latency 的权衡
- 监控 accuracy vs cost (tokens) 的权衡
- 根据业务需求调整优先级


---

# 6. 三个基准测试对比总结

## 6.1 测试特点对比

| 基准测试 | 主要评估目标 | 数据集特点 | 适用场景 |
|---------|------------|-----------|---------|
| **Locomo** | 长对话记忆检索 | 长对话历史，多轮交互 | 评估长期记忆保持和检索能力 |
| **LongMemEval** | 时间推理和多会话记忆 | 支持时间推理，多会话场景 | 评估时间感知和跨会话记忆能力 |
| **MemSciQA** | 对话记忆问答 | 对话历史问答 | 评估对话上下文理解和记忆提取 |

## 6.2 核心指标对比

### 准确性指标

| 指标 | Locomo | LongMemEval | MemSciQA | 说明 |
|-----|--------|-------------|----------|------|
| **F1 Score** | ✅ | ✅ | ✅ | 所有测试都使用，最重要的综合指标 |
| **Accuracy** | ❌ | ✅ | ✅ | 直观的准确率指标 |
| **BLEU-1** | ✅ | ❌ | ✅ | 词汇级别匹配度 |
| **Jaccard** | ✅ | ✅ | ✅ | 集合相似度 |
| **Exact Match** | ❌ | ✅ | ❌ | 最严格的评估标准 |

### 性能指标

所有三个测试都包含：
- **检索延迟** (search latency): mean, p50, p95, iqr
- **LLM 延迟** (llm latency): mean, p50, p95, iqr
- **上下文统计**: token 数、字符数、文档数

## 6.3 关键进步指标优先级

### 🥇 一级指标（必须关注）

1. **准确性指标**
   - Locomo: `f1`, `locomo_f1`
   - LongMemEval: `score_accuracy`, `accuracy_by_type`
   - MemSciQA: `accuracy`, `f1`
   - **目标**: > 85% 或 > 0.85

2. **综合性能**
   - 所有测试的 F1 分数应保持一致性
   - 不同类型问题的准确率应均衡

### 🥈 二级指标（重要）

3. **响应延迟**
   - `latency.p95` (95分位数延迟)
   - **目标**: 
     - search.p95 < 2000ms
     - llm.p95 < 3000ms
     - 总延迟 < 5000ms

4. **性能稳定性**
   - `iqr` (四分位距)
   - **目标**: 越小越好，说明性能稳定

### 🥉 三级指标（优化）

5. **成本效率**
   - `avg_context_tokens`
   - **目标**: 在保持准确性前提下最小化
   - 成本效益比 = accuracy / avg_context_tokens

6. **检索质量**
   - `avg_retrieved_docs` 的合理性
   - `unique_preview_count` (LongMemEval)
   - 检索内容的多样性和相关性

## 6.4 系统优化路径

### 阶段一：提升准确性（优先级最高）

**目标**: 所有测试的准确率 > 85%

**优化方向**:
1. 改进 embedding 模型质量
2. 优化检索算法（hybrid search 参数）
3. 增加检索上下文数量（`search_limit`）
4. 优化 prompt 工程
5. 改进记忆存储结构

**监控指标**:
- Locomo: `f1`, `locomo_f1`
- LongMemEval: `score_accuracy`, `exact_match` 比例
- MemSciQA: `accuracy`, `f1`

### 阶段二：优化性能（准确性达标后）

**目标**: p95 延迟 < 5 秒，性能稳定

**优化方向**:
1. 优化数据库索引和查询
2. 实施缓存策略
3. 使用更快的 LLM 模型
4. 并行化检索和推理
5. 减少不必要的检索

**监控指标**:
- `latency.p50`, `latency.p95`
- `iqr` (稳定性)
- 各阶段耗时分布

### 阶段三：降低成本（性能达标后）

**目标**: 在保持准确性和性能前提下，最小化成本

**优化方向**:
1. 精简检索上下文
2. 优化 context 选择策略
3. 使用更小的 LLM 模型
4. 实施智能缓存
5. 批处理优化

**监控指标**:
- `avg_context_tokens`
- 成本效益比 = accuracy / avg_context_tokens
- API 调用成本

## 6.5 评估最佳实践

### 测试执行建议

1. **初始测试**: 使用小样本快速验证
   ```bash
   --sample_size 10
   ```

2. **完整评估**: 使用足够大的样本量
   ```bash
   --sample_size 100  # 或更多
   ```

3. **增量测试**: 数据已摄入时跳过摄入阶段
   ```bash
   --skip_ingest
   ```

4. **参数调优**: 系统性地调整参数并记录结果
   - 调整 `search_limit`: 4, 8, 12, 16
   - 调整 `context_char_budget`: 2000, 4000, 8000
   - 尝试不同的 `search_type`: vector, keyword, hybrid

### 结果分析建议

1. **横向对比**: 比较三个测试的结果，识别系统的强弱项
2. **纵向对比**: 跟踪同一测试在不同版本的表现
3. **分类分析**: 关注不同问题类型的性能差异
4. **异常诊断**: 分析失败案例，找出根本原因

### 持续监控

建议建立监控仪表板，跟踪：
- 核心指标趋势（准确率、延迟）
- 成本效益比趋势
- 不同问题类型的性能分布
- 异常样本和失败模式

## 6.6 性能基准参考

### 优秀水平（Production Ready）

- **准确性**: accuracy/f1 > 0.90
- **延迟**: p95 < 3 秒
- **稳定性**: iqr < 500ms
- **成本效益**: accuracy/tokens > 0.0001

### 良好水平（Acceptable）

- **准确性**: accuracy/f1 > 0.85
- **延迟**: p95 < 5 秒
- **稳定性**: iqr < 1000ms
- **成本效益**: accuracy/tokens > 0.00005

### 需要改进（Below Target）

- **准确性**: accuracy/f1 < 0.85
- **延迟**: p95 > 5 秒
- **稳定性**: iqr > 1000ms
- **成本效益**: accuracy/tokens < 0.00005

---

**注**: 以上标准仅供参考，实际目标应根据具体业务需求和资源约束调整。
