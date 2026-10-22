# task-bridge-sdk

任务投递与状态查询的桥接 SDK：HTTP 服务不 import worker 的 Celery 应用模块（不注册任务、
不创建 worker 全局 app），仅凭**任务名**投递任务，并轮询任务结果。

投递/查询被抽象为 `TaskTransport`，**Celery 只是其中一个实现**（`CeleryTransport`）；
`TaskBridge` 只负责与机制无关的部分（参数校验、超时、轮询、抖动、同步/异步 API）。
后续接入 HTTP 任务服务、Redis Streams 等只需新增一个 transport。

```
HTTP service ──TaskBridge.submit(name, ...)──▶ TaskTransport ──▶ broker ──▶ worker
      │                                              │                        │
      └──── TaskBridge.await_result(task_id) ◀── get_status ◀── result backend ┘
```

默认装配：

```
TaskBridge
  └── CeleryTransport
        ├── bare Celery producer app（send_task）
        ├── Celery result backend（同步读取 / redis.asyncio 直读）
        └── Redis result pub/sub（完成唤醒）
```

## 目录结构

```
src/task_bridge/
├── __init__.py                 # 公共导出（TaskBridge / CeleryBridge / ...）
├── core/                       # 机制无关
│   ├── bridge.py               # TaskBridge：轮询、超时、抖动、sync/async API
│   ├── transport.py            # TaskTransport / TaskSubmission / TaskWatch / ThreadedTaskTransport
│   ├── status.py               # TaskStatus / TaskState
│   └── errors.py
└── transports/                 # 具体实现
    ├── __init__.py
    └── celery/
        ├── bridge.py           # CeleryBridge = TaskBridge + CeleryTransport
        ├── transport.py        # CeleryTransport（send_task / result backend）
        ├── config.py           # CeleryConfig（BridgeConfig 别名）
        └── redis_results.py    # Redis 结果直读 + pub/sub 通知
```

新增投递机制时只需在 `transports/` 下新建子包，`core/` 不需要改动。

## 能力

- **按名投递**：`submit("app.tasks.xxx", ...)`，无需声明任务函数。
- **裸 producer app**：只配置 broker / backend / JSON 协议；`set_as_current=False`，
  不会替换进程内的 `celery.current_app`；首次使用时才创建，import 不建立连接。
- **同步 + 异步**：`submit` / `wait_for_result` 与 `asubmit` / `await_result`。
- **异步直读 Redis 结果**：result backend 是 Redis 时，`aget_status` / `await_result` 走
  `redis.asyncio`，**完全不占工作线程**，取消可真正取消 socket 操作；其他后端自动回退到线程路径。
  异步客户端复用 Celery 解析后的 `connparams`（含 `redis_backend_use_ssl` 的证书/TLS 设置），
  不是只按 URL 建连。
- **完成即唤醒**：`await_result` 订阅 Celery Redis backend 的结果 pub/sub 通道，worker 写结果时
  立即唤醒；一个共享 PubSub 连接服务所有等待者，断线或不可用时退回轮询，正确性不依赖它。
- **批量状态**：`aget_status_many` / `get_status_many` 用一次 `MGET` 读取一页任务，200 条约 7ms。
- **结果轮询**：单次快照 `get_status`，或轮询到终态 `wait_for_result(timeout=...)`。
- **线程池可控**：`executor_workers` 给发布/回退路径一个专用线程池，与宿主的默认线程池隔离。
- **启动预热**：`warm_up(connections=N)` 提前建立结果后端连接，消除首轮并发建连成本。
- **明确的错误**：发布失败、结果不可读、等待超时、任务失败各有独立异常。
- **载荷前置校验**：发布前拒绝 JSON 协议无法承载的 payload（UUID/datetime 等），
  报错发生在业务请求内，而不是静默积压在 worker 侧。

## 安装

基础包只包含机制无关的抽象层，**没有任何第三方依赖**：

```toml
dependencies = ["task-bridge-sdk"]
```

使用 Celery 实现时安装 `celery` extra（同时带上 Redis 客户端，适配本仓库的 redis 6.x/8.x）：

```toml
dependencies = ["task-bridge-sdk[celery]"]
```

在宿主服务中通过路径依赖引入：

```toml
[tool.uv.sources]
task-bridge-sdk = { path = "../packages/task-bridge-sdk" }
```

> 不要使用 `celery[redis]`：kombu 的 `redis` extra 把 redis 限制在 `<=5.2.1`，与本仓库的
> redis 6.x/8.x 冲突。`task-bridge-sdk[celery]` 直接声明 `celery` + `redis>=6,<9`。

未安装 extra 时：

- `import task_bridge` 和 `task_bridge.core` 正常可用；
- 访问 `CeleryBridge` / `CeleryTransport` / `CeleryConfig` 会抛 `ImportError`，
  提示安装 `task-bridge-sdk[celery]`。

> 本地修改本包后，宿主服务需要重新同步（路径依赖按已安装副本解析）：
> `uv sync --reinstall-package task-bridge-sdk`

## 快速开始

以下示例按 `task-bridge-sdk[celery]` 安装。

```python
from task_bridge import CeleryBridge

# 从环境变量解析 broker/backend（见下文），整个进程复用一个实例
bridge = CeleryBridge.from_env()

task_id = bridge.submit(
    "app.tasks.do_refresh_user_tags",
    kwargs={"user_id": str(user_id)},
    queue="memory_heavy_tasks",   # 生产端路由，必须与 worker 的队列一致
)

# 异步等待（FastAPI 路由内）
status = await bridge.await_result(task_id, timeout=30, interval=0.5)
if status.successful:
    return status.result
if status.failed:
    raise RuntimeError(status.error)
if status.pending:
    # 仍未被 worker 消费：任务尚未开始，或结果已被 result_expires 清理
    ...
```

在 FastAPI lifespan 中持有单个实例：

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from task_bridge import CeleryBridge

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.task_bridge = CeleryBridge.from_env()
    # 可选：提前打开结果后端连接，避免首个并发请求承担建连成本
    # （connections 不能超过 async_redis_max_connections，默认 50）
    await app.state.task_bridge.awarm_up(connections=8)
    try:
        yield
    finally:
        await app.state.task_bridge.aclose()

app = FastAPI(lifespan=lifespan)
```

把它暴露成状态查询接口：

```python
from fastapi import Request

@app.get("/tasks/{task_id}")
async def task_status(task_id: str, request: Request):
    status = await request.app.state.task_bridge.aget_status(task_id)
    return {
        "task_id": status.task_id,
        "state": status.state,
        "ready": status.ready,
        "result": status.result,
        "error": status.error,
    }
```

显式配置：

```python
from task_bridge import CeleryBridge, CeleryConfig

bridge = CeleryBridge(CeleryConfig(
    broker_url="redis://redis:6379/3",
    result_backend="redis://redis:6379/4",
    default_queue="memory_tasks",
    task_routes={
        "app.tasks.do_refresh_user_tags": "memory_heavy_tasks",
        "app.core.rag.tasks.parse_document": "document_tasks",
    },
    # 可选：发布/回退路径使用专用线程池，避免与宿主争抢默认线程池
    executor_workers=16,
    # 可选：首轮并发发布时并行建立 broker 连接（Celery 默认 10）
    broker_pool_limit=32,
))
```

`BridgeConfig` 是 `CeleryConfig` 的旧名别名，两者等价。

## 扩展：自定义 transport

自定义 transport 只需要基础包（`task-bridge-sdk`），不需要安装 Celery。
`TaskBridge` 只依赖 `TaskTransport`，实现下面两个方法就是一个可用的投递机制：

```python
from task_bridge import TaskBridge, TaskStatus, TaskSubmission, TaskTransport


class HttpTaskTransport(TaskTransport):
    """把任务投递到另一个 HTTP 任务服务。"""

    def __init__(self, client):
        self._client = client

    def submit(self, submission: TaskSubmission) -> str:
        response = self._client.post("/tasks", json={
            "name": submission.task_name,
            "args": list(submission.args),
            "kwargs": dict(submission.kwargs),
            "queue": submission.queue,
        })
        response.raise_for_status()
        return response.json()["task_id"]

    def get_status(self, task_id: str) -> TaskStatus:
        payload = self._client.get(f"/tasks/{task_id}").json()
        return TaskStatus.from_meta(task_id, payload)


bridge = TaskBridge(HttpTaskTransport(client=...))
```

可选钩子：

| 成员 | 默认行为 | 何时覆盖 |
|------|----------|----------|
| `get_status_many(ids)` | 逐个 `get_status` | 后端支持批量查询（Celery 实现用 Redis `MGET`） |
| `revoke(task_id, ...)` | 抛 `NotImplementedError` | 机制支持取消 |
| `warm_up(*, connections)` | 无操作 | 需要预热连接 |
| `close()` / `asyncio` 版 `aclose()` | 无操作 | 需要释放连接/线程池 |
| `a*` 方法（`asubmit` 等） | 把同步方法放到线程执行 | 机制本身是异步 I/O（如 `httpx.AsyncClient`） |
| `watch(task_id)` | 返回 `None`（纯轮询） | 机制支持推送（如 Redis pub/sub），返回 `TaskWatch` |

异步实现示例（原生 async transport）：

```python
class AsyncHttpTaskTransport(TaskTransport):
    def submit(self, submission): ...          # 仍需实现同步版本
    def get_status(self, task_id): ...

    async def asubmit(self, submission):
        response = await self._client.post("/tasks", json=...)
        return response.json()["task_id"]

    async def aget_status(self, task_id):
        payload = (await self._client.get(f"/tasks/{task_id}")).json()
        return TaskStatus.from_meta(task_id, payload)
```

`TaskBridge` 提供 `submit` / `asubmit`、`get_status` / `aget_status`、
`get_status_many` / `aget_status_many`、`wait_for_result` / `await_result`、
`revoke` / `arevoke`、`warm_up` / `awarm_up`、`close` / `aclose`，与具体机制无关。

## 配置解析顺序

`CeleryConfig.from_env()` 与 `CeleryBridge.from_env()`：

| 顺序 | Broker | Result backend |
|------|--------|----------------|
| 1 | `TASK_BRIDGE_BROKER_URL` | `TASK_BRIDGE_RESULT_BACKEND` |
| 2 | `CELERY_BROKER_URL` | `CELERY_RESULT_BACKEND` |
| 3 | 由 `REDIS_URL` 或 `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` 派生 | 同左 |

派生时使用 `REDIS_DB_CELERY_BROKER`（默认 `3`）和 `REDIS_DB_CELERY_BACKEND`（默认 `4`），
与 `api/app/celery_app.py` 完全一致。`REDIS_URL` 若已配置，会保留其连接串并把 path 替换为目标库号，
避免误投到业务缓存库。`TASK_BRIDGE_BROKER_DB` / `TASK_BRIDGE_BACKEND_DB` 可单独覆盖库号。

其余变量：

| 变量 | 说明 |
|------|------|
| `TASK_BRIDGE_DEFAULT_QUEUE` | `submit` 未显式传 `queue` 时使用的队列 |
| `TASK_BRIDGE_TASK_ROUTES` | JSON 对象，`{"任务名": "队列名"}`，由 Celery 生产端路由 |
| `TASK_BRIDGE_VALIDATE_PAYLOAD` | `true`/`false`，默认 `true`，关闭 JSON 载荷前置校验 |
| `TASK_BRIDGE_ASYNC_REDIS_RESULTS` | `true`/`false`，默认 `true`，Redis 结果走 `redis.asyncio` |
| `TASK_BRIDGE_REDIS_PUBSUB` | `true`/`false`，默认 `true`，用结果 pub/sub 通道唤醒 `await_result` |
| `TASK_BRIDGE_ASYNC_REDIS_MAX_CONNECTIONS` | 异步**状态读取**连接池上限，默认 `50`；Pub/Sub 另有独立连接 |
| `TASK_BRIDGE_EXECUTOR_WORKERS` | 专用线程池大小；不设则用事件循环默认线程池 |
| `TASK_BRIDGE_BROKER_POOL_LIMIT` | Celery producer/连接池大小，默认 10；建议设为预期发布并发 |
| `TASK_BRIDGE_POLL_JITTER` | 轮询抖动比例，默认 `0.1`（在 interval 上叠加 0–10% 随机）；`0` 关闭 |

## API

### `TaskBridge` / `CeleryBridge`

`TaskBridge(transport)` 只依赖抽象层；`CeleryBridge` 等价于
`TaskBridge(CeleryTransport(...))`，并保留 `config` / `app` / `from_env` 便捷入口。

| 方法 | 说明 |
|------|------|
| `submit(name, *, args, kwargs, queue, task_id, countdown, eta, expires, priority, time_limit, soft_time_limit, headers, **options) -> str` | 发布任务，返回 task id；`**options` 透传给 transport |
| `asubmit(...)` | 异步版本 |
| `get_status(task_id) -> TaskStatus` | 读取一次结果快照 |
| `aget_status(task_id)` | 异步版本；Celery + Redis 时直读，不占线程 |
| `get_status_many(task_ids) -> dict[str, TaskStatus]` | 批量读取（Celery + Redis 一次 `MGET`） |
| `aget_status_many(task_ids)` | 异步批量读取 |
| `wait_for_result(task_id, *, timeout=None, interval=0.5, raise_on_error=False) -> TaskStatus` | 同步轮询到终态 |
| `await_result(...)` | 异步等待；transport 支持推送时完成即唤醒，`interval` 仅作兜底与超时粒度 |
| `warm_up(*, connections=1)` / `awarm_up(...)` | 预热 N 条结果后端连接：直接从连接池同时占用，数量精确；超过池上限抛 `ValueError`（lifespan 用 `awarm_up`） |
| `revoke(task_id, *, terminate=False, signal=None)` / `arevoke(...)` | 尽力取消（机制需支持） |
| `close()` / `aclose()` | 释放 transport 资源 |
| `transport` | 当前 transport（逃生舱） |

`CeleryBridge` 额外提供 `app`（裸 Celery app）与 `config`（`CeleryConfig`）。

### `TaskStatus`

`task_id`、`state`（Celery 原始状态字符串）、`result`、`exception`、`traceback`、`meta`；
派生属性 `pending`、`ready`、`terminal`、`successful`、`failed`、`revoked`、`error`。

状态常量见 `TaskState`（`SUCCESS` / `FAILURE` / `PENDING` / `STARTED` / …）。worker 自定义状态
（如 `PROGRESS`）原样保留在 `state`，此时 `result` 即进度信息。

### 异常

| 异常 | 场景 |
|------|------|
| `BridgeConfigurationError` | 配置缺失/非法 |
| `InvalidTaskPayloadError` | payload 不是合法 JSON 或 `args` 传了字符串 |
| `TaskPublishError` | broker 拒收/不可达，或没有返回 task id |
| `TaskResultUnavailableError` | 未配置 result backend，或读取结果失败 |
| `TaskTimeoutError` | 等待超时；`status` 为最后一次观察到的状态 |
| `TaskFailedError` | `raise_on_error=True` 且任务 `FAILURE`；`status` 含错误与 traceback |
| `BridgeError` | 上述异常的基类 |

## 语义与边界

1. **`PENDING` 是多义的**：任务未开始、worker 尚未写回结果、结果已过期，都表现为 `PENDING`。
   API 侧 `result_expires` 默认 1 小时，超过后无法再轮询到结果。
2. **轮询需要 result backend**。只投递不轮询时可以不配置；未配置而调用
   `get_status` 会抛 `TaskResultUnavailableError`，而不是静默返回 `PENDING`。
3. **队列由生产端决定**。Celery 的 `task_routes` 在发布时生效，worker 的 `-Q` 只是消费哪些队列。
   省略 `queue` 会落到 app 默认队列（未配置时是 `celery`），本项目 worker 不消费该队列。
   请显式传 `queue`，或配置 `default_queue` / `task_routes`。
4. **仅支持 JSON payload**。`uuid.UUID`、`datetime`、`bytes`、ORM 对象等需先转换为
   `str` / ISO 字符串 / 引用；大对象放对象存储，只传引用。关闭前置校验后 Celery 仍会在发布时失败。
5. **线程占用取决于路径**：result backend 是 Redis 且安装了 `redis` 时，`aget_status` /
   `await_result` 走 `redis.asyncio`，**不占工作线程**；其他后端（或 `prefer_async_redis_results=False`）
   回退到线程池。`asubmit`（broker 发布）仍是同步 Celery producer，通过线程执行；用
   `executor_workers` 给它一个专用线程池，避免与宿主的默认线程池互相拖累。
6. **取消与超时**：Redis 异步路径的取消会真正取消 socket 读取。线程路径的取消**不会**打断线程：
   `asyncio.wait_for(bridge.aget_status(...), ...)` 超时返回后，底层调用仍在那个线程里跑到完成或超时。
   因此 SDK 对 Redis broker/backend 默认设置 5s 的 `socket_timeout` /
   `socket_connect_timeout`（`CeleryConfig(redis_socket_timeout=..., redis_socket_connect_timeout=...)`,
   设为 `None` 退回 Celery 默认：读 120s、连接无超时），避免线程被长期占住。
7. **首次并发有连接预热成本**：Celery 的 `app.backend` 是惰性且无锁的，冷启动并发会为每个线程
   各建一个 backend/连接池（实测 24 线程建了 24 个）。SDK 已把 backend 解析收敛为一次（加锁缓存），
   并用异步结果客户端跳过线程路径；剩余成本是建连。实测首轮 200 次读取 53ms、预热后 22ms，
   100 次发布首轮约 1s、预热后约 60ms。可在 lifespan 里调用 `warm_up(connections=N)` 预热。
8. **pub/sub 唤醒是尽力而为**：`await_result` 订阅 Redis 结果通道，worker 写结果时立即唤醒；
   一个连接服务所有等待者，通道只在有等待者时订阅。Pub/Sub 使用**独立连接**，
   不占用 `async_redis_max_connections` 的状态读取池（该值设为 1 也不会饿死状态读取）。
   订阅失败、断线或服务端禁用 SUBSCRIBE 时自动退回轮询，等待不会失败或挂死。
   启用后可以把 `interval` 放大到 1–10s（长任务轮询量下降），完成检测仍接近即时；
   pub/sub 不可用时才会退化为该 `interval` 的检测延迟。同步 `wait_for_result` 不做唤醒，仍是轮询。
9. **只做桥接，不做状态机**：任务是否被消费、是否重试、结果是否可信，仍由 Celery 与 worker 决定；
   需要持久任务账本/取消语义时应使用独立的 task runtime。
10. **不会污染 `current_app`**：裸 app 以 `set_as_current=False` 创建。
11. **RabbitMQ 部署**：默认 `task_create_missing_queues=True`，发布时会声明队列。若 worker 的队列
    以特殊参数声明（`x-queue-type`、TTL 等），请通过 `CeleryConfig.app_options` 传
    `task_queues` / `task_create_missing_queues`，避免声明参数不一致。

## 实测与优化

`benches/async_blocking_probe.py` 用真实本地 Redis（result backend）与 RabbitMQ（broker）测量
事件循环心跳、并发延迟和线程池排队。Windows 下事件循环定时器精度约 15.6ms，因此心跳基线本身就是
~16ms；关键看是否显著高于基线。

| 场景 | 结果 | 心跳最大值 |
|------|------|-----------|
| 空闲基线（1s） | — | 16ms |
| 200 并发 `aget_status`（Redis 异步直读） | 首轮 53ms，预热后 22ms | 15ms / 8ms |
| 200 条状态一次 `aget_status_many`（MGET） | 6.8ms | 8ms |
| 100 并发 `await_result(timeout=1.5s)` | 总耗时 1.51s（若每次等待都占线程应为 6.25s） | p95 15ms，最大 ~30ms |
| 100 并发 `asubmit`（真实 RabbitMQ） | 首轮 818ms（建连），预热后 60ms | 17ms / 16ms |
| 24 个 0.5s 慢读取占满线程池 + 一个无关的 50ms `to_thread` | 该 50ms 调用变成 510ms | 16ms |
| 取消 24 个 1s 读取后再发 50ms `to_thread` | 取消立即返回（0.2ms），50ms 调用仍等 990ms | — |
| 对照组：在事件循环里直接调同步 `get_status`（300ms） | — | 301ms |

优化手段与对应收益：

| 优化 | 做法 | 收益 |
|------|------|------|
| 异步结果直读 | Redis backend 用 `redis.asyncio` 发 GET/MGET，key 与解码复用 Celery backend | 200 并发读 40ms → 22ms，且不占线程 |
| 批量状态 | `aget_status_many` 一次 `MGET` | 200 条 7ms（逐条约 22ms） |
| backend 单次解析 | 给 Celery 的无锁惰性 `app.backend` 加锁缓存 | 冷启动 200 读 ~700ms → 53ms |
| Redis socket 超时 | broker/backend 默认 5s | 挂起时线程不再被 120s/无限期占用 |
| 专用线程池 | `executor_workers` | 与宿主 `to_thread` 隔离，互不排队 |
| producer 池大小 | `broker_pool_limit`（默认 10） | 冷启动首轮 100 发布 p50 680ms → 390ms（仅首轮，波动较大） |
| 轮询抖动 | interval 上叠加 0–10% 随机 | 100 个同时等待的循环尖峰 107ms → ~30ms |
| 完成即唤醒 | 订阅 Celery 结果 pub/sub 通道（单连接、多等待者） | interval=5s 时完成检测 5.02s → 0.56s；写入后 **1.1ms** 返回 |
| 启动预热 | `warm_up(connections=N)` | 首轮发布/读取建连成本提前摊掉 |

仍然存在的边界：

- **发布仍是同步 Celery producer**：`asubmit` 必须经过线程。要彻底异步化就得自己实现 AMQP/Redis
  协议的生产者，收益有限、风险很大；用专用线程池隔离即可。
- **非 Redis 结果后端**（RPC、Database、自定义 backend）回退到线程路径，仍受线程池大小约束。
- **取消只对异步路径生效**；线程路径依赖 socket 超时兜底。

结论：

- **等待期间事件循环不被阻塞，Redis 结果读取也不持有线程**：1.5s 的等待总耗时就是 1.5s，而不是
  `ceil(100/24) × 1.5s`。
- **发布与回退路径仍占线程**，线程池是共享且有界的；`executor_workers` 可隔离。
- **取消不会释放线程**（线程路径），所以必须给底层网络调用设超时（SDK 默认 5s）。
- **首次并发有建连预热**；`warm_up` 或预热后延迟稳定。

复现：

```bash
cd packages/task-bridge-sdk
PYTHONPATH=src python benches/async_blocking_probe.py
```

## 测试

测试不依赖 Redis/RabbitMQ：一部分使用 fake app，一部分使用 Celery 的 `memory://`
broker 与 `cache+memory://` backend 验证真实发布路径。

```bash
cd packages/task-bridge-sdk
uv sync
uv run pytest
```
