# memory-jobs

MemoryBear 的 **Celery 任务执行服务**。与 `api`、`memory-profile` 同级独立部署，负责消费
Celery 队列、执行后台任务（周期性扫描、记忆写入/遗忘/反思、文档解析等）。

服务提供 Celery 应用、同步基础设施客户端、任务基类、健康检查、Docker/k8s 骨架，
业务代码统一收在 `src/modules/` 下并按**功能**分包：`memory_insight`（记忆洞察）、
`user_summary`（用户摘要）、`interest_distribution`（兴趣分布）、`profile_cache`
（洞察 + 摘要的缓存编排），跨域共享的模型配置与数据访问在 `src/modules/shared/`。
`src/tasks/` 只放 Celery 适配层，生成算法与提示词都在各自的功能包里。
适配层按**任务域**分包、每个 Celery 任务一个模块；定时任务（beat）统一登记在
`src/tasks/schedules.py`，不在别处写排期。

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

所有客户端都是线程安全的，因此 `--pool=threads` 与 `--pool=prefork` 都可以直接使用；
本服务统一用 `--pool=threads` 启动（镜像 CMD 与 `deploy/k8s/worker.yaml` 一致）。
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
│   ├── healthcheck.py          # 容器探针：python -m src.healthcheck
│   ├── common/                 # 日志、任务上下文、归一化
│   ├── prompts/                # 全部生成类提示词（单一入口 + templates/）
│   ├── infrastructure/         # 同步 DB / Redis / ES / Neo4j / model-service / 企业版服务客户端
│   ├── models/                 # 宿主表的 SQLAlchemy 映射
│   ├── modules/                # 业务功能，按功能分包
│   │   ├── memory_insight/     #   记忆洞察生成（算法 + 提示词）
│   │   ├── user_summary/       #   用户摘要 + 热门标签
│   │   ├── interest_distribution/  # 兴趣分布生成
│   │   ├── profile_cache/      #   洞察 + 摘要的缓存编排
│   │   └── shared/             #   跨域共享
│   │       ├── model_config.py #     工作空间 LLM 解析与阻塞模型构建
│   │       ├── scene_names.py  #     洞察输入的场景名投影（可选企业插件）
│   │       └── repositories/   #     数据访问：每个聚合一个模块
│   └── tasks/                  # Celery 适配层：每个任务一个模块
│       ├── __init__.py         # 任务注册表：import 各任务包 + 路由
│       ├── base.py             # JobTask：重试策略、db_session、客户端快捷方式
│       ├── schedules.py        # 定时任务（beat）唯一登记处
│       ├── health/
│       │   └── ping.py         # memory_jobs.health.ping 示例/冒烟任务
│       ├── interest_distribution/
│       │   ├── generate.py     # memory_jobs.interest_distribution.generate
│       │   ├── init_for_users.py  # memory_jobs.interest_distribution.init_for_users（dashboard 预热派发）
│       │   └── dispatch.py     # 错峰投递（预热 fan-out）
│       └── profile_cache/
│           ├── generate.py     # memory_jobs.profile_cache.generate
│           ├── generate_workspace.py  # memory_jobs.profile_cache.generate_workspace
│           ├── scan.py         # memory_jobs.profile_cache.scan（beat 定时扫描）
│           ├── dispatch.py     # 错峰投递（工作空间 fan-out 与 scan 共用）
│           └── inflight.py     # 每用户并发锁
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
  --pool=threads --concurrency=100 \
  --queues=memory_jobs_tasks \
  --hostname=memory-jobs@%h
```

`--concurrency` 就是线程数。任务绝大部分时间在等 LLM / Neo4j / PostgreSQL，同步客户端
阻塞的只是当前线程，因此一个进程内挂住上百个等待中的任务是正常的。镜像 CMD 与
`deploy/k8s/worker.yaml` 使用同一组参数（`--pool=threads --concurrency=100`），
改并发只需改这两处之一或直接在编排层覆盖 `command`。

两点约束：

- **threads 池不支持超时**：Celery 的线程池会丢弃 `time_limit` / `soft_time_limit`，
  任务的截止时间只能由下游客户端的超时兜住（`MODEL_SERVICE_INVOKE_*_TIMEOUT`、
  `ELASTICSEARCH_REQUEST_TIMEOUT`、`NEO4J_*_TIMEOUT`、`JOBS_REDIS_*_TIMEOUT`）。
  `JOBS_TASK_TIME_LIMIT` 仍需保留：Redis broker 下 `visibility_timeout` 由它推导，
  硬超时失效时这条线决定了消息多久后被重投（`acks_late` 下会重复执行）；RabbitMQ 在
  连接断开时立即重投，不看这个值。
- **连接池要能覆盖并发**：所有线程共享一个进程的 `PostgresManager` /
  `ElasticsearchManager` / Neo4j driver。`--concurrency` 调高时同步调高
  `JOBS_DATABASE_POOL_SIZE` + `JOBS_DATABASE_MAX_OVERFLOW`、`NEO4J_MAX_POOL_SIZE`，
  否则线程会在池上排队直到 `JOBS_DATABASE_POOL_TIMEOUT_SECONDS`。

启动周期性调度（**全局单副本**）：

```bash
uv run celery -A src.entry:celery_app beat --schedule=/tmp/celerybeat-schedule
```

冒烟测试：在任意 worker 队列上调用内置任务。

```bash
uv run celery -A src.entry:celery_app call memory_jobs.health.ping
# 或本地直接执行（不需要 broker）：
uv run python -c "from src.tasks.health.ping import ping; print(ping.apply().get())"
```

依赖探针（容器 liveness/readiness 使用）：

```bash
uv run python -m src.healthcheck
# postgres=up redis=up elasticsearch=up neo4j=up
```

## 队列

`memory_jobs_tasks` 是本服务唯一的队列：所有任务都路由到这里，只有 memory-jobs
worker 消费它，因此 API 的 worker 不会收到自己没注册的 task 名。memory-profile
的投递端也指向这个队列。

任务名、队列名与 `task name -> queue` 表不在这里硬编码，而是来自共享契约
`task-bridge-sdk[mem-router]` 的 `task_bridge.mem_router`（`TaskName` /
`QueueName` / `TASK_ROUTES`）：memory-jobs 用它注册任务和渲染 `task_routes`，
memory-profile 用同一份表投递，改一边不改另一边会在测试里直接失败。

下表其余队列名与 `api/app/celery_app.py` 保持一致，仅作为 API 的队列名镜像保留
（仅在本文记录），本服务不再向它们投递：

| 队列 | 用途 | 建议池 |
|------|------|--------|
| `memory_jobs_tasks` | **本服务全部任务**（生成 / 工作空间派发 / 周期扫描） | threads |
| `memory_tasks` | 常规记忆读写（IO 密集） | threads |
| `memory_fast_tasks` | 快速写入，独立避免阻塞 | threads |
| `memory_heavy_tasks` | 生成类（LLM 等待）/ 重任务 | threads（等待）或 prefork（CPU 密集） |
| `memory_projection` | outbox 投影 | threads |
| `document_tasks` | 文档解析 | prefork |
| `graphrag_tasks` | GraphRAG 构建 | prefork |
| `periodic_tasks` | 扫描器/定时派发 | prefork（低并发） |
| `reflection_tasks` | 反思执行 | prefork |
| `workflow_trigger_tasks` | 工作流触发 | prefork |

同一镜像可启动多个 Deployment，每个只消费一个队列（见 `deploy/k8s/worker.yaml` 的
`args`）。默认队列由 `JOBS_TASK_DEFAULT_QUEUE` 控制，默认值取自共享契约里的
`QueueName.MEMORY_JOBS`。

## 任务优先级

本服务跑在 RabbitMQ（AMQP）上：优先级就是消息的 `priority` 属性，**数字越大越紧急**
（`kombu/entity.py`：`0 to broker configured max priority, where higher is better`），
而且只有当队列声明时带了 `x-max-priority` 才生效——没有这个参数的 RabbitMQ 队列会忽略
消息的 priority 字段（同文件：`queues without a max priority set will ignore the
priority field`）。该参数由 Celery 的 `task_queue_max_priority` 写出，本服务用
`JOBS_TASK_QUEUE_MAX_PRIORITY` 配置；级别本身定义在共享契约
`task_bridge.mem_router`（`TaskPriority` / `MAX_PRIORITY`）。

| 级别 | 值 | 用于 |
|------|----|------|
| `INTERACTIVE` | 9 | 用户在同步等待：单用户缓存生成、兴趣分布（memory-profile 投递） |
| `NORMAL` | 5 | 内部默认值；工作空间扇出的每用户单元、dashboard 预热派发 |
| `BACKGROUND` | 0 | beat 定时扫描及其派发的单元 |

约定：

- **声明参数三端一致**：`memory_jobs_tasks` 由 worker 首次声明时带
  `x-max-priority=9`；memory-profile（task-bridge producer）与 API（dashboard 派发时
  显式 `declare=[Queue(..., {"x-max-priority": 9})]`）必须以同样参数声明。参数不同会被
  RabbitMQ 以 `PRECONDITION_FAILED` 拒绝，完全不带参数则优先级被静默忽略。
- **队列需要重建一次**：`x-max-priority` 只能在声明时指定。升级到本版本前先删除已存在
  的 `memory_jobs_tasks`（确认无在途消息），再滚动 worker，否则 worker 首次声明就会
  406；之后才发布 memory-profile / API。
- 派发点必须显式传 `priority`（`dispatch_generate(..., priority=...)`、
  `PeriodicTask(priority=...)`）；漏传时由 `task_default_priority` 落到 `NORMAL`。
- 优先级只决定**取消息的顺序**，不抢占正在执行的任务。已预取（reserved）的消息留在
  进程里，后到的高优先级消息插不到它们前面，所以预取窗口就是优先级响应的下限：
  `prefetch_count = --concurrency × JOBS_WORKER_PREFETCH_MULTIPLIER`
  （`celery/worker/components.py` 的 `Consumer.create`），当前 100 × 1 = 100 条。
  multiplier=1 已是最小可用值，设 0 会让 kombu 的 `QoS.can_consume` 直接放行
  （`not pcount` 短路），等于取消上限；要缩小预取窗口只能调低 `--concurrency`。
- **Redis broker 的差异**：Redis 没有 priority 字段，kombu 为 `priority_steps` 的每个
  bucket 建一个 list 并按升序 BRPOP，**数字小者先消费**，与 AMQP 相反。
  `priority_steps` 与 `visibility_timeout` 只在 Redis broker 下由 `src/celery_app.py`
  注入（`_broker_transport_options`）；把 broker 换回 Redis 时数值映射必须同时翻转。

## 新增任务

1. 业务逻辑按**功能**建包放在 `src/modules/` 下（例如 `src/modules/memory_write/`：
   算法 + 提示词 + 提示词模板）；`src/tasks/` 下按任务域建包，每个 Celery 任务
   一个模块（如 `src/tasks/memory_write/write.py`），并在该包的 `__init__.py` 里
   显式 import 任务模块完成注册。
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

3. 在 `src/tasks/__init__.py` 中 import 该任务包，并在 `TASK_ROUTES` 登记路由：

```python
from src.tasks import memory_write

TASK_ROUTES = {
    "memory_jobs.memory.write": "memory_jobs_tasks",
}
```

4. 需要周期性执行时，**只改** `src/tasks/schedules.py`：在 `periodic_tasks()` 中
   追加一条 `PeriodicTask`，`BEAT_SCHEDULE` 由它统一渲染：

```python
PeriodicTask(
    key="memory-write-scan",              # 全局唯一，即 beat 条目名
    task="memory_jobs.memory.write_scan",
    schedule=60.0,                        # 秒级间隔用 float；cron 用 crontab(...)，UTC
    enabled=settings.memory_write_scan_enabled,  # 开关绑定 JobsSettings 字段
    expires=55,                           # 扫描类任务：错过的这一轮不必补跑
    priority=TaskPriority.BACKGROUND,     # 扫描让位于用户触发的任务
)
```

登记约定：`key` 全局唯一；`enabled` 绑定 settings 开关而不是写死 `True`；
`queue` 默认就是本服务自己的队列，不需要单独指定。

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
- **Redis visibility timeout（仅 Redis broker）**：自动设置为
  `JOBS_TASK_TIME_LIMIT + 600`，大于硬超时，避免 `acks_late` 下仍在运行的任务被重复投递。

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
- **企业版服务（可选）**：企业版表（本体场景名等）由闭源 `memory-enterprise` 服务持有，
  包含表定义与迁移；本服务只通过 HTTP 读取，不接触企业 schema。
  `MEMORY_ENTERPRISE_BASE_URL` 指向该服务（例：`http://memory-enterprise:8083`），
  `MEMORY_ENTERPRISE_TIMEOUT_SECONDS` 默认 3s，`MEMORY_ENTERPRISE_TOKEN` 设置后以
  `X-Internal-Token` 发送。未配置时与「未装插件的 API」一致：洞察输入里 uuid 场景键
  视为已删除、整组丢弃。该服务**不参与**依赖探测，挂了只会丢场景名，不会让任务失败。
- `JOBS_INTEREST_CACHE_EXPIRE_SECONDS`（默认 24h）：预热单元写入兴趣分布缓存的 TTL。
  与 memory-profile 的 `PROFILE_INTEREST_CACHE_EXPIRE_SECONDS` 是同一个键（重启后者的
  环境变量即可同时生效），两个写入方用同一个过期时间。
- `JOBS_RESULT_BACKEND_THREAD_SAFE=true`（默认）：整个进程共用一份 result backend。
  Celery 默认把 backend 放在 `threading.local()` 里，线程池下每个线程都会建一份
  `RedisBackend`，各自带一个 redis 连接池；共享实例只保留一份。该键不在 Celery 的
  默认配置表里，只能通过 `celery_app.conf.update()` 生效。

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
- `memory_jobs_tasks` 必须以 `x-max-priority=9` 声明。升级前先删掉旧队列、再按
  worker → memory-profile → API 的顺序发布（见「任务优先级」）。
