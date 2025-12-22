<img width="2346" height="1310" alt="image" src="https://github.com/user-attachments/assets/bc73a64d-cd1e-4d22-be3e-04ce40423a20" />

# MemoryBear 让AI拥有如同人类一样的记忆

中文 | [English](./README.md)

### [安装教程](#memorybear安装教程)
### 论文：<a href="https://memorybear.ai/pdf/memoryBear" target="_blank" rel="noopener noreferrer">《Memory Bear AI: 从记忆到认知的突破》</a>
## 项目简介
MemoryBear是红熊AI自主研发的新一代AI记忆系统，其核心突破在于跳出传统知识“静态存储”的局限，以生物大脑认知机制为原型，构建了具备“感知-提炼-关联-遗忘”全生命周期的智能知识处理体系。该系统致力于让机器摆脱“信息堆砌”的困境，实现对知识的深度理解与自主进化，成为人类认知协作的核心伙伴。

## MemoryBear是从解决这些问题来的
### 一、单模型知识遗忘的核心原因</br>
上下文窗口限制：主流大模型上下文窗口通常为 8k-32k tokens，长对话中早期信息会被 “挤出”，导致后续回复脱离历史语境：如用户第 1 轮说 “我对海鲜过敏”，第 5 轮问 “推荐今晚的菜品” 时模型可能遗忘过敏信息。</br>
静态知识库与动态数据割裂：大模型训练时的静态知识库如截止 2023 年数据，无法实时吸收用户对话中的个性化信息如用户偏好、历史订单，需依赖外部记忆模块补充。</br>
模型注意力机制缺陷：Transformer 的自注意力对长距离依赖的捕捉能力随序列长度下降，出现 “近因效应”更关注最新输入，忽略早期关键信息。</br>

### 二、多 Agent 协作的记忆断层问题</br>
Agent 数据孤岛：不同 Agent如咨询 Agent、售后 Agent、推荐 Agent各自维护独立记忆，未建立跨模块的共享机制，导致用户重复提供信息如用户向咨询 Agent 说明地址后，售后 Agent 仍需再次询问。</br>
对话状态不一致：多轮交互中 Agent 切换时，对话状态如用户当前意图、历史问题标签传递不完整，引发服务断层如用户从 “产品咨询” 转 “投诉” 时，新 Agent 未继承前期投诉细节。</br>
决策冲突：不同 Agent 基于局部记忆做出的响应可能矛盾如推荐 Agent 推荐用户过敏的产品，因未获取健康禁忌的历史记录。</br>

### 三、模型推理过程中的 “语义歧义” 引发理解偏差</br>
用户对话中的个性化信息如行业术语、口语化表达、上下文指代未被准确编码，导致模型对记忆内容的语义解析失真，比如对用户历史对话中的模糊表述如 “上次说的那个方案”无法准确定位具体内容。</br>
多语言、方言场景中，跨语种记忆关联失效如用户混用中英描述需求时，模型无法整合多语言信息。</br>
典型案例：用户说之前客服说可以‘加急处理’现在进度如何？模型因未记录 “加急” 对应的具体服务等级，回复笼统模糊。</br>

## MemoryBear核心定位
与传统记忆管理工具将知识视为“待检索的静态数据”不同，MemoryBear以“模拟人类大脑知识处理逻辑”为核心目标，构建了从知识摄入到智能输出的闭环体系。系统通过复刻大脑海马体的记忆编码、新皮层的知识固化及突触修剪的遗忘机制，让知识具备动态演化的“生命特征”，彻底重构了知识与使用者之间的交互关系——从“被动查询”升级为“主动辅助记忆认知”

## MemoryBear核心哲学
MemoryBear的设计哲学源于对人类认知本质的深刻洞察：知识的价值不在于存量积累，而在于动态流转中的价值升华。传统系统中，知识一旦存储便陷入“静止状态”，难以形成跨领域关联，更无法主动适配使用者的认知需求；而MemoryBear坚信，只有让知识经历“原始信息提炼为结构化规则、孤立规则关联为知识网络、冗余信息智能遗忘”的完整过程，才能实现从“信息记忆”到“认知理解”的跨越，最终涌现出真正的智能。

## MemoryBear核心特性
MemoryBear作为模仿生物大脑认知过程的智能记忆管理系统，其核心特性围绕“记忆知识全生命周期管理”与“智能认知进化”两大维度构建，覆盖记忆从摄入提炼到存储检索、动态优化的完整链路，同时通过标准化服务架构实现高效集成与调用。

### 一、记忆萃取引擎：多维度结构化提炼，夯实认知基础</br>
记忆萃取是MemoryBear实现“认知化管理”的起点，区别于传统数据提取的“机械转换”，其核心优势在于对非结构化信息的“语义级解析”与“多格式标准化输出”，精准适配后续图谱构建与智能检索需求。具体能力包括：</br>
多类型信息精准解析：可自动识别并提取文本中的陈述句核心信息，剥离冗余修饰成分，保留“主体-行为-对象”核心逻辑；同时精准抽取三元组数据（如“MemoryBear-核心功能-知识萃取”），为图谱存储提供基础数据单元，保障知识关联的准确性。</br>
时序信息锚定：针对含有时效性的知识（如事件记录、政策文件、实验数据），自动提取并标记时间戳信息，支持“时间维度”的知识追溯与关联，解决传统知识管理中“时序混乱”导致的认知偏差问题。</br>
智能剪枝生成：基于上下文语义理解，生成“关键信息全覆盖+逻辑连贯性强”的摘要内容，支持自定义摘要长度（50-500字）与侧重点（如技术型、业务型），适配不同场景的知识快速获取需求。例如对10页技术文档处理时，可在3秒内生成含核心参数、实现逻辑与应用场景的精简摘要。</br>

### 二、图谱存储：对接Neo4j，构建可视化知识网络</br>
存储层采用“图数据库优先”的架构设计，通过对接业界成熟的Neo4j图数据库，实现知识实体与关系的高效管理，突破传统关系型数据库“关联弱、查询繁”的局限，契合生物大脑“神经元关联”的认知模式。</br>
该特性核心价值体现在：一是支持海量实体与多元关系的灵活存储，可管理百万级知识实体及千万级关联关系，涵盖“上下位、因果、时序、逻辑”等12种核心关系类型，适配多领域知识场景；二是与知识萃取模块深度联动，萃取的三元组数据可直接同步至Neo4j，自动构建初始知识图谱，无需人工二次映射；三是支持图谱可视化交互，用户可直观查看实体关联路径，手动调整关系权重，实现“机器构建+人工优化”的协同管理。</br>

### 三、混合搜索：关键词+语义向量，兼顾精准与智能</br>
为解决传统搜索“要么精准但僵化，要么模糊但失准”的痛点，MemoryBear采用“关键词检索+语义向量检索”的混合搜索架构，实现“精准匹配”与“意图理解”的双重目标。</br>
其中，关键词检索基于Lucene引擎优化，针对知识中的核心实体、关键参数等结构化信息实现毫秒级精准定位，保障“明确需求”下的高效检索；语义向量检索则通过BERT模型对查询语句进行语义编码，将其转化为高维向量后与知识库中的向量数据比对，可识别同义词、近义词及隐含意图，例如用户查询“如何优化记忆衰减效率”时，系统可关联到“遗忘机制参数调整”“记忆强度评估方法”等相关知识。两种检索方式智能融合：先通过语义检索扩大候选范围，再通过关键词检索精准筛选，使检索准确率提升至92%，较单一检索方式平均提升35%。</br>

### 四、记忆遗忘引擎：基于强度与时效的动态衰减，模拟生物记忆特性</br>
遗忘是MemoryBear区别于传统静态知识管理工具的核心特性之一，其灵感源于生物大脑“突触修剪”机制，通过“记忆强度+时效”双维度模型实现知识的逐步衰减，避免冗余知识占用资源，保障核心知识的“认知优先级”。</br>
具体实现逻辑为：系统为每条知识分配“初始记忆强度”（由萃取质量、人工标注重要性决定），并结合“调用频率、关联活跃度”实时更新强度值；同时设定“时效衰减周期”，根据知识类型（如核心规则、临时数据）差异化配置衰减速率。当知识强度低于阈值且超过设定时效后，将进入“休眠-衰减-清除”三阶段流程：休眠阶段保留数据但降低检索优先级，衰减阶段逐步压缩存储体积，清除阶段则彻底删除并备份至冷存储。该机制使系统冗余知识占比控制在8%以内，较传统无遗忘机制系统降低60%以上。</br>

### 五、自我反思引擎：定期回顾优化，实现记忆自主进化</br>
自我反思机制是MemoryBear实现“智能升级”的关键，通过定期对已有记忆进行回顾、校验与优化，模拟人类“复盘总结”的认知行为，持续提升知识体系的准确性与有效性。</br>
系统默认每日凌晨触发自动反思流程，核心动作包括：一是“一致性校验”，对比关联知识间的逻辑冲突（如同一实体的矛盾属性），标记可疑知识并推送人工审核；二是“价值评估”，统计知识的调用频次、关联贡献度，将高价值知识强化记忆强度，低价值知识加速衰减；三是“关联优化”，基于近期检索与使用行为，调整知识间的关联权重，强化高频关联路径。此外，支持人工触发专项反思（如新增核心知识后），并提供反思报告可视化展示优化结果，实现“自主进化+人工监督”的双重保障。</br>

### 六、FastAPI服务：标准化API输出，实现高效集成与管理</br>
为保障系统与外部业务场景的高效对接，MemoryBear采用FastAPI构建统一服务架构，实现管理端与服务端API的集中暴露，具备“高性能、易集成、强规范”的核心优势。服务端API涵盖知识萃取、图谱操作、搜索查询、遗忘控制等全功能模块，支持JSON/XML多格式数据交互，响应延迟平均低于50ms，单实例可支撑1000QPS并发请求；管理端API则提供系统配置、权限管理、日志查询等运维功能，支持通过API实现批量知识导入导出、反思周期调整等操作。同时，系统自动生成Swagger API文档，包含接口参数说明、请求示例与返回格式定义，开发者可快速完成集成调试。该架构已适配企业级微服务体系，支持Docker容器化部署，可灵活对接CRM、OA、研发管理等各类业务系统。</br>

## MemoryBear架构总览
<img width="2294" height="1154" alt="image" src="https://github.com/user-attachments/assets/3afd3b49-20ea-4847-b9ed-38b646a4ad89" />
</br>
- 记忆萃取引擎（Extraction Engine）：预处理、去重、结构化提取</br>
- 记忆遗忘引擎（Forgetting Engine）：记忆强度模型与衰减策略</br>
- 记忆自我反思引擎（Reflection Engine）：评价与重写记忆</br>
- 检索服务：关键词、语义与混合检索</br>
- Agent 与 MCP：提供多工具协作的智能体能力</br>

## 实验室指标
我们采用不同问题的数据集中，通过具备记忆功能的系统，进行性能对比。评估指标包括F1分数（F1）、BLEU-1（B1）以及LLM-as-a-Judge分数（J），数值越高表示表现越好，性能更高。
MemoryBear 在 “单跳场景” 的精准度、结果匹配度与任务特异性表现上，均处于领先，“多跳”更强的信息连贯性与推理准确性，“开放泛化”对多样，无边界信息的处理质量与泛化能力更优，“时序”对时效性信息的匹配与处理表现更出色，四大任务的核心指标中，均优于 行业内的其他海外竞争对手Mem O、Zep、Lang Mem 等现有方法，整体性能更突出。
<img width="2256" height="890" alt="image" src="https://github.com/user-attachments/assets/5ff86c1f-53ac-4816-976d-95b48a4a10c0" />
Memory Bear 基于向量的知识记忆非图谱版本，成功在保持高准确性的同时，极大地优化了检索效率。该方法在总体准确性上的表现已明显高于现有最高全文检索方法（72.90 ± 0.19%）。更重要的是，它在关键的延迟指标（包括 Search Latency 和 Total Latency 的 p50/p95）上也保持了较低水平，充分体现出 “性能更优且延迟更高效” 的特点，解决了全文检索方法的高准确性伴随的高延迟瓶颈。
<img width="2248" height="498" alt="image" src="https://github.com/user-attachments/assets/2759ea19-0b71-4082-8366-e8023e3b28fe" />
Memory Bear 通过集成知识图谱架构，在需要复杂推理和关系感知的任务上进一步释放了潜力。虽然图谱的遍历和推理可能会引入轻微的检索开销，但该版本通过优化图检索策略和决策流，成功将延迟控制在高效范围。更关键的是，基于图谱的 Memory Bear 将总体准确性推至新的高度（75.00 ± 0.20%），在保持准确性的同时，整体指标显著优于其他所有方法，证明了“结构化记忆带来的性能决定性优势”。
<img width="2238" height="342" alt="image" src="https://github.com/user-attachments/assets/c928e094-45a2-414b-831a-6990b711ed07" />

# MemoryBear安装教程
## 一、前期准备

### 1.环境要求

* Node.js 20.19+ 或 22.12+  前端运行环境

* Python 3.12  后端运行环境

* PostgreSQL 13+ 主数据库

* Neo4j 4.4+ 图数据库（存储知识图谱）

* Redis 6.0+ 缓存和消息队列

## 二、项目获取

### 1.获取方式

Git克隆（推荐）：

```plain&#x20;text
git clone https://github.com/SuanmoSuanyangTechnology/MemoryBear.git
```

### 2.目录说明

<img width="5238" height="1626" alt="diagram" src="https://github.com/user-attachments/assets/416d6079-3f34-40c3-9bcf-8760d186741a" />


## 三、安装步骤

### 1.后端API服务启动

#### 1.1 安装python依赖

```python
# 0.安装依赖管理工具uv
pip install uv

# 1.终端切换API目录
cd api

# 2.安装依赖
uv sync 

# 3.激活虚拟环境 (Windows)
.venv\Scripts\Activate.ps1  （powershell，在api目录下）
api\.venv\Scripts\activate （powershell，在根目录下）
.venv\Scripts\activate.bat （cmd，在api目录下）

```

#### 1.2 安装必备基础服务（docker镜像）

使用docker desktop安装所需的docker镜像

* **docker desktop安装地址：**&#x68;ttps://www.docker.com/products/docker-desktop/

* **PostgreSQL**

  **拉取镜像**

  search——select——pull

  <img width="1280" height="731" alt="image-9" src="https://github.com/user-attachments/assets/0609eb5f-e259-4f24-8a7b-e354da6bae4d" />


**创建容器**

<img width="1280" height="731" alt="image-8" src="https://github.com/user-attachments/assets/d57b3206-1df1-42a4-80fd-e71f37201a25" />


**服务启动成功**

<img width="1280" height="731" alt="image" src="https://github.com/user-attachments/assets/76e04c54-7a36-46ec-a68e-241ad268e427" />


* **Neo4j**

**拉取镜像**，与PostgreSQL一样从docker desktop中拉取镜像

**创建容器**，Neo4j 默认需要映射**2 个关键端口**（7474 对应 Browser，7687 对应 Bolt 协议），同时需设置初始密码

<img width="1280" height="731" alt="image-1" src="https://github.com/user-attachments/assets/6bfb0c27-74e8-45f7-b381-189325d516bd" />


**服务成功启动**

<img width="1280" height="731" alt="image-2" src="https://github.com/user-attachments/assets/0d28b4fa-e8ed-4c05-8983-7a47f0a892d1" />


* **Redis**

同上

#### 1.3 配置环境变量

复制 env.example 为 .env 并填写配置

```bash
# Neo4j 图数据库 
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password
# Neo4j Browser访问地址

# PostgreSQL 数据库
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your-password
DB_NAME=redbear-mem

# Database Migration Configuration
# Set to true to automatically upgrade database schema on startup
DB_AUTO_UPGRADE=true  # 首次启动设为true自动迁移数据库 在空白数据库创建表结构

# Redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=1 

# Celery (使用Redis作为broker)
BROKER_URL=redis://127.0.0.1:6379/0
RESULT_BACKEND=redis://127.0.0.1:6379/0

# JWT密钥 (生成方式: openssl rand -hex 32)
SECRET_KEY=your-secret-key-here
```

#### 1.4 PostgreSQL数据库建立

通过项目中已有的 alembic 数据库迁移文件，为全新创建的空白 PostgreSQL 数据库创建对应的表结构。

**（1）配置数据库连接**

确认项目中`alembic.ini`文件的`sqlalchemy.url`配置指向你的空白 PostgreSQL 数据库，格式示例：

```bash
sqlalchemy.url = postgresql://用户名:密码@数据库地址:端口/空白数据库名
```

同时检查 migrations`/env.py`中`target_metadata`是否正确关联到 ORM 模型的`metadata`（确保迁移脚本和模型一致）

**（2）执行迁移文件**

在API目录执行以下命令，alembic 会自动识别空白数据库，并执行所有未应用的迁移脚本，创建完整表结构：

```bash
alembic upgrade head
```

<img width="1076" height="341" alt="image-3" src="https://github.com/user-attachments/assets/9edda79d-4637-46e3-bee3-2eec39975d59" />


通过Navicat查看迁移创建的数据库表结构

<img width="1280" height="680" alt="image-4" src="https://github.com/user-attachments/assets/aa5c1d98-bdc3-4d25-acb2-5c8cf6ecd3f5" />


#### API服务启动

```python
uv run -m app.main
```

访问 API 文档：http://localhost:8000/docs

<img width="1280" height="675" alt="image-5" src="https://github.com/user-attachments/assets/68fa62b4-2c4f-4cf0-896c-41d59aa7d712" />


### 2.前端web应用启动

#### 2.1安装依赖

```python
# 切换web目录下
cd web

# 下载依赖
npm install
```

#### 2.2 修改API代理配置

编辑 web/vite.config.ts，将代理目标改为后端地址

```python
proxy: {
  '/api': {
    target: 'http://127.0.0.1:8000',  // 改为后端地址，win用户127.0.0.1  mac用户0.0.0.0
    changeOrigin: true,
  },
}

```

#### 2.3 启动服务

```python
# 启动web服务
npm run dev

```

服务启动会输出可访问的前端界面

<img width="935" height="311" alt="image-6" src="https://github.com/user-attachments/assets/cba1074a-440c-4866-8a94-7b6d1c911a93" />


<img width="1280" height="652" alt="image-7" src="https://github.com/user-attachments/assets/a719dc0a-cbdd-4ba1-9b21-123d5eac32eb" />


## 四、用户操作

step1：项目获取

step2：后端API服务启动

step3：前端web应用启动

step4： 终端输入 curl.exe -X POST http://127.0.0.1:8000/api/setup ，访问接口初始化数据库获得超级管理员账号

step5：超级管理员&#x20;

账号：admin@example.com

密码：admin\_password

step6：登陆前端页面




## 许可证

本项目采用 Apache License 2.0 开源协议，详情见 `LICENSE`。

## 致谢与交流

- 问题反馈与讨论：请提交 Issue 到代码仓库
- 欢迎贡献：提交 PR 前请先创建功能分支并遵循常规提交信息格式
- 如感兴趣需要联络：tianyou_hubm@redbearai.com
