<img width="2346" height="1310" alt="MemoryBear Hero Banner" src="./assets/generated/hero-banner.png" />

<div align="center">

# MemoryBear — Empowering AI with Human-Like Memory

**Next-Generation AI Memory Management System · Perceive · Extract · Associate · Forget**

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-green?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-teal?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Neo4j](https://img.shields.io/badge/Neo4j-4.4+-blue?logo=neo4j&logoColor=white)](https://neo4j.com/)
[![Gitee Sync](https://img.shields.io/github/actions/workflow/status/SuanmoSuanyangTechnology/MemoryBear/sync-to-gitee.yml?label=Gitee%20Sync&logo=gitee&logoColor=white)](https://github.com/SuanmoSuanyangTechnology/MemoryBear/actions/workflows/sync-to-gitee.yml)

[中文](./README_CN.md) | English

[Quick Start](#quick-start) · [Installation](#installation) · [Core Features](#core-features) · [Architecture](#architecture) · [Benchmarks](#benchmarks) · [Papers](#papers)

</div>

---

## Overview

MemoryBear is a next-generation AI memory system developed by RedBear AI. Its core breakthrough lies in moving beyond the limitations of traditional "static knowledge storage". Inspired by the cognitive mechanisms of biological brains, MemoryBear builds an intelligent knowledge-processing framework that spans the full lifecycle of **perception → extraction → association → forgetting**.

Unlike traditional memory tools that treat knowledge as static data to be retrieved, MemoryBear emulates the hippocampus's memory encoding, the neocortex's knowledge consolidation, and synaptic pruning-based forgetting — enabling knowledge to dynamically evolve with life-like properties. This shifts the relationship between AI and users from **passive lookup** to **proactive cognitive assistance**.

## Papers

| Paper | Description |
|-------|-------------|
| 📄 [Memory Bear AI: A Breakthrough from Memory to Cognition](https://memorybear.ai/pdf/memoryBear) | MemoryBear core technical report |
| 📄 [Memory Bear AI Memory Science Engine for Multimodal Affective Intelligence](https://arxiv.org/abs/2603.22306) | Technical report on multimodal affective intelligence memory engine |
| 📄 [A-MBER: Affective Memory Benchmark for Emotion Recognition](https://arxiv.org/abs/2604.07017) | Affective memory benchmark dataset |

## Why MemoryBear

### Knowledge Forgetting in Single Models

- **Context window limits**: Mainstream LLMs have 8k–32k token windows. In long conversations, early messages are pushed out, causing responses to lose historical context
- **Static knowledge gap**: Training data is a static snapshot — it cannot absorb personalized information (preferences, history) from live interactions
- **Recency bias**: Transformer self-attention weakens on long-range dependencies, overweighting recent input and ignoring earlier critical information

### Memory Gaps in Multi-Agent Collaboration

- **Data silos**: Different agents (consulting, after-sales, recommendation) maintain isolated memories, forcing users to repeat information
- **Inconsistent dialogue state**: When switching agents, user intent and history labels are not fully passed along, causing service discontinuities
- **Decision conflicts**: Agents with partial memory can produce contradictory responses (e.g., recommending products a user is allergic to)

### Semantic Ambiguity in Reasoning

- Domain jargon, colloquial expressions, and context-dependent references are not accurately encoded, leading to semantic drift in memory interpretation
- Cross-language memory associations fail in multilingual or dialect-rich scenarios

<img width="2294" height="1154" alt="Why MemoryBear" src="./assets/generated/pain-points.png" />

---

## Core Features

<img width="2294" height="1154" alt="MemoryBear Core Features" src="./assets/generated/core-features.png" />

### Memory Extraction Engine

Performs **semantic-level parsing** of unstructured conversations and documents to extract:

- **Core declarative information**: Strips redundant modifiers, preserving subject-action-object logic
- **Structured triples**: Automatically extracts entity relationships (e.g., `MemoryBear → core function → knowledge extraction`) as atomic units for graph storage
- **Temporal anchoring**: Automatically extracts and tags timestamps, enabling time-based knowledge tracing
- **Intelligent summarization**: Customizable length (50–500 words) and focus; generates concise summaries of 10-page documents in under 3 seconds

### Graph Storage (Neo4j)

**Graph-first architecture** integrated with Neo4j, overcoming the weak relational modeling of traditional databases:

- Supports millions of entities and tens of millions of relational edges
- Covers 12 core relationship types: hierarchical, causal, temporal, logical, and more
- Extracted triples sync directly to Neo4j, automatically building the initial knowledge graph
- Interactive graph visualization with "machine-generated + human-optimized" collaborative management

### Hybrid Search

**Keyword retrieval + semantic vector retrieval** dual-engine fusion:

- Keyword search powered by Elasticsearch for millisecond-level exact matching of structured information
- Semantic vector search via BERT embeddings, recognizing synonyms, near-synonyms, and implicit intent
- Semantic retrieval expands the candidate space; keyword retrieval then performs precise filtering
- Retrieval accuracy reaches **92%**, improving **35%** over single-mode retrieval

### Memory Forgetting Engine

Inspired by the brain's **synaptic pruning** mechanism, using a dual-dimension model of memory strength and time decay:

- Each knowledge item is assigned an initial memory strength, updated dynamically by usage frequency and association activity
- When strength falls below threshold, knowledge enters a **dormancy → decay → clearance** three-stage lifecycle
- Redundant knowledge maintained below **8%**, reducing waste by over **60%** compared to systems without forgetting

### Self-Reflection Engine

Scheduled daily reflection process, mimicking human review and retrospection:

- **Consistency checks**: Detects logical conflicts across related knowledge, flags suspicious records for human review
- **Value assessment**: Evaluates invocation frequency and association contribution; reinforces high-value knowledge, accelerates decay of low-value knowledge
- **Association optimization**: Adjusts relationship weights based on recent usage, strengthening high-frequency association paths

### FastAPI Service Layer

Unified service architecture exposing two API surfaces:

| API Type | Path Prefix | Auth | Purpose |
|----------|-------------|------|---------|
| Management API | `/api` | JWT | System config, permissions, log queries |
| Service API | `/v1` | API Key | Knowledge extraction, graph ops, search, forgetting control |

- Average response latency below **50ms**, single instance sustaining **1000 QPS**
- Auto-generated Swagger documentation
- Docker-ready, compatible with enterprise microservice ecosystems (CRM, OA, R&D management)

---

## Architecture

<img src="./assets/generated/architecture.png" alt="MemoryBear System Architecture" width="100%"/>

**Celery Three-Queue Async Architecture:**

| Queue | Worker Type | Concurrency | Purpose |
|-------|-------------|-------------|---------|
| `memory_tasks` | threads | 100 | Memory read/write (asyncio-friendly) |
| `document_tasks` | prefork | 4 | Document parsing (CPU-bound) |
| `periodic_tasks` | prefork | 2 | Scheduled tasks, reflection engine |

---

## Benchmarks

Evaluation metrics include F1 score (F1), BLEU-1 (B1), and LLM-as-a-Judge score (J) — higher values indicate better performance.

MemoryBear consistently outperforms competing systems including Mem0, Zep, and LangMem across all four task categories:

<img width="2256" height="890" alt="Benchmark Results" src="./assets/generated/benchmark-results.png" />

**Vector version (non-graph)**: Achieves substantially improved retrieval efficiency while maintaining high accuracy. Overall accuracy surpasses the best existing full-text retrieval methods (72.90 ± 0.19%), while maintaining low latency at both p50 and p95 for Search Latency and Total Latency.

<img width="2248" height="498" alt="Vector Version Metrics" src="./assets/generated/benchmark-vector.png" />

**Graph version**: Integrating the knowledge graph architecture pushes overall accuracy to a new benchmark (**75.00 ± 0.20%**), delivering performance metrics that significantly surpass all other methods.

<img width="2238" height="342" alt="Graph Version Metrics" src="./assets/generated/benchmark-graph.png" />

---

## Quick Start

### Docker Compose (Recommended)

**Prerequisites**: [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed.

```bash
# 1. Clone the repository
git clone https://github.com/SuanmoSuanyangTechnology/MemoryBear.git
cd MemoryBear/api

# 2. Start base services (PostgreSQL / Neo4j / Redis / Elasticsearch)
# Pull and start these images via Docker Desktop first (see Installation section 3.2)

# 3. Configure environment variables
cp env.example .env
# Edit .env with your database connections and LLM API keys

# 4. Initialize the database
pip install uv && uv sync
alembic upgrade head

# 5. Start API + Celery Workers + Beat scheduler
docker-compose up -d

# 6. Initialize the system and get the admin account
curl -X POST http://127.0.0.1:8002/api/setup
```

> **Note**: `docker-compose.yml` includes the API service and Celery Workers only. Base services (PostgreSQL, Neo4j, Redis, Elasticsearch) must be started separately.
>
> **Port info**: Docker Compose defaults to port `8002`; manual startup defaults to port `8000`. The installation guide below uses manual startup (`8000`) as the example.

After startup:
- API docs: http://localhost:8002/docs
- Frontend: http://localhost:3000 (after starting the web app)

**Default admin credentials:**
- Account: `admin@example.com`
- Password: `admin_password`

### Manual Start

> Quick commands below — see [Installation](#installation) for detailed steps.

```bash
# Backend
cd api
pip install uv && uv sync
alembic upgrade head
uv run -m app.main

# Frontend (new terminal)
cd web
npm install && npm run dev
```

---

## Installation

### 1. Environment Requirements

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.12+ | Backend runtime |
| Node.js | 20.19+ or 22.12+ | Frontend runtime |
| PostgreSQL | 13+ | Primary database |
| Neo4j | 4.4+ | Knowledge graph storage |
| Redis | 6.0+ | Cache and message queue |
| Elasticsearch | 8.x | Hybrid search engine |

### 2. Get the Project

```bash
git clone https://github.com/SuanmoSuanyangTechnology/MemoryBear.git
```

<img src="./assets/directory-structure.svg" alt="Directory Structure" width="100%"/>

### 3. Backend API Service

#### 3.1 Install Python Dependencies

```bash
# Install uv package manager
pip install uv

# Switch to the API directory
cd api

# Install dependencies
uv sync

# Activate virtual environment
# Windows (PowerShell, inside /api)
.venv\Scripts\Activate.ps1
# Windows (cmd, inside /api)
.venv\Scripts\activate.bat
# macOS / Linux
source .venv/bin/activate
```

#### 3.2 Install Base Services (Docker Images)

Download [Docker Desktop](https://www.docker.com/products/docker-desktop/) and pull the required images.

**PostgreSQL** — search → select → pull

<img width="1280" height="731" alt="PostgreSQL Pull" src="./assets/screenshots/pg-pull.png" />

<img width="1280" height="731" alt="PostgreSQL Container" src="./assets/screenshots/pg-container.png" />

<img width="1280" height="731" alt="PostgreSQL Running" src="./assets/screenshots/pg-running.png" />

**Neo4j** — pull the same way. When creating the container, map two required ports and set an initial password:
- `7474`: Neo4j Browser
- `7687`: Bolt protocol

<img width="1280" height="731" alt="Neo4j Container" src="./assets/screenshots/neo4j-container.png" />

<img width="1280" height="731" alt="Neo4j Running" src="./assets/screenshots/neo4j-running.png" />

**Redis** — same steps as above.

**Elasticsearch**

Pull the Elasticsearch 8.x image and create a container, mapping ports `9200` (HTTP API) and `9300` (cluster communication). For initial setup, disable security to simplify configuration:

```bash
docker run -d --name elasticsearch \
  -p 9200:9200 -p 9300:9300 \
  -e "discovery.type=single-node" \
  -e "xpack.security.enabled=false" \
  elasticsearch:8.15.0
```

#### 3.3 Configure Environment Variables

```bash
cp env.example .env
```

Fill in the core configuration in `.env`:

```bash
# Neo4j Graph Database
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password

# PostgreSQL Database
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your-password
DB_NAME=redbear-mem

# Set to true on first startup to auto-migrate the database
DB_AUTO_UPGRADE=true

# Redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=1

# Celery
REDIS_DB_CELERY_BROKER=1
REDIS_DB_CELERY_BACKEND=2

# Elasticsearch
ELASTICSEARCH_HOST=127.0.0.1
ELASTICSEARCH_PORT=9200

# JWT Secret Key (generate with: openssl rand -hex 32)
SECRET_KEY=your-secret-key-here
```

#### 3.4 Initialize the PostgreSQL Database

Verify the database connection in `alembic.ini`:

```ini
sqlalchemy.url = postgresql://<username>:<password>@<host>:<port>/<database_name>
```

Apply all migrations to create the full schema:

```bash
alembic upgrade head
```

<img width="1076" height="341" alt="Alembic Migration" src="./assets/screenshots/alembic-migration.png" />

<img width="1280" height="680" alt="Database Tables" src="./assets/screenshots/db-tables.png" />

#### 3.5 Start the API Service

```bash
uv run -m app.main
```

Access API documentation at http://localhost:8000/docs

<img width="1280" height="675" alt="API Docs" src="./assets/screenshots/api-docs.png" />

#### 3.6 Start Celery Workers (Optional, for async tasks)

```bash
# Memory worker (thread pool, asyncio-friendly, high concurrency)
celery -A app.celery_worker.celery_app worker --loglevel=info --pool=threads --concurrency=100 --queues=memory_tasks

# Document worker (prefork, CPU-bound parsing)
celery -A app.celery_worker.celery_app worker --loglevel=info --pool=prefork --concurrency=4 --queues=document_tasks

# Periodic worker (reflection engine, scheduled tasks)
celery -A app.celery_worker.celery_app worker --loglevel=info --pool=prefork --concurrency=2 --queues=periodic_tasks

# Beat scheduler
celery -A app.celery_worker.celery_app beat --loglevel=info
```

### 4. Frontend Web Application

#### 4.1 Install Dependencies

```bash
cd web
npm install
```

#### 4.2 Update API Proxy Configuration

Edit `web/vite.config.ts`:

```typescript
proxy: {
  '/api': {
    target: 'http://127.0.0.1:8000',  // Windows: 127.0.0.1 | macOS: 0.0.0.0
    changeOrigin: true,
  },
}
```

#### 4.3 Start the Frontend Service

```bash
npm run dev
```

<img width="935" height="311" alt="Frontend Start" src="./assets/screenshots/frontend-start.png" />

<img width="1280" height="652" alt="Frontend UI" src="./assets/screenshots/frontend-ui.png" />

### 5. Initialize the System

```bash
# Initialize the database and obtain the super admin account
curl -X POST http://127.0.0.1:8000/api/setup
```

**Super admin credentials:**
- Account: `admin@example.com`
- Password: `admin_password`

### 6. Full Startup Checklist

```
Step 1  Clone the repository
Step 2  Start base services (PostgreSQL / Neo4j / Redis / Elasticsearch)
Step 3  Configure .env environment variables
Step 4  Run alembic upgrade head to initialize the database
Step 5  uv run -m app.main to start the backend API
Step 6  npm run dev to start the frontend
Step 7  curl -X POST http://127.0.0.1:8000/api/setup to initialize the system
Step 8  Log in to the frontend with the admin account
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend Framework | FastAPI + Uvicorn |
| Async Tasks | Celery (3 queues: memory / document / periodic) |
| Primary Database | PostgreSQL 13+ |
| Graph Database | Neo4j 4.4+ |
| Search Engine | Elasticsearch 8.x (keyword + semantic vector hybrid) |
| Cache / Queue | Redis 6.0+ |
| ORM | SQLAlchemy 2.0 + Alembic |
| LLM Integration | LangChain / OpenAI / DashScope / AWS Bedrock |
| MCP Integration | fastmcp + langchain-mcp-adapters |
| Frontend Framework | React 18 + TypeScript + Vite |
| UI Components | Ant Design 5.x |
| Graph Visualization | AntV X6 + ECharts + D3.js |
| Package Manager | uv (backend) / npm (frontend) |

---

## License

This project is licensed under the [Apache License 2.0](LICENSE).

---

## Community & Support

- **Bug Reports & Feature Requests**: [GitHub Issues](https://github.com/SuanmoSuanyangTechnology/MemoryBear/issues)
- **Contribute**: Please read our [Contributing Guide](CONTRIBUTING.md). Submit [Pull Requests](https://github.com/SuanmoSuanyangTechnology/MemoryBear/pulls) on a feature branch following Conventional Commits format
- **Discussions**: [GitHub Discussions](https://github.com/SuanmoSuanyangTechnology/MemoryBear/discussions)
- **WeChat Community**: Scan the QR code below to join our WeChat group

![WeChat QR](https://github.com/user-attachments/assets/8c81885c-4134-40d5-96e2-7f78cc082dc6)

- **Star History**:

[![Star History Chart](https://api.star-history.com/svg?repos=SuanmoSuanyangTechnology/MemoryBear&type=Date)](https://star-history.com/#SuanmoSuanyangTechnology/MemoryBear&Date)

- **Contact**: tianyou_hubm@redbearai.com
