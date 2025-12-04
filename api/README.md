# Memory Bear 前端项目

## 快速开始

### 环境要求

- Python 3.12
- PostgreSQL 13+
- Neo4j 4.4+
- Redis 6.0+

### 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 方式一：基于 pyproject 安装
pip install .

# 方式二：使用 requirements.txt
pip install -r requirements.txt
```

### 配置环境变量

创建 `.env` 文件（示例）：

```env
# Postgres
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your-password
DB_NAME=redbear-mem
DB_AUTO_UPGRADE=false

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password

# Redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=1

# LLM / API Keys（按需）
OPENAI_API_KEY=your-openai-key
DASHSCOPE_API_KEY=your-dashscope-key

# 其他
WEB_URL=http://localhost:3000
LOG_LEVEL=INFO
```

### 初始化与启动

```bash
# 如需自动迁移数据库：设置 DB_AUTO_UPGRADE=true 或手动执行
alembic upgrade head

# 激活虚拟环境
api\.venv\Scripts\activate

# 目录切换到api下
cd api


# 启动开发服务
uvicorn app.main:app --reload --port 8000

# 打开交互文档
# http://localhost:8000/docs
```

## 项目结构

```
app/
├── main.py                  # FastAPI 入口
├── controllers/             # 控制器与路由
├── core/                    # 核心：配置、异常、日志等
│   └── memory/              # 记忆模块
│       ├── storage_services/  # 萃取/遗忘/反思/检索
│       ├── agent/             # Agent + MCP 服务
│       ├── utils/             # 工具与提示词
│       └── models/            # 领域模型
└── rag/                     # RAG 能力与文档解析

logs/                        # 日志与输出
LICENSE                      # 许可协议（Apache-2.0）
README.md                    # 项目说明
```

## API 与路由

- 管理端：`/api`（JWT 认证）
- 服务端：`/v1`（API Key 认证）
- 根路由健康检查：`GET /` 返回运行状态
- Swagger 文档：`/docs`


## 部署建议

- 使用 `gunicorn` + `uvicorn.workers.UvicornWorker` 作为生产入口
- 配置 `LOG_LEVEL=WARNING` 并启用文件日志
- 数据库与缓存请使用托管服务或独立实例

示例：

```bash
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

## 许可证

本项目采用 Apache License 2.0 开源协议，详情见 `LICENSE`。

## 致谢与交流

- 问题反馈与讨论：请提交 Issue 到代码仓库
- 欢迎贡献：提交 PR 前请先创建功能分支并遵循常规提交信息格式
- 如感兴趣需要联络：tianyou_hubm@redbearai.com
