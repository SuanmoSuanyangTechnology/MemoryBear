<img width="2346" height="1310" alt="image" src="https://github.com/user-attachments/assets/bc73a64d-cd1e-4d22-be3e-04ce40423a20" />

# MemoryBear empowers AI with human-like memory capabilities

[中文](./README_CN.md) | English

### [Installation Guide](#memorybear-installation-guide)
### Paper: <a href="https://memorybear.ai/pdf/memoryBear" target="_blank" rel="noopener noreferrer">《Memory Bear AI: A Breakthrough from Memory to Cognition》</a>
## Project Overview
MemoryBear is a next-generation AI memory system independently developed by RedBear AI. Its core breakthrough lies in moving beyond the limitations of traditional "static knowledge storage". Inspired by the cognitive mechanisms of biological brains, MemoryBear builds an intelligent knowledge-processing framework that spans the full lifecycle of perception, refinement, association, and forgetting.The system is designed to free machines from the trap of mere "information accumulation", enabling deep knowledge understanding, autonomous evolution, and ultimately becoming a key partner in human-AI cognitive collaboration.

## MemoryBear was created to address these challenges
### 1. Core causes of knowledge forgetting in single models</br>
Context window limitations: Mainstream large language models typically have context windows of 8k-32k tokens. In long conversations, earlier messages are pushed out of the window, causing later responses to lose their historical context.For example, a user says in turn 1, "I'm allergic to seafood", but by turn 5 when they ask, "What should I have for dinner tonight?" the model may have already forgotten the allergy information.</br>

Gap between static knowledge bases and dynamic data: The model's training corpus is a static snapshot (e.g., data up to 2023) and cannot continuously absorb personalized information from user interactions, such as preferences or order history. External memory modules are required to supplement and maintain this dynamic, user-specific knowledge.</br>

Limitations of the attention mechanism: In Transformer architectures, self-attention becomes less effective at capturing long-range dependencies as the sequence grows. This leads to a recency bias, where the model overweights the latest input and ignores crucial information that appeared earlier in the conversation.</br>

### 2. Memory gaps in multi-agent collaboration</br>
Data silos between agents: Different agents-such as a consulting agent, after-sales agent, and recommendation agent-often maintain their own isolated memories without a shared layer. As a result, users have to repeat information. For instance, after providing their address to the consulting agent, the user may be asked for it again by the after-sales agent.</br>

Inconsistent dialogue state: When switching between agents in multi-turn interactions, key dialogue state-such as the user's current intent or past issue labels-may not be passed along completely. This causes service discontinuities. For example,a user transitions from "product inquiry" to "complaint", but the new agent does not inherit the complaint details discussed earlier.</br>

Conflicting decisions: Agents that only see partial memory can generate contradictory responses. For example, a recommendation agent might suggest products that the user is allergic to, simply because it does not have access to the user's recorded health constraints.</br>

### 3. Semantic ambiguity during model reasoning distorted understanding of personalized context</br>
Personalized signals in user conversations-such as domain-specific jargon, colloquial expressions, or context-dependent references-are often not encoded accurately, leading to semantic drift in how the model interprets memory. For instance, when the user refers to "that plan we discussed last time", the model may be unable to reliably locate the specific plan in previous conversations. Broken cross-lingual and dialect memory links in multilingual or dialect-rich scenarios, cross-language associations in memory may fail. When a user mixes Chinese and English in their requests, the model may struggle to integrate information expressed across languages.</br>

Typical example: A user says: "Last time customer support told me it could be processed 'as an urgent case'. What's the status now?" If the system never encoded what "urgent" corresponds to in terms of a concrete service level, the model can only respond with vague, unhelpful answers.</br>

## Core Positioning of MemoryBear
Unlike traditional memory management tools that treat knowledge as static data to be retrieved, MemoryBear is designed around the goal of simulating the knowledge-processing logic of the human brain. It builds a closed-loop system that spans the entire lifecycle-from knowledge intake to intelligent output. By emulating the hippocampus's memory encoding, the neocortex's knowledge consolidation, and synaptic pruning-based forgetting mechanisms, MemoryBear enables knowledge to dynamically evolve with "life-like" properties. This fundamentally redefines the relationship between knowledge and its users-shifting from passive lookup to proactive cognitive assistance.</br>

## Core Philosophy of MemoryBear
MemoryBear's design philosophy is rooted in deep insight into the essence of human cognition: the value of knowledge does not lie in its accumulation, but in the continuous transformation and refinement that occurs as it flows.

In traditional systems, once stored, knowledge becomes static-hard to associate across domains and incapable of adapting to users' cognitive needs. MemoryBear, by contrast, is built on the belief that true intelligence emerges only when knowledge undergoes a full evolutionary process: raw information distilled into structured rules, isolated rules connected into a semantic network, redundant information intelligently forgotten. Through this progression, knowledge shifts from mere informational memory to genuine cognitive understanding, enabling the emergence of real intelligence.</br>

## Core Features of MemoryBear
As an intelligent memory management system inspired by biological cognitive processes, MemoryBear centers its capabilities on two dimensions: full-lifecycle knowledge memory management and intelligent cognitive evolution. It covers the complete chain-from memory ingestion and refinement to storage, retrieval, and dynamic optimization-while providing a standardized service architecture that ensures efficient integration and invocation across applications.</br>

### 1. Memory Extraction Engine: Multi-dimensional Structured Refinement as the Foundation of Cognition</br>
Memory extraction is the starting point of MemoryBear's cognitive-oriented knowledge management. Unlike traditional data extraction, which performs "mechanical transformation", MemoryBear focuses on semantic-level parsing of unstructured information and standardized multi-format outputs, ensuring precise compatibility with downstream graph construction and intelligent retrieval. Core capabilities include:</br>

Accurate parsing of diverse information types: The engine automatically identifies and extracts core information from declarative sentences, removing redundant modifiers while preserving the essential subject-action-object logic. It also extracts structured triples (e.g., "MemoryBear-core functionality-knowledge extraction"), providing atomic data units for graph storage and ensuring high-accuracy knowledge association.</br>

Temporal information anchoring: For time-sensitive knowledge-such as event logs, policy documents, or experimental data-the engine automatically extracts timestamps and associates them with the content. This enables time-based reasoning and resolves the "temporal confusion" found in traditional knowledge systems.</br>

Intelligent pruning summarization: Based on contextual semantic understanding, the engine generates summaries that cover all key information with strong logical coherence. Users may customize summary length (50-500 words) and emphasis (technical, business, etc.), enabling fast knowledge acquisition across scenarios.Example: For a 10-page technical document, MemoryBear can produce a concise summary including core parameters, implementation logic, and application scenarios in under 3 seconds.</br>

### 2. Graph Storage: Neo4j-Powered Visual Knowledge Networks</br>
The storage layer adopts a graph-first architecture, integrating with the mature Neo4j graph database to manage knowledge entities and relationships efficiently. This overcomes limitations of traditional relational databases-such as weak relational modeling and slow complex queries-and mirrors the biological "neuron-synapse" cognition model.</br>

Key advantages include:
Scalable, flexible storage: supportting millions of entities and tens of millions of relational edges, covering 12 core relationship types (hierarchical, causal, temporal, logical, etc.) to fit multi-domain knowledge applications. Seamless integration with the extraction module: Extracting triples synchronize directly into Neo4j, automatically constructing the initial knowledge graph with zero manual mapping. Interactive graph visualization: users can intuitively explore entity connection paths, adjust relationship weights, and perform hybrid "machine-generated + human-optimized" graph management.</br>

### 3. Hybrid Search: Keyword + Semantic Vector for Precision and Intelligence</br>
To overcome the classic tradeoff-precision but rigidity vs. fuzziness but inaccuracy-MemoryBear implements a hybrid retrieval framework combining keyword search and semantic vector search.</br>

Keyword search: Optimized with Lucene, enabling millisecond-level exact matching of structured Semantic vector search:Powered by BERT embeddings, transforming queries into high-dimensional vectors for deep semantic comparison. This allows recognition of synonyms, near-synonyms, and implicit intent.For example, the query "How to optimize memory decay efficiency?" may surface related knowledge such as "forgetting-mechanism parameter tuning" or "memory strength evaluation methods".
Intelligent fusion strategy:Semantic retrieval expands the candidate space; keyword retrieval then performs precise filtering.This dual-stage process increases retrieval accuracy to 92%, improving by 35% compared with single-mode retrieval.</br>

### 4. Memory Forgetting Engine: Dynamic Decay Based on Strength & Timeliness</br>
Forgetting is one of MemoryBear's defining features-setting it apart from static knowledge systems. Inspired by the brain's synaptic pruning mechanism, MemoryBear models forgetting using a dual-dimension approach based on memory strength and time decay, ensuring redundant knowledge is removed while key knowledge retains cognitive priority.</br>

Implementation details:Each knowledge item is assigned an initial memory strength (determined by extraction quality and manual importance labels). Strength is updated dynamically according to usage frequency and association activity; A configurable time-decay cycle defines how different knowledge types (core rules vs. temporary data) lose strength over time. When knowledge falls below the strength threshold and exceeds its validity period, it enters a three-stage lifecycle: Dormancy-retained but with lower retrieval priority. Decay-gradually compressed to reduce storage cost. Clearance -permanently removed and archived into cold storage. This mechanism maintains redundant knowledge under 8%, reducing waste by over 60% compared with systems lacking forgetting capabilities.</br>

### 5. Self-Reflection Engine: Periodic Optimization for Autonomous Memory Evolution</br>
The self-reflection mechanism is key to MemoryBear's "intelligent self-improvement'. It periodically revisits, validates, and optimizes existing knowledge, mimicking the human behavior of review and retrospection.</br>

A scheduled reflection process runs automatically at midnight each day, performing:
1. Consistency checks, Detects logical conflicts across related knowledge (e.g., contradictory attributes for the same entity), flags suspicious records, and routes them for human verification;
2. Value assessment, Evaluates invocation frequency and contribution to associations. High-value knowledge is reinforced; low-value knowledge experiences accelerated decay;
3. Association optimization, Adjusts relationship weights based on recent usage and retrieval behavior, strengthening high-frequency association paths.</br>

### 6. FastAPI Services: Standardized API Layer for Efficient Integration & Management</br>
To support seamless integration with external business systems, MemoryBear uses FastAPI to build a unified service architecture that exposes both management and service APIs with high performance, easy integration, and strong consistency. Service-side APIs cover knowledge extraction, graph operations, search queries, forgetting management, and more. Support JSON/XML formats, with average latency below 50 ms, and a single instance sustaining 1000 QPS concurrency. Management-side APIs provide configuration, permissions, log queries, batch knowledge import/export, reflection cycle adjustments, and other operational capabilities. Swagger API documentation is auto-generated, including parameter descriptions, request samples, and response schemas, enabling rapid integration and testing. The architecture is compatible with enterprise microservice ecosystems, supports Docker-based deployment, and integrates easily with CRM, OA, R&D management, and various business applications.</br>

## MemoryBear Architecture Overview
<img width="2294" height="1154" alt="image" src="https://github.com/user-attachments/assets/3afd3b49-20ea-4847-b9ed-38b646a4ad89" />
</br>
- Memory Extraction Engine: Preprocessing, deduplication, and structured knowledge extraction</br>
- Memory Forgetting Engine: Memory strength modeling and decay strategies</br>
- Memory Reflection Engine: Evaluation and rewriting of stored memories</br>
- Retrieval Services: Keyword search, semantic search, and hybrid retrieval</br>
- Agent & MCP Integration: Multi-tool collaborative agent capabilities</br>

## Metrics
We evaluate MemoryBear across multiple datasets covering different types of tasks, comparing its performance with other memory-enabled systems. The evaluation metrics include F1 score (F1), BLEU-1 (B1), and LLM-as-a-Judge score (J)-where higher values indicate better performance. MemoryBear achieves state-of-the-art results across all task categories: 
In single-hop scenarios, MemoryBear leads in precision, answer matching quality, and task specificity.
In multi-hop reasoning, it demonstrates stronger information coherence and higher reasoning accuracy.
In open generalization tasks, it exhibits superior capability in handling diverse, unbounded information and maintaining high-quality generalization.
In temporal reasoning tasks, it excels at aligning and processing time-sensitive information.
Across the core metrics of all four task types, MemoryBear consistently outperforms other competing systems in the industry, including Mem O, Zep, and LangMem, demonstrating significantly stronger overall performance.

<img width="2256" height="890" alt="image" src="https://github.com/user-attachments/assets/5ff86c1f-53ac-4816-976d-95b48a4a10c0" />
MemoryBear's vector-based knowledge memory (non-graph version) achieves substantial improvements in retrieval efficiency while maintaining high accuracy. Its overall accuracy surpasses the best existing full-text retrieval methods (72.90 ± 0.19%). More importantly, it maintains low latency across critical metrics-including Search Latency and Total Latency at both p50 and p95-demonstrating the characteristics of higher performance with greater latency efficiency. This effectively resolves the common bottleneck in full-text retrieval systems, where high accuracy typically comes at the cost of significantly increased latency.

<img width="2248" height="498" alt="image" src="https://github.com/user-attachments/assets/2759ea19-0b71-4082-8366-e8023e3b28fe" />
MemoryBear further unlocks its potential in tasks requiring complex reasoning and relationship awareness through the integration of a knowledge-graph architecture. Although graph traversal and reasoning introduce a slight retrieval overhead, this version effectively keeps latency within an efficient range by optimizing graph-query strategies and decision flows. More importantly, the graph-based MemoryBear pushes overall accuracy to a new benchmark (75.00 ± 0.20%). While maintaining high accuracy, it delivers performance metrics that significantly surpass all other methods, demonstrating the decisive advantage of structured memory systems.

<img width="2238" height="342" alt="image" src="https://github.com/user-attachments/assets/c928e094-45a2-414b-831a-6990b711ed07" />

# MemoryBear Installation Guide
## 1. Prerequisites

### 1.1 Environment Requirements

* Node.js 20.19+ or 22.12+- Required for running the frontend

* Python 3.12- Backend runtime environment

* PostgreSQL 13+- Primary relational database

* Neo4j 4.4+- Graph database (used for storing the knowledge graph)

* Redis 6.0+- Cache layer and message queue

## 2. Getting the Project

### 1. Download Method

Clone via Git (recommended):

```plain&#x20;text
git clone https://github.com/SuanmoSuanyangTechnology/MemoryBear.git
```

### 2. Directory Structure Explanation

<img width="5238" height="1626" alt="diagram" src="https://github.com/user-attachments/assets/416d6079-3f34-40c3-9bcf-8760d186741a" />


## Installation Steps

### 1. Start the Backend API Service

#### 1.1 Install Python Dependencies

```python
# 0. Install the dependency management tool: uv
pip install uv

# 1. Switch to the API directory
cd api

# 2. Install dependencies
uv sync 

# 3. Activate the Virtual Environment (Windows)
.venv\Scripts\Activate.ps1  # run inside /api directory
api\.venv\Scripts\activate  # run inside project root directory
.venv\Scripts\activate.bat  # run inside /api directory

```

#### 1.2 Install Required Base Services (Docker Images)

Use Docker Desktop to install the necessary service images.

* **Docker Desktop download page:** &#x68;ttps://www.docker.com/products/docker-desktop/

* **PostgreSQL**

  **Pull the Image**

  search-select-pull

  <img width="1280" height="731" alt="image-9" src="https://github.com/user-attachments/assets/0609eb5f-e259-4f24-8a7b-e354da6bae4d" />


**Create the Container**

<img width="1280" height="731" alt="image-8" src="https://github.com/user-attachments/assets/d57b3206-1df1-42a4-80fd-e71f37201a25" />


**Service Started Successfully**

<img width="1280" height="731" alt="image" src="https://github.com/user-attachments/assets/76e04c54-7a36-46ec-a68e-241ad268e427" />


* **Neo4j**

**Pull the Image** from Docker Desktop, the same way as with PostgreSQL.

**Create the Neo4j Container** ensure that you map **the two required ports** 7474 - Neo4j Browser, 7687 - Bolt protocol. Additionally, you must set an initial password for the Neo4j database during container creation.

<img width="1280" height="731" alt="image-1" src="https://github.com/user-attachments/assets/6bfb0c27-74e8-45f7-b381-189325d516bd" />


**Service Started Successfully**

<img width="1280" height="731" alt="image-2" src="https://github.com/user-attachments/assets/0d28b4fa-e8ed-4c05-8983-7a47f0a892d1" />


* **Redis**

The same as above

#### 1.3 Configure environment variables

Copy env.example as.env and fill in the configuration

```bash
# Neo4j Graph Database
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password
#  Neo4j Browser Access URL (optional documentation)

# PostgreSQL Database
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your-password
DB_NAME=redbear-mem

# Database Migration Configuration
# Set to true to automatically upgrade database schema on startup
DB_AUTO_UPGRADE=true  # For the first startup, keep this as true to create the schema in an empty database.

# Redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=1 

# Celery (Using Redis as broker)
BROKER_URL=redis://127.0.0.1:6379/0
RESULT_BACKEND=redis://127.0.0.1:6379/0

# JWT Secret Key (Formation method: openssl rand -hex 32)
SECRET_KEY=your-secret-key-here
```

#### 1.4 Initialize the PostgreSQL Database

MemoryBear uses Alembic migration files included in the project to create the required table structures in a newly created, empty PostgreSQL database.

**(1) Configure the Database Connection**

Ensure that the sqlalchemy.url value in the project's alembic.ini file points to your empty PostgreSQL database. Example format:

```bash
sqlalchemy.url = postgresql://<username>:<password>@<host>:<port>/<database_name>
```

Also verify that target_metadata in migrations/env.py is correctly linked to the ORM model's metadata object.

**(2) Apply the Migration Files**

Run the following command inside the API directory. Alembic will automatically detect the empty database and apply all outstanding migrations to create the full schema:
```bash
alembic upgrade head
```

<img width="1076" height="341" alt="image-3" src="https://github.com/user-attachments/assets/9edda79d-4637-46e3-bee3-2eec39975d59" />


Use Navicat to inspect the database tables created by the Alembic migration process.

<img width="1280" height="680" alt="image-4" src="https://github.com/user-attachments/assets/aa5c1d98-bdc3-4d25-acb2-5c8cf6ecd3f5" />


#### Start the API Service

```python
uv run -m app.main
```

Access the API documentation at http://localhost:8000/docs

<img width="1280" height="675" alt="image-5" src="https://github.com/user-attachments/assets/68fa62b4-2c4f-4cf0-896c-41d59aa7d712" />


### 2. Start the Frontend Web Application

#### 2.1 Install Dependencies

```python
# Switch to the web directory
cd web

# Install dependencies
npm install
```

#### 2.2 Update the API Proxy Configuration

Edit web/vite.config.ts and update the proxy target to point to your backend API service:

```python
proxy: {
  '/api': {
    target: 'http://127.0.0.1:8000',  // Change to the backend address, windows users 127.0.0.1  macOS users 0.0.0.0
    changeOrigin: true,
  },
}

```

#### 2.3 Start the Frontend Service

```python
# Start the web service
npm run dev

```

After the service starts, the console will output the URL for accessing the frontend interface.

<img width="935" height="311" alt="image-6" src="https://github.com/user-attachments/assets/cba1074a-440c-4866-8a94-7b6d1c911a93" />


<img width="1280" height="652" alt="image-7" src="https://github.com/user-attachments/assets/a719dc0a-cbdd-4ba1-9b21-123d5eac32eb" />


## 4. User Guide

step1: Retrieve the Project.

step2: Start the Backend API Service.

step3: Start the Frontend Web Application.

step4: Enter curl.exe -X POST http://127.0.0.1:8000/api/setup in the terminal to access the interface, initialize the database, and obtain the super administrator account.

step5: Super Administrator Credentials
Account: admin@example.com
Password: admin_password

step6: Log In to the Frontend Interface.

## License
This project is licensed under the Apache License 2.0. For details, see the LICENSE file.

## Acknowledgements & Community
- Feedback & Issues: Please submit an Issue in the repository for bug reports or discussions.
- Contributions Welcome: When submitting a Pull Request, please create a feature branch and follow conventional commit message guidelines.
- Contact: If you are interested in contributing or collaborating, feel free to reach out at tianyou_hubm@redbearai.com