# Memory 模块工具函数文档

本目录包含 Memory 模块使用的所有工具函数，统一管理以提高代码可维护性和可复用性。

## 目录结构

```
app/core/memory/utils/
├── __init__.py                      # 包初始化文件，导出所有公共接口
├── README.md                        # 本文档
├── config/                          # 配置管理模块
│   ├── __init__.py                  # 配置模块初始化
│   ├── config_utils.py              # 配置管理工具
│   ├── definitions.py               # 全局定义和常量
│   ├── overrides.py                 # 运行时配置覆写
│   ├── get_data.py                  # 数据获取工具
│   ├── litellm_config.py            # LiteLLM 配置和监控
│   └── config_optimization.py       # 配置优化工具
├── log/                             # 日志管理模块
│   ├── __init__.py                  # 日志模块初始化
│   ├── logging_utils.py             # 日志工具
│   └── audit_logger.py              # 审计日志
├── prompt/                          # 提示词管理模块
│   ├── __init__.py                  # 提示词模块初始化
│   ├── prompt_utils.py              # 提示词渲染工具
│   ├── template_render.py           # 模板渲染工具
│   └── prompts/                     # Jinja2 提示词模板目录
│       ├── entity_dedup.jinja2      # 实体去重提示词
│       ├── extract_statement.jinja2 # 陈述句提取提示词
│       ├── extract_temporal.jinja2  # 时间信息提取提示词
│       ├── extract_triplet.jinja2   # 三元组提取提示词
│       ├── memory_summary.jinja2    # 记忆摘要提示词
│       ├── evaluate.jinja2          # 评估提示词
│       ├── reflexion.jinja2         # 反思提示词
│       ├── system.jinja2            # 系统提示词
│       └── user.jinja2              # 用户提示词
├── llm/                             # LLM 工具模块
│   ├── __init__.py                  # LLM 模块初始化
│   └── llm_utils.py                 # LLM 客户端工具
├── data/                            # 数据处理模块
│   ├── __init__.py                  # 数据模块初始化
│   ├── text_utils.py                # 文本处理工具
│   ├── time_utils.py                # 时间处理工具
│   └── ontology.py                  # 本体定义（谓语、标签等）
├── paths/                           # 路径管理模块
│   ├── __init__.py                  # 路径模块初始化
│   └── output_paths.py              # 输出路径管理
├── visualization/                   # 可视化模块
│   ├── __init__.py                  # 可视化模块初始化
│   └── forgetting_visualizer.py     # 遗忘曲线可视化
└── self_reflexion_utils/            # 自我反思工具模块
    ├── __init__.py                  # 反思模块初始化
    ├── evaluate.py                  # 冲突评估
    ├── reflexion.py                 # 反思处理
    └── self_reflexion.py            # 自我反思主逻辑
```

## 模块分类

### 1. 配置管理（config/）

配置管理模块包含所有与配置相关的工具函数和定义。

#### config_utils.py
提供配置加载和管理功能：
- `get_model_config(model_id)` - 获取 LLM 模型配置
- `get_embedder_config(embedding_id)` - 获取嵌入模型配置
- `get_neo4j_config()` - 获取 Neo4j 数据库配置
- `get_chunker_config(chunker_strategy)` - 获取分块策略配置
- `get_pipeline_config()` - 获取流水线配置
- `get_pruning_config()` - 获取语义剪枝配置
- `get_picture_config()` - 获取图片模型配置
- `get_voice_config()` - 获取语音模型配置

#### definitions.py
全局定义和常量：
- `CONFIG` - 基础配置（从 config.json 加载）
- `RUNTIME_CONFIG` - 运行时配置（从 runtime.json 或数据库加载）
- `PROJECT_ROOT` - 项目根目录路径
- 各种选择配置常量（LLM、嵌入模型、分块策略等）
- `reload_configuration_from_database(config_id)` - 动态重新加载配置

#### overrides.py
运行时配置覆写：
- `load_unified_config(project_root)` - 加载统一配置

#### get_data.py
数据获取工具：
- `get_data(host_id)` - 从 SQL 数据库获取数据

#### litellm_config.py
LiteLLM 配置和监控：
- `LiteLLMConfig` - LiteLLM 配置类
- `setup_litellm_enhanced(max_retries)` - 设置增强的 LiteLLM 配置
- `get_usage_summary()` - 获取使用统计摘要
- `print_usage_summary()` - 打印使用统计
- `get_instant_qps(module)` - 获取即时 QPS 数据
- `print_instant_qps(module)` - 打印即时 QPS 信息

#### config_optimization.py
配置优化工具：
- 配置参数优化相关功能

### 3. LLM 工具（llm/）

LLM 工具模块包含所有与 LLM 客户端相关的工具函数。

#### llm_utils.py
LLM 客户端工具：
- `get_llm_client(llm_id)` - 获取 LLM 客户端实例
- `get_reranker_client(rerank_id)` - 获取重排序客户端实例
- `handle_response(response)` - 处理 LLM 响应

#### litellm_config.py
LiteLLM 配置和监控：
- `LiteLLMConfig` - LiteLLM 配置类
- `setup_litellm_enhanced(max_retries)` - 设置增强的 LiteLLM 配置
- `get_usage_summary()` - 获取使用统计摘要
- `print_usage_summary()` - 打印使用统计
- `get_instant_qps(module)` - 获取即时 QPS 数据
- `print_instant_qps(module)` - 打印即时 QPS 信息

### 4. 提示词管理（prompt/）

提示词管理模块包含所有提示词渲染和模板管理相关的工具函数。

#### prompt_utils.py
提示词渲染工具（使用 Jinja2 模板）：
- `get_prompts(message)` - 获取系统和用户提示词
- `render_statement_extraction_prompt(...)` - 渲染陈述句提取提示词
- `render_temporal_extraction_prompt(...)` - 渲染时间信息提取提示词
- `render_entity_dedup_prompt(...)` - 渲染实体去重提示词
- `render_triplet_extraction_prompt(...)` - 渲染三元组提取提示词
- `render_memory_summary_prompt(...)` - 渲染记忆摘要提示词
- `prompt_env` - Jinja2 环境对象

#### template_render.py
模板渲染工具（用于评估和反思）：
- `render_evaluate_prompt(evaluate_data, schema)` - 渲染评估提示词
- `render_reflexion_prompt(data, schema)` - 渲染反思提示词

#### prompts/
Jinja2 模板文件目录，包含所有提示词模板

### 5. 数据处理（data/）

数据处理模块包含所有数据处理相关的工具函数。

#### text_utils.py
文本处理工具：
- `escape_lucene_query(query)` - 转义 Lucene 查询特殊字符
- `extract_plain_query(query_input)` - 从各种输入格式提取纯文本查询

#### time_utils.py
时间处理工具：
- `validate_date_format(date_str)` - 验证日期格式（YYYY-MM-DD）
- `normalize_date(date_str)` - 标准化日期格式
- `normalize_date_safe(date_str, default)` - 安全的日期标准化（带默认值）
- `preprocess_date_string(date_str)` - 预处理日期字符串

#### ontology.py
本体定义：
- `PREDICATE_DEFINITIONS` - 谓语定义字典
- `LABEL_DEFINITIONS` - 标签定义字典
- `Predicate` - 谓语枚举
- `StatementType` - 陈述句类型枚举
- `TemporalInfo` - 时间信息枚举
- `RelevenceInfo` - 相关性信息枚举

### 2. 日志管理（log/）

日志管理模块包含所有与日志记录相关的工具函数。

#### logging_utils.py
日志工具：
- `log_prompt_rendering(role, content)` - 记录提示词渲染
- `log_template_rendering(template_name, context)` - 记录模板渲染
- `log_time(operation, duration)` - 记录操作耗时
- `prompt_logger` - 提示词日志记录器

#### audit_logger.py
审计日志：
- `audit_logger` - 审计日志记录器
- 记录系统关键操作和安全事件

### 6. 自我反思工具（self_reflexion_utils/）

自我反思工具模块包含记忆冲突检测和反思处理功能。

#### evaluate.py
冲突评估：
- `conflict(evaluate_data, schema)` - 评估记忆冲突

#### reflexion.py
反思处理：
- `reflexion(data, schema)` - 执行反思处理

#### self_reflexion.py
自我反思主逻辑：
- `self_reflexion(...)` - 自我反思主函数

### 7. 数据模型

#### json_schema.py
JSON Schema 数据模型：
- `BaseDataSchema` - 基础数据模型
- `ConflictResultSchema` - 冲突结果模型
- `ConflictSchema` - 冲突模型
- `ReflexionSchema` - 反思模型
- `ResolvedSchema` - 解决方案模型
- `ReflexionResultSchema` - 反思结果模型

#### messages.py
API 消息模型：
- `ConfigKey` - 配置键模型
- `ChunkerStrategy` - 分块策略枚举
- `ConfigParams` - 配置参数模型
- `ConfigParamsCreate` - 创建配置参数模型
- `ConfigUpdate` - 更新配置模型
- `ConfigUpdateExtracted` - 更新萃取引擎配置模型
- `ConfigUpdateForget` - 更新遗忘引擎配置模型
- `ConfigPilotRun` - 试运行配置模型
- `ConfigFilter` - 配置过滤模型
- `ApiResponse` - API 响应模型
- `ok(msg, data)` - 成功响应构造函数
- `fail(msg, error_code, data)` - 失败响应构造函数

### 8. 可视化（visualization/）

可视化模块包含所有可视化相关的工具函数。

#### forgetting_visualizer.py
遗忘曲线可视化：
- `export_memory_curve_numpy(...)` - 导出记忆曲线为 NumPy 数组
- `export_memory_curves_multiple_strengths(...)` - 导出多个强度的记忆曲线
- `export_parameter_sweep_numpy(...)` - 导出参数扫描结果
- `visualize_forgetting_curve(...)` - 可视化遗忘曲线
- `plot_3d_forgetting_surface(...)` - 绘制 3D 遗忘曲线表面
- `create_comparison_visualization(...)` - 创建对比可视化
- `save_memory_curves_to_file(...)` - 保存记忆曲线到文件

### 9. 路径管理（paths/）

路径管理模块包含所有路径管理相关的工具函数。

#### output_paths.py
输出路径管理：
- `get_output_dir()` - 获取输出目录
- `get_output_path(filename)` - 获取输出文件路径

## 使用示例

### 配置管理

```python
from app.core.memory.utils.config import get_model_config, get_pipeline_config
from app.core.memory.utils.config.definitions import SELECTED_LLM_ID

# 获取模型配置
model_config = get_model_config("model_id_123")

# 获取流水线配置
pipeline_config = get_pipeline_config()

# 使用全局常量
llm_id = SELECTED_LLM_ID
```

### 日志管理

```python
from app.core.memory.utils.log import log_prompt_rendering, log_time, audit_logger

# 记录提示词渲染
log_prompt_rendering('user', 'Hello, world!')

# 记录操作耗时
log_time('extraction', 1.23)

# 使用审计日志
audit_logger.info('User action performed')
```

### LLM 工具

```python
from app.core.memory.utils.llm import get_llm_client

# 获取 LLM 客户端
llm_client = get_llm_client("llm_id_456")

# 调用 LLM
response = await llm_client.chat([
    {"role": "user", "content": "Hello"}
])
```

### 提示词渲染

```python
from app.core.memory.utils.prompt import render_statement_extraction_prompt
from app.core.memory.utils.data.ontology import LABEL_DEFINITIONS

# 渲染陈述句提取提示词
prompt = await render_statement_extraction_prompt(
    chunk_content="对话内容...",
    definitions=LABEL_DEFINITIONS,
    json_schema=schema,
    granularity=2
)
```

### 数据处理

```python
from app.core.memory.utils.data.time_utils import normalize_date
from app.core.memory.utils.data.text_utils import escape_lucene_query

# 标准化日期
normalized = normalize_date("2025/10/28")  # 返回 "2025-10-28"

# 转义 Lucene 查询
escaped = escape_lucene_query("user:admin AND status:active")
```

### 运行时配置覆写

```python
from app.core.memory.utils import apply_runtime_overrides_with_config_id

# 使用指定 config_id 覆写配置
runtime_cfg = {"selections": {}}
updated_cfg = apply_runtime_overrides_with_config_id(
    project_root="/path/to/project",
    runtime_cfg=runtime_cfg,
    config_id="config_123"
)
```

## 迁移说明

### 从旧路径迁移

如果你的代码使用了旧的导入路径，请按以下方式更新：

**旧路径（2024年11月之前）：**
```python
from app.core.memory.src.utils.config_utils import get_model_config
from app.core.memory.src.utils.prompt_utils import render_statement_extraction_prompt
from app.core.memory.src.data_config_api.utils.messages import ok, fail
```

**中间路径（2024年11月）：**
```python
from app.core.memory.utils.config_utils import get_model_config
from app.core.memory.utils.logging_utils import log_prompt_rendering
from app.schemas.memory_storage_schema import ok, fail
```

**新路径（2024年11月27日之后）：**
```python
# 配置相关
from app.core.memory.utils.config.config_utils import get_model_config
from app.core.memory.utils.config import get_model_config  # 简化导入

# 日志相关
from app.core.memory.utils.log.logging_utils import log_prompt_rendering
from app.core.memory.utils.log import log_prompt_rendering  # 简化导入

# 其他工具
from app.core.memory.utils import prompt_utils
from app.schemas.memory_storage_schema import ok, fail
```

### 目录结构重组（2024年11月27日）

utils 目录已按功能进行了完整的重组：

**重组前的结构：**
- 所有文件都在 `app/core/memory/utils/` 根目录下

**重组后的结构：**
- `config/` - 配置管理相关文件
- `log/` - 日志管理相关文件
- `prompt/` - 提示词管理相关文件
- `llm/` - LLM 工具相关文件
- `data/` - 数据处理相关文件
- `paths/` - 路径管理相关文件
- `visualization/` - 可视化相关文件
- `self_reflexion_utils/` - 自我反思工具（已存在）

**导入路径变化：**
```python
# 旧导入方式
from app.core.memory.utils.config_utils import get_model_config
from app.core.memory.utils.logging_utils import log_prompt_rendering
from app.core.memory.utils.prompt_utils import render_statement_extraction_prompt

# 新导入方式
from app.core.memory.utils.config.config_utils import get_model_config
from app.core.memory.utils.log.logging_utils import log_prompt_rendering
from app.core.memory.utils.prompt.prompt_utils import render_statement_extraction_prompt

# 或使用简化导入
from app.core.memory.utils.config import get_model_config
from app.core.memory.utils.log import log_prompt_rendering
from app.core.memory.utils.prompt import render_statement_extraction_prompt
```

## 维护指南

### 添加新工具函数

1. 在相应的模块文件中添加函数
2. 在 `__init__.py` 中导出函数
3. 在本 README 中添加文档
4. 编写单元测试

### 删除旧工具函数

1. 确认没有代码使用该函数
2. 从模块文件中删除函数
3. 从 `__init__.py` 中删除导出
4. 更新本 README

### 重构工具函数

1. 保持向后兼容性（使用别名或包装器）
2. 更新所有使用该函数的代码
3. 更新文档和测试
4. 在适当时机删除旧版本

## 注意事项

1. **向后兼容性**：所有工具函数应保持向后兼容，避免破坏现有代码
2. **文档完整性**：每个函数都应有清晰的文档字符串
3. **类型注解**：使用类型注解提高代码可读性
4. **错误处理**：工具函数应有适当的错误处理
5. **测试覆盖**：所有工具函数都应有单元测试

## 相关文档

- [Memory 模块架构设计](../.kiro/specs/memory-refactoring/design.md)
- [Memory 模块需求文档](../.kiro/specs/memory-refactoring/requirements.md)
- [Memory 模块任务列表](../.kiro/specs/memory-refactoring/tasks.md)
