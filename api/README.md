# Memory 模块项目

## 项目简�?

本项目是一个知识提取和图数据库管理系统，负责从对话数据中提取知识、生成嵌入向量、构建知识图谱，并提供混合搜索功能�?

## 核心功能

- **知识提取**: 从对话中提取陈述句、三元组、时间信息和嵌入向量
- **图数据库管理**: 使用 Neo4j 存储和管理知识图�?
- **混合搜索**: 结合关键词搜索和语义搜索
- **遗忘机制**: 模拟人类记忆衰减
- **自我反�?*: 对已存储的记忆进行反思和优化

## 架构设计

Memory 模块采用三大引擎架构�?

1. **萃取引擎（Extraction Engine�?*: 负责知识提取、预处理、去重消�?
2. **遗忘引擎（Forgetting Engine�?*: 负责记忆遗忘机制
3. **自我反思引擎（Reflection Engine�?*: 负责自我反思和优化

详细架构请参�?[架构文档](docs/memory_refactoring_architecture.md)

## 快速开�?

### 1. 环境要求

- Python 3.9+
- PostgreSQL 13+
- Neo4j 4.4+
- Redis 6.0+

### 2. 安装依赖

```bash
# 克隆项目
git clone <repository-url>
cd <project-directory>

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置环境变量

创建 `.env` 文件并配置以下参数：

```env
# 数据库配�?
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your-password
DB_NAME=memory_db

# Neo4j 配置
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password

# Redis 配置
REDIS_HOST=localhost
REDIS_PORT=6379

# Elasticsearch 配置（可选）
ELASTICSEARCH_HOST=localhost:9200

# LLM 配置
LLM_MODEL_NAME=gpt-4
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=your-api-key

# 嵌入模型配置
EMBEDDING_MODEL_NAME=text-embedding-ada-002
EMBEDDING_DIMENSION=1536

# 日志配置
LOG_DIR=logs
LOG_LEVEL=INFO
```

### 4. 初始化数据库

```bash
# 运行数据库迁�?
alembic upgrade head

# 初始�?Neo4j 索引
python -m app.core.memory.utils.init_neo4j
```

### 5. 启动服务

```bash
# 启动开发服务器
uvicorn app.main:app --reload --port 8000

# 访问 API 文档
# http://localhost:8000/docs
```

## 项目结构

```
app/core/memory/
├── models/                    # 数据模型�?
├── storage_services/          # 业务逻辑层（三大引擎�?
�?  ├── extraction_engine/     # 萃取引擎
�?  ├── forgetting_engine/     # 遗忘引擎
�?  ├── reflection_engine/     # 反思引�?
�?  └── search/                # 搜索服务
├── llm_tools/                 # LLM 工具�?
├── config/                    # 配置管理
├── utils/                     # 工具函数
└── agent/                     # Agent 功能

app/repositories/              # 数据访问�?
├── neo4j/                     # Neo4j 仓储
└── postgresql/                # PostgreSQL 仓储

logs/                          # 日志和输出目�?
└── memory-output/             # Memory 模块输出
```

详细结构请参�?[架构文档](docs/memory_refactoring_architecture.md)

## API 文档

完整�?API 文档请参�?[API 接口文档](docs/memory_refactoring_api.md)

### 主要 API 端点

- `POST /api/v1/memory/extract` - 提取对话知识
- `POST /api/v1/memory/search/hybrid` - 混合搜索
- `POST /api/v1/memory/forgetting/apply` - 应用遗忘机制
- `POST /api/v1/memory/reflection/run` - 运行自我反�?
- `GET /api/v1/memory/statistics` - 获取记忆统计

## 开发指�?

详细的开发指南请参�?[开发指南](docs/memory_refactoring_development_guide.md)

### 代码规范

- 遵循 PEP 8 规范
- 所有函数都有类型注�?
- 所有公共函数都有文档字符串
- 使用异步编程处理 I/O 操作

### 测试

```bash
# 运行所有测�?
pytest

# 运行特定测试
pytest tests/unit/test_extraction.py

# 生成覆盖率报�?
pytest --cov=app.core.memory --cov-report=html
```

## 输出路径说明

Memory 模块的所有输出文件统一存放�?`logs/memory-output/` 目录�?

详细说明请参�?[输出路径文档](docs/memory_output_paths.md)

### 主要输出文件

- `chunker_test_output.txt` - 分块测试输出
- `preprocessed_data.json` - 预处理数�?
- `statements_output.txt` - 陈述句提取输�?
- `triplets_output.txt` - 三元组提取输�?
- `extracted_result_summary.txt` - 提取结果摘要

## 配置说明

### 模型配置

�?`app/core/memory/config/runtime.json` 中配置模�?ID�?

```json
{
  "llm_id": "your-llm-model-id",
  "embedding_id": "your-embedding-model-id"
}
```

### 全局配置

通过 `app/core/config.py` 管理全局配置�?

```python
from app.core.config import settings

# 访问配置
print(settings.LLM_MODEL_NAME)
print(settings.memory_output_dir)
```

## 部署

### Docker 部署

```bash
# 构建镜像
docker build -t memory-app:latest .

# 运行容器
docker-compose up -d
```

### 生产环境配置

```bash
# 设置环境变量
export LOG_LEVEL=WARNING
export DB_HOST=production-db-host
export NEO4J_URI=bolt://production-neo4j:7687

# 启动服务
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

## 监控和维�?

### 日志管理

- 应用日志: `logs/app.log`
- 性能日志: `logs/time.log`
- 提示词日�? `logs/prompts/`
- Memory 输出: `logs/memory-output/`

### 定期维护

```bash
# 清理旧日志（保留 30 天）
find logs/ -type f -mtime +30 -delete

# 备份输出文件
tar -czf memory-output-backup-$(date +%Y%m%d).tar.gz logs/memory-output/

# 检查磁盘使�?
du -sh logs/
```

## 故障排查

### 常见问题

1. **LLM 调用超时**: 增加 `timeout` 配置或使用重试机�?
2. **Neo4j 连接失败**: 检�?`NEO4J_URI` 和认证信�?
3. **内存不足**: 使用批量处理减少内存占用
4. **输出文件路径错误**: 使用 `settings` 对象访问路径

详细故障排查请参�?[开发指南](docs/memory_refactoring_development_guide.md#常见问题)

## 贡献指南

欢迎贡献代码！请遵循以下步骤�?

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/your-feature`)
3. 提交更改 (`git commit -m 'feat: add some feature'`)
4. 推送到分支 (`git push origin feature/your-feature`)
5. 创建 Pull Request

详细贡献指南请参�?[开发指南](docs/memory_refactoring_development_guide.md#贡献指南)

## 文档

- [架构文档](docs/memory_refactoring_architecture.md) - 详细的架构设�?
- [API 文档](docs/memory_refactoring_api.md) - 完整�?API 接口说明
- [开发指南](docs/memory_refactoring_development_guide.md) - 开发者指南和最佳实�?
- [输出路径文档](docs/memory_output_paths.md) - 输出文件路径说明

## 许可�?

[MIT License](LICENSE)

## 联系方式

- 邮箱: support@example.com
- 文档: https://docs.example.com/memory
- 问题反馈: https://github.com/your-repo/issues

## 更新历史

- **v1.0.0** (2024-01-20): 完成 Memory 模块重构
  - 实现三大引擎架构
  - 统一输出路径管理
  - 完善文档和测�?
