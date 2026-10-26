# memory-jobs

MemoryBear 的 **Celery 任务执行服务**。与 `api`、`memory-profile` 同级独立部署，负责消费
Celery 队列、执行后台任务（周期性扫描、记忆写入/遗忘/反思、文档解析等）。

服务提供 Celery 应用、同步基础设施客户端、任务基类、健康检查、Docker/k8s 骨架，
业务代码统一收在 `src/modules/` 下并按**功能**分包：`memory_insight`（记忆洞察）、
`user_summary`（用户摘要）、`interest_distribution`（兴趣分布）、`profile_cache`
（洞察 + 摘要的缓存编排），跨域共享的模型配置与数据访问在 `src/modules/shared/`。
`src/tasks/` 只放 Celery 适配层，生成算法与提示词都在各自的功能包里。

---

## 为什么全部使用同步客户端

Celery 的 prefork/pool 与 asyncio 事件循环天然冲突：worker 进程 fork 后继承的是父进程的
半初始化 loop，任何跨进程复用的 async 连接都会表现为 `Event loop is closed`、
`attached to a different loop` 或连接池脏连接。本服务因此**只使用同步驱动**：

| 依赖 | 客户端 | 模块 |
|------|--------|------|
| PostgreSQL | SQLAlchemy 2 + psycopg 3（sync） | `src/infrastructure/postgres.py` |
| Redis | `redis.Redis`（sync） | `src/infrastructure/redis.py` |
| Elasticsearch | `elasticsearch.Elasticsearch`（sync） | `src/infrastructure/elasticsearch.py` |
| Neo4j | `neo4j.GraphDatabase.driver`（sync） | `src/infrastructure/neo4j.py` |

所有客户端都是线程安全的，因此 `--pool=threads` 与 `--pool=prefork` 都可以直接使用。
`scripts/check_sync_only.py` 会在 CI 中拦截 async 驱动和跨服务 import。

## 目录结构

```
memory-jobs/
├── src/
│   ├── bootstrap.py            # .env 合并与 settings 单例（cwd 无关）
│   ├── config.py               # JobsSettings（pydantic-settings）
│   ├── celery_app.py           # Celery 应用、队列/超时/信号
│   ├── entry.py                # CLI 入口：src.entry:celery_app
│   ├── runtime.py              # 进程级基础设施组合（懒连接）
│   ├── queues.py               # 与 API 共享的队列名
│   ├── healthcheck.py          # 容器探针：python -m src.healthcheck
│   ├── common/                 # 日志、任务上下文、归一化、Jinja2 渲染
│   ├── infrastructure/         # 同步 DB / Redis / ES / Neo4j 管理器
│   ├── models/                 # 宿主表的 SQLAlchemy 映射
│   ├── modules/                # 业务功能，按功能分包
│   │   ├── memory_insight/     #   记忆洞察生成（算法 + 提示词）
│   │   ├── user_summary/       #   用户摘要 + 热门标签
│   │   ├── interest_distribution/  # 兴趣分布生成
│   │   ├── profile_cache/      #   洞察 + 摘要的缓存编排
│   │   └── shared/             #   跨域共享
│   │       ├── model_config.py #     工作空间 LLM 解析与阻塞模型构建
│   │       └── repositories/   #     数据访问：每个聚合一个模块
│   └── tasks/
│       ├── __init__.py         # 任务注册表（路由 + beat 排期）
│       ├── base.py             # JobTask：重试策略、db_session、客户端快捷方式
│       ├── health.py           # memory_jobs.health.ping 示例/冒烟任务
│       ├── profile_cache.py    # memory_jobs.profile_cache.generate[_workspace]
│       └── interest_distribution.py  # memory_jobs.interest_distribution.generate
├── tests/
├── scripts/check_sync_only.py
├── deploy/k8s/                 # worker / beat 部署清单
├── Dockerfile
└── .env.example
```

## 快速开始

```bash
cd memory-jobs
uv sync
cp .env.example .env        # 或直接复用仓库根目录 .env 中的共享基础设施配置
```

启动 worker（线程池）：

```bash
uv run celery -A src.entry:celery_app worker \
  --pool=threads --concurrency=16 \
  --queues=memory_tasks \
  --hostname=memory-jobs@%h
```

生成类任务（`profile_cache.*`、`interest_distribution.*`）以 LLM/Neo4j 等待为主，
推荐 gevent 池——等待期间让出 hub，同样的内存可以承载远多于线程的并发，而且
`time_limit` / `soft_time_limit` 在 gevent 下会真正生效（threads 池不支持超时）：

```bash
uv run celery -A src.gevent_entry:celery_app worker \
  --pool=gevent --concurrency=32 \
  --queues=memory_heavy_tasks \
  --hostname=memory-jobs@%h
```

`src/gevent_entry.py` 在导入 Celery app **之前**执行 `monkey.patch_all()`，因此
psycopg / redis-py / neo4j / httpx / urllib3 都建在 gevent 的绿色 socket 上。
psycopg 3 会检测到 select 已被 patch（`psycopg.waiting._is_select_patched`）并放弃
C 层阻塞的 `wait_c`。代价：任何**在 C 里阻塞**的调用会卡住该进程的全部 greenlet，
所以这个池只用于上述生成任务，不要在同一个 worker 里混跑其它重活。

启动周期性调度（**全局单副本**）：

```bash
uv run celery -A src.entry:celery_app beat --schedule=/tmp/celerybeat-schedule
```

冒烟测试：在任意 worker 队列上调用内置任务。

```bash
uv run celery -A src.entry:celery_app call memory_jobs.health.ping
# 或本地直接执行（不需要 broker）：
uv run python -c "from src.tasks.health import ping; print(ping.apply().get())"
```

依赖探针（容器 liveness/readiness 使用）：

```bash
uv run python -m src.healthcheck
# postgres=up redis=up elasticsearch=up neo4j=up
```

## 队列

队列名必须与 `api/app/celery_app.py` 保持一致，API 侧的生产者按这些名字投递：

| 队列 | 用途 | 建议池 |
|------|------|--------|
| `memory_tasks` | 常规记忆读写（IO 密集） | threads |
| `memory_fast_tasks` | 快速写入，独立避免阻塞 | threads |
| `memory_heavy_tasks` | 生成类（LLM 等待）/ 重任务 | gevent（生成类）或 prefork（CPU 密集） |
| `memory_projection` | outbox 投影 | threads |
| `document_tasks` | 文档解析 | prefork |
| `graphrag_tasks` | GraphRAG 构建 | prefork |
| `periodic_tasks` | 扫描器/定时派发 | prefork（低并发） |
| `reflection_tasks` | 反思执行 | prefork |
| `workflow_trigger_tasks` | 工作流触发 | prefork |

同一镜像可启动多个 Deployment，每个只消费一个队列（见 `deploy/k8s/worker.yaml` 的
`args`）。默认队列由 `JOBS_TASK_DEFAULT_QUEUE` 控制。

## 新增任务

1. 业务逻辑按**功能**建包放在 `src/modules/` 下（例如 `src/modules/memory_write/`：
   算法 + 提示词 + 提示词模板），`src/tasks/` 下只加对应的 Celery 适配模块
   （如 `src/tasks/memory_write.py`）。
2. 任务使用 `shared_task` + `JobTask` 基类，并显式命名（`memory_jobs.<domain>.<action>`）：

```python
from celery import shared_task

from src.runtime import get_runtime
from src.tasks.base import JobTask


@shared_task(bind=True, base=JobTask, name="memory_jobs.memory.write")
def write_memory(self, payload: dict) -> dict:
    # 事务边界由 db_session 管理：成功自动 commit，异常自动 rollback
    with self.db_session() as session:
        ...
    return {"ok": True}
```

3. 在 `src/tasks/__init__.py` 中显式 import 该模块，并登记路由 / beat 排期：

```python
from src.tasks.memory import write

TASK_ROUTES = {
    "memory_jobs.memory.write": "memory_tasks",
}

BEAT_SCHEDULE = {
    "memory-write-scan": {
        "task": "memory_jobs.memory.scan",
        "schedule": 60.0,
        "options": {"queue": "periodic_tasks", "expires": 55},
    },
}
```

约定：

- **同步到底**：任务函数、仓储、模型调用链中不得出现 `async def` / `await`。
- **不 import `api` / `app`**：两个服务独立部署，共享代码应下沉到 `packages/`。
- **幂等**：`task_acks_late=True` + 重试意味着任务可能被执行多次。
- **事务**：使用 `self.db_session()`，不要在任务里手动 `commit()`。
- **重试**：基类只对 `OperationalError`（连接类故障）自动重试；业务失败由任务自行判断。
- 参数与返回值必须是 JSON 可序列化类型（`task_serializer=json`）。

## 基础设施生命周期

- **懒连接**：`JobsRuntime` 在首次使用时才创建引擎/客户端，import 与构造阶段不建立连接。
- **fork 安全**：`worker_process_init` 信号在子进程中调用 `runtime.reset_after_fork()`，
  丢弃父进程继承的连接（SQLAlchemy 使用 `dispose(close=False)`），随后子进程按需重建。
- **关闭**：`worker_process_shutdown` / `worker_shutdown` 关闭全部客户端。
- **事务清理**：`PostgresManager.session()` 成功 commit、异常 rollback，并在 finally 中
  close，避免 `idle in transaction`。
- **Redis visibility timeout**：自动设置为 `JOBS_TASK_TIME_LIMIT + 600`，大于硬超时，
  避免 `acks_late` 下仍在运行的任务被重复投递。

## 配置

完整列表见 `.env.example`。优先级：进程环境变量 > `memory-jobs/.env` > 仓库根 `.env`。

关键点：

- `DATABASE_*` / `REDIS_*` / `ELASTICSEARCH_*` / `NEO4J_*` 与 API 同名，可复用同一份部署配置。
- `DATABASE_URL` 允许是 `postgresql+asyncpg://`，服务会自动改写为 `postgresql+psycopg://`。
- `REDIS_DB_CELERY_BROKER=3`、`REDIS_DB_CELERY_BACKEND=4` 必须与 API 一致。
- 不要设置裸的 `BROKER_URL` / `RESULT_BACKEND`：会被 Celery 的 Click CLI 劫持。
- `JOBS_STARTUP_DEPENDENCY_CHECK=true` 可让 worker 在依赖不可用时启动失败（默认关闭，
  避免瞬时故障导致 worker 无法就绪）。
- `JOBS_HEALTHCHECK_TIMEOUT_SECONDS` 只约束探针请求（默认 3s），不影响任务的正常超时与重试。

## 测试与检查

```bash
cd memory-jobs
uv run pytest
uv run ruff check src tests scripts
uv run python scripts/check_sync_only.py
```

## Docker / 部署

构建上下文必须是仓库根目录：

```bash
docker build -f memory-jobs/Dockerfile -t harbor.rboa.redbearai.com/rb/memory-jobs:<tag> .
```

- `deploy/k8s/worker.yaml`：worker Deployment，`args` 中按队列切分，可复制多份。
- `deploy/k8s/beat.yaml`：beat Deployment，**replicas 必须为 1**。
- 两者都通过 `envFrom` 注入共享基础设施配置，探针使用 `python -m src.healthcheck`。
