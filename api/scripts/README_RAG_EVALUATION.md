# MemoryBear 知识库评测脚本使用说明

本文说明以下两个独立脚本的安装、配置和完整使用流程：

- `rag_recall_eval.py`：语料快照、评测数据集生成、数据集校验和召回效果评测；
- `rag_retrieval_load_test.py`：基于 Locust 的检索接口性能压测。

两个脚本都只通过 HTTP 调用已经运行的 MemoryBear 服务，不导入 `app`、service、model 或数据库代码，也不读取项目 `.env`。可以将脚本复制到其他目录后单独运行。
两个脚本都会生成中文 `report.html`；JSON/JSONL 保留英文键名，供 CI、`jq` 和其他程序稳定读取。

## 1. 评测范围

本工具只评测知识库的召回能力：

- 是否召回正确文档；
- 是否召回正确 chunk/context；
- 正确结果在返回列表中的位置；
- 单文档、多文档和无命中场景；
- 检索接口的成功率、延迟和实际吞吐量。

本工具不生成或评测最终回答，不计算 Faithfulness、Answer Correctness、Answer Relevancy 等答案类指标。

首版只支持内部 JWT 路由：

```text
POST /api/chunks/retrieval
```

暂不支持 `/v1/chunks/retrieval`、GraphRAG、Folder、Share target 和 `ex_ids`。

## 2. 推荐运行方式

### 2.1 使用 uv 自动安装隔离依赖

两个脚本都包含 PEP 723 内联依赖声明。推荐始终使用 `uv run`：

```bash
uv run api/scripts/rag_recall_eval.py --help
uv run api/scripts/rag_retrieval_load_test.py --help
```

`uv` 会为每个脚本创建独立缓存环境，不会把 Ragas 或 Locust 安装进 MemoryBear 的生产虚拟环境。

召回脚本使用的关键依赖：

```text
ragas==0.4.3
langchain-community==0.3.31
```

性能脚本使用的关键依赖：

```text
locust==2.45.0
PyYAML==6.0.2
```

如果本机没有 `uv`，可以先安装：

```bash
pip install uv
```

### 2.2 为什么直接 python 会提示缺少 Locust

下面的命令不会读取 PEP 723 依赖，因此当前 Python 环境没有安装 Locust 时会报 `ModuleNotFoundError`：

```bash
python api/scripts/rag_retrieval_load_test.py --help
```

请改为：

```bash
uv run api/scripts/rag_retrieval_load_test.py --help
```

如果必须使用普通 `python`，请为压测脚本单独创建环境：

```bash
python3 -m venv .venv-rag-load
source .venv-rag-load/bin/activate
pip install locust==2.45.0 PyYAML==6.0.2
python api/scripts/rag_retrieval_load_test.py --help
```

不要为了运行压测而把 Locust 添加到 MemoryBear 的生产依赖。

## 3. 准备认证和工作目录

脚本使用当前用户的 JWT 调用内部接口。凭据只能通过环境变量传入：

```bash
export RAG_EVAL_BASE_URL="http://127.0.0.1:8000"
export RAG_EVAL_JWT_TOKEN="<JWT>"
```

推荐把语料快照、数据集和结果放在已忽略的临时目录：

```bash
mkdir -p api/tmp/rag_evaluation/datasets
mkdir -p api/tmp/rag_evaluation/runs
```

注意：

- 不要把 JWT 写进 YAML、JSONL、命令行参数或 Git；
- 快照包含完整 chunk 正文，属于敏感文件；
- 生成的数据集通常包含 query 和 reference，也应按知识库数据管理；
- 结果默认不保存完整召回正文，但仍可能包含文档和 chunk ID。

## 4. 召回效果评测完整流程

标准流程为：

```text
snapshot -> generate -> validate -> evaluate
```

### 4.1 第一步：生成知识库快照

指定一个知识库：

```bash
uv run api/scripts/rag_recall_eval.py snapshot \
  --base-url "$RAG_EVAL_BASE_URL" \
  --kb-id "<knowledge_id>" \
  --output api/tmp/rag_evaluation/datasets/corpus.snapshot.json
```

指定多个知识库时重复传入 `--kb-id`：

```bash
uv run api/scripts/rag_recall_eval.py snapshot \
  --base-url "$RAG_EVAL_BASE_URL" \
  --kb-id "<knowledge_id_1>" \
  --kb-id "<knowledge_id_2>" \
  --output api/tmp/rag_evaluation/datasets/corpus.snapshot.json
```

脚本通过以下只读接口获取数据：

```text
GET /api/knowledges/{knowledge_id}
GET /api/documents/{knowledge_id}/documents
GET /api/chunks/{knowledge_id}/{document_id}/chunks
```

默认只接受：

- active 知识库；
- 非 Folder 知识库；
- `status == 1` 的文档；
- `progress == 1` 且 `run == 0` 的已完成文档。

如仅用于排查，可以使用 `--include-unready` 跳过文档就绪检查，但不建议用该快照生成正式评测数据集：

```bash
uv run api/scripts/rag_recall_eval.py snapshot \
  --base-url "$RAG_EVAL_BASE_URL" \
  --kb-id "<knowledge_id>" \
  --include-unready \
  --output api/tmp/rag_evaluation/datasets/corpus.snapshot.json
```

快照包含：

- 知识库和文档信息；
- chunk 正文与 metadata；
- `physical_chunk_id`；
- 父子块或 QA/source 规范化后的 `canonical_evidence_id`；
- 每个 chunk 的内容 SHA256；
- 整体 `physical_corpus_sha256`。

首版快照通过分页接口读取，不是数据库事务或 Elasticsearch PIT 原子快照。生成快照期间应暂停目标知识库的上传、删除和重解析。

### 4.2 第二步：配置数据集生成模型

生成器调用 OpenAI-compatible `chat/completions` 接口，需要设置：

```bash
export RAG_EVAL_LLM_BASE_URL="https://example.com/v1"
export RAG_EVAL_LLM_API_KEY="<LLM_API_KEY>"
export RAG_EVAL_LLM_MODEL="<model_name>"
```

如果 `RAG_EVAL_LLM_BASE_URL` 不是以 `/chat/completions` 结尾，脚本会自动追加该路径。

提供方需要支持：

```json
{"response_format": {"type": "json_object"}}
```

生成时会把选中的 source chunk 正文发送给配置的模型。敏感知识库应使用本地或私有部署模型，不要直接发送给未经批准的外部服务。

### 4.3 第三步：生成评测数据集

示例：生成 100 条数据，其中普通样本的 30% 为多文档问题，全部样本的 10% 为 no-hit 问题：

```bash
uv run api/scripts/rag_recall_eval.py generate \
  --snapshot api/tmp/rag_evaluation/datasets/corpus.snapshot.json \
  --output api/tmp/rag_evaluation/datasets/retrieval.dataset.jsonl \
  --count 100 \
  --multi-ratio 0.30 \
  --no-hit-ratio 0.10 \
  --seed 20260715
```

主要参数：

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `--count` | `50` | 生成样本总数 |
| `--multi-ratio` | `0.3` | 非 no-hit 样本中的多文档比例 |
| `--no-hit-ratio` | `0.1` | 全部样本中的无命中比例 |
| `--seed` | `20260715` | source 选择和样本顺序的随机种子 |
| `--max-source-chars` | `6000` | 每个 source 最多发送给 LLM 的字符数 |
| `--temperature` | `0.3` | LLM temperature |
| `--timeout` | `90` | 单次 LLM 请求超时秒数 |

生成逻辑：

1. 从快照中排除 QA chunk 和空 chunk；
2. 父子模式优先使用 child 正文生成问题，但将 parent 作为规范化黄金 context；
3. QA 模式使用 source 正文，QA 返回结果通过 `source_chunk_id` 映射到 source evidence；
4. 单文档样本只使用物理单文档知识库或文件名唯一的文档；
5. 多文档样本从两个不同文档选择证据，要求 LLM 生成需要组合两份证据的问题；
6. query 和黄金 document/context ID 一起写入 JSONL，生成内容不会写回知识库。

每条生成数据包含 `quality_status=generated_unreviewed`。首版的黄金 ID 是生成时选中的已知正确证据，尚未自动穷举语料中所有可能的等价 chunk；因此 Precision/nDCG 更适合相对比较，不应直接当成绝对业务正确率。

no-hit 样本会标记 `low-confidence`。它表示生成器预期目标语料无法回答，不代表已经数学证明整个知识库不存在相关内容。

### 4.4 第四步：校验数据集

只校验 JSONL 结构：

```bash
uv run api/scripts/rag_recall_eval.py validate \
  --dataset api/tmp/rag_evaluation/datasets/retrieval.dataset.jsonl
```

同时校验黄金 ID 和语料快照：

```bash
uv run api/scripts/rag_recall_eval.py validate \
  --dataset api/tmp/rag_evaluation/datasets/retrieval.dataset.jsonl \
  --snapshot api/tmp/rag_evaluation/datasets/corpus.snapshot.json
```

校验内容包括：

- JSONL 每行都是 object；
- `case_id` 存在且唯一；
- query 非空；
- `target.kb_ids` 非空；
- target 不包含服务端会静默忽略的未知字段；
- 普通样本有黄金 context；
- no-hit 样本没有黄金 context；
- 数据集与快照的 `physical_corpus_sha256` 一致；
- 黄金 context ID 存在于快照。

### 4.5 第五步：执行召回评测

```bash
uv run api/scripts/rag_recall_eval.py evaluate \
  --base-url "$RAG_EVAL_BASE_URL" \
  --dataset api/tmp/rag_evaluation/datasets/retrieval.dataset.jsonl \
  --snapshot api/tmp/rag_evaluation/datasets/corpus.snapshot.json \
  --output-dir api/tmp/rag_evaluation/runs/quality-hybrid-top10 \
  --retrieve-type hybrid \
  --top-k 10 \
  --top-n 20 \
  --similarity-threshold 0.2 \
  --vector-similarity-weight 0.3
```

默认计算 `K=[1,3,5,10]`。如需自定义，重复传入 `--k`：

```bash
uv run api/scripts/rag_recall_eval.py evaluate \
  --base-url "$RAG_EVAL_BASE_URL" \
  --dataset api/tmp/rag_evaluation/datasets/retrieval.dataset.jsonl \
  --output-dir api/tmp/rag_evaluation/runs/quality-hybrid-custom-k \
  --top-k 20 \
  --top-n 30 \
  --k 1 \
  --k 5 \
  --k 10 \
  --k 20
```

必须满足：

```text
top_n >= top_k
max(K) <= top_k
```

`--retrieve-type` 支持：

```text
participle
semantic
hybrid
```

如需设置重排阈值：

```bash
--rerank-score-threshold 0.3
```

#### 召回指标说明

| 指标 | 含义 |
| --- | --- |
| `context_precision@K` | 前 K 个规范化 context 中黄金 context 的占比 |
| `context_recall@K` | 黄金 context 被前 K 个结果覆盖的比例 |
| `context_hit@K` | 前 K 个结果是否至少命中一个黄金 context |
| `context_mrr@K` | 第一个黄金 context 排名的倒数，越靠前越高 |
| `context_ndcg@K` | 多个黄金 context 按返回位置折损后的排序质量 |
| `document_recall@K` | 正确文档被前 K 个唯一文档覆盖的比例 |
| `document_hit@K` | 前 K 个唯一文档是否包含正确文档 |
| `group_recall@K` | 多证据组在前 K 个结果中的最大覆盖程度 |
| `complete_evidence_group@K` | 是否完整召回一个证据组 |
| `ragas_id_context_precision@K` | Ragas IDBasedContextPrecision 结果 |
| `ragas_id_context_recall@K` | Ragas IDBasedContextRecall 结果 |
| `no_hit_accuracy` | no-hit 样本正确返回空列表的比例 |

#### 召回评测输出

指定的 `--output-dir` 中会生成：

```text
case_results.jsonl
summary.json
report.html
```

`report.html` 是中文人可读报告，包含总体指标、单文档/多文档/no-hit 分组结果、逐 case 概要和指标解释。可以直接打开：

```bash
open api/tmp/rag_evaluation/runs/quality-hybrid-top10/report.html
```

`case_results.jsonl` 保存逐 case 的请求状态、耗时、返回 ID、指标和错误；`summary.json` 保存运行配置、数据集 hash、成功/失败数量和平均指标。这两个机器可读文件的字段名仍为英文，不影响中文报告。

如果 Ragas 导入或计算失败，逐 case 会出现 `ragas_error`，summary 的 `ragas_failure_count` 大于 0，进程以退出码 3 结束，避免只输出本地指标却被误认为 Ragas 已成功执行。

## 5. Locust 性能压测

性能脚本读取同一份评测 JSONL，但只使用：

- `query`；
- `target`；
- `corpus_mode`；
- `load_weight`；
- 单文档场景的黄金 document ID，用于检查范围越界。

它不在 Locust task 中计算 Ragas 指标，也不调用 LLM。

### 5.1 创建性能配置

创建一个本地 YAML，例如：

```text
api/tmp/rag_evaluation/load-smoke.yaml
```

内容：

```yaml
seed: 20260715
endpoint: /api/chunks/retrieval

retrieval:
  retrieve_type: hybrid
  top_k: 10
  top_n: 20
  similarity_threshold: 0.2
  vector_similarity_weight: 0.3
  rerank_score_threshold: 0.3

load:
  profile: smoke
  users: 2
  spawn_rate: 1
  run_time: 30s
  wait_seconds: 0
  request_timeout_seconds: 60

gates:
  min_requests: 20
  max_failure_ratio: 0.01
  max_p95_ms: 2500
  min_achieved_rps: 0.5
```

配置说明：

| 字段 | 说明 |
| --- | --- |
| `seed` | query 加权抽样随机种子 |
| `endpoint` | 首版必须为 `/api/chunks/retrieval` |
| `retrieval` | 每个请求共同使用的召回参数 |
| `load.profile` | `smoke`、`baseline`、`staircase` 或 `soak` |
| `users` | 非 staircase 模式的并发用户数 |
| `spawn_rate` | 每秒启动的用户数 |
| `run_time` | Locust 时长，如 `30s`、`5m`、`1h` |
| `wait_seconds` | 同一用户两次请求间等待时间，0 表示闭环持续请求 |
| `request_timeout_seconds` | 单次 HTTP 超时 |
| `min_requests` | 最少有效请求数量 |
| `max_failure_ratio` | 最大失败率，0.01 表示 1% |
| `max_p95_ms` | 最大 p95 延迟 |
| `min_achieved_rps` | 可选，最小实际 RPS |

示例阈值不是 MemoryBear 的正式 SLA，应根据测试环境和业务目标修改。

### 5.2 运行 smoke、baseline 或 soak

```bash
uv run api/scripts/rag_retrieval_load_test.py \
  --dataset api/tmp/rag_evaluation/datasets/retrieval.dataset.jsonl \
  --config api/tmp/rag_evaluation/load-smoke.yaml \
  --base-url "$RAG_EVAL_BASE_URL" \
  --output-dir api/tmp/rag_evaluation/runs/load-smoke-001
```

`--output-dir` 必须是尚不存在的目录，避免覆盖以前的压测结果。

`baseline` 和 `soak` 使用相同结构，只需要修改 profile、并发和时长：

```yaml
load:
  profile: baseline
  users: 1
  spawn_rate: 1
  run_time: 2m
  wait_seconds: 0
  request_timeout_seconds: 60
```

```yaml
load:
  profile: soak
  users: 10
  spawn_rate: 2
  run_time: 60m
  wait_seconds: 0
  request_timeout_seconds: 60
```

### 5.3 运行 staircase

staircase 不使用顶层 `users`、`spawn_rate` 和 `run_time`，而是逐阶段配置：

```yaml
seed: 20260715
endpoint: /api/chunks/retrieval

retrieval:
  retrieve_type: hybrid
  top_k: 10
  top_n: 20
  similarity_threshold: 0.2
  vector_similarity_weight: 0.3

load:
  profile: staircase
  wait_seconds: 0
  request_timeout_seconds: 60
  stages:
    - users: 1
      spawn_rate: 1
      duration_seconds: 60
    - users: 5
      spawn_rate: 2
      duration_seconds: 120
    - users: 10
      spawn_rate: 2
      duration_seconds: 180

gates:
  min_requests: 200
  max_failure_ratio: 0.01
  max_p95_ms: 2500
```

运行方式不变：

```bash
uv run api/scripts/rag_retrieval_load_test.py \
  --dataset api/tmp/rag_evaluation/datasets/retrieval.dataset.jsonl \
  --config api/tmp/rag_evaluation/load-staircase.yaml \
  --base-url "$RAG_EVAL_BASE_URL" \
  --output-dir api/tmp/rag_evaluation/runs/load-staircase-001
```

Locust 是闭环用户模型：一个用户会在上一次请求结束后再发下一次请求。报告中的 `achieved_rps` 是实际达到的吞吐，不代表开放到达模型下的系统硬 QPS 上限。

### 5.4 Locust 成功和失败判定

一次请求只有同时满足以下条件才计为成功：

1. HTTP 状态码为 2xx；
2. body 是 JSON object；
3. `code == 0`；
4. `data` 是 list；
5. 单文档场景没有返回其他文档的 chunk。

常见低基数错误类型：

```text
http_401
http_429
http_500
invalid_json
invalid_envelope
business_code_nonzero
invalid_data_schema
single_scope_violation
```

普通有答案 case 返回空列表时，HTTP 请求仍可视为性能成功，因为“没有召回正确内容”属于质量问题，应由召回评测脚本判断。

### 5.5 性能输出

输出目录包含：

```text
manifest.json
summary.json
locust.json
locust_stats.csv
locust_stats_history.csv
locust_failures.csv
locust_exceptions.csv
report.html
locust-report.html
```

`report.html` 是脚本生成的中文主报告，包含请求数、失败率、RPS、延迟分位数、门禁结果、失败类型，以及 staircase 的逐档并发统计。

`locust-report.html` 是 Locust 生成的原始英文页，仅在需要查看 Locust 原生图表或诊断信息时使用。

正常压测结束后直接打开中文报告：

```bash
open api/tmp/rag_evaluation/runs/load-smoke-001/report.html
```

`summary.json` 包含：

- request、success、failure 数量；
- failure ratio；
- achieved RPS；
- p50、p90、p95、p99 和最大延迟；
- 门禁失败原因；
- 最终 passed 状态。

### 5.6 为已有压测结果生成中文报告

如果旧的运行目录中只有 Locust 英文 `report.html`，不用重新压测，执行：

```bash
uv run api/scripts/rag_retrieval_load_test.py \
  --render-report-dir api/tmp/rag_evaluation/runs/load-smoke-001
```

脚本会：

1. 读取目录中的 `manifest.json`、`summary.json` 和 Locust CSV；
2. 把原有 Locust 英文页保留为 `locust-report.html`；
3. 生成新的中文 `report.html`。

如果运行被中断、没有 `summary.json`，但 `locust_stats.csv` 仍可读，中文页会标记“运行未完整结束”，只展示诊断数据，不判定门禁通过。

## 6. 退出码

| 退出码 | 含义 |
| ---: | --- |
| `0` | 输入有效，运行完成且门禁通过 |
| `2` | 运行有效，但请求失败或性能门禁未通过 |
| `3` | 配置、认证、数据集、快照、依赖或 Ragas 指标问题导致运行无效 |

可在 shell 或 CI 中检查：

```bash
uv run api/scripts/rag_recall_eval.py validate \
  --dataset api/tmp/rag_evaluation/datasets/retrieval.dataset.jsonl

echo $?
```

## 7. 常见问题

### 7.1 `No module named 'locust'`

原因：使用了普通 `python`，当前解释器没有安装 Locust。

解决：

```bash
uv run api/scripts/rag_retrieval_load_test.py --help
```

### 7.2 `Missing authentication token`

设置 JWT：

```bash
export RAG_EVAL_JWT_TOKEN="<JWT>"
```

不要在 token 值前后额外添加 `Bearer`；脚本会自动构造 Authorization header。

### 7.3 `Document ... is not ready`

文档仍在解析、解析失败或任务状态没有结束。正式评测应等待：

```text
status == 1
progress == 1
run == 0
```

### 7.4 `Snapshot content does not match physical_corpus_sha256`

快照文件被修改或损坏。重新执行 `snapshot`，不要手动修改快照正文或 metadata。

### 7.5 `gold context ids missing from snapshot`

数据集与快照不是同一语料版本，或者文档已经重解析并重新生成 `doc_id`。重新生成快照和数据集。

### 7.6 `top_n must be greater than or equal to top_k`

调整参数，例如：

```bash
--top-k 10 --top-n 20
```

### 7.7 Ragas 导入失败或 `ragas_failure_count > 0`

必须通过 `uv run` 使用脚本锁定的依赖。如果手动安装，请使用 README 前述兼容版本；不要只安装最新版 `langchain-community`。

### 7.8 LLM 不支持 `response_format`

首版生成器要求 OpenAI-compatible JSON object 输出。如果提供方不支持该字段，需要更换兼容模型服务，或后续扩展脚本适配器。

### 7.9 401 不一定表示 JWT 本身失效

低并发先执行 smoke。如果高并发才出现 401，应同时检查服务端认证日志、数据库连接池和 session 生命周期，不要仅凭 Locust 的 401 直接判断 token 失效。

## 8. 推荐实践

1. 在测试或预发布环境执行正式压测，生产环境默认只运行低并发 smoke。
2. 生成快照期间不要上传、删除或重解析目标文档。
3. 数据集生成后先运行 `validate`，再运行 `evaluate`。
4. 对比检索参数时复用同一份 dataset 和 corpus hash，每组参数单独创建 run 目录。
5. 不要把不同 retrieve type 或不同 `top_k` 的性能结果混成一个总体 p95。
6. 自动生成数据适合快速建立基线；关键版本可对少量样本抽查，但不要求人工逐条制作全部数据。
7. 快照、数据集、LLM 输入和结果都可能含敏感知识，不要提交到 Git 或发送到未经批准的外部服务。
