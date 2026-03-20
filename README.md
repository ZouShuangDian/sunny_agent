# Sunny Agent (AI智能体框架)

Sunny Agent 是一个基于 Python 和 FastAPI 构建的企业级 AI 智能体框架。它具备意图识别、智能路由、工具调用、RAG（检索增强生成）以及完善的可观测性（Metrics & Audit）功能。

## 🚀 项目现状与核心功能

当前版本已包含以下核心模块：

- **智能意图路由 (Intent Engine)**:
  - 基于特征的决策指南，自动将请求路由至 `direct_response` (直接回答), `fast_track` (快速通道), 或 `deep_engine` (深度引擎)。
  - 支持多轮对话上下文理解与意图澄清。
- **工具生态系统 (Tool System)**:
  - `builtin_tools`: 内置 WebSearch (博查) 和 WebFetch 等基础工具。
  - `ToolRegistry`: 可扩展的工具注册与管理机制。
- **L1 Master Agent 架构**:
  - 职责分离：意图识别与参数归一化解耦。
  - 剥离了码表查询逻辑，保持 Master Layer 的纯粹性。
- **数据与存储**:
  - **PostgreSQL**: 存储用户、会话、审计日志 (Audit Log)、Prompt 模板。
  - **Milvus**: 向量数据库，用于知识库检索。
  - **Redis**: 缓存与会话状态管理。
- **可观测性**:
  - 集成 Prometheus Metrics (接口调用次数、耗时、Token 消耗)。
  - 结构化审计日志，支持 Prompt 效果追踪 (Template ID 关联)。

## 🛠️ 环境准备

确保本地环境已安装：
- Python >= 3.10
- Docker & Docker Compose

## 📦 初始化指南

### 1. 克隆项目
```bash
git clone https://github.com/ZouShuangDian/sunny_agent.git
cd sunny_agent
```

### 2. 启动基础设施(可选，用于开发环境)

#### 核心服务

使用 Docker Compose 一键启动 PostgreSQL、Redis、Milvus：

```bash
docker compose -f infra/docker-compose.yml up -d
```

| 服务 | 端口 | 用途 |
|------|------|------|
| PostgreSQL | 5432 | 主数据库（用户/密码：`root`/`abc123!`） |
| Redis | 6379 | 缓存与会话管理（密码：`abc123!`） |
| Milvus | 19530 | 向量数据库（依赖 etcd + MinIO） |

```bash
# 停止
docker compose -f infra/docker-compose.yml down
# 停止并清除数据
docker compose -f infra/docker-compose.yml down -v
```

#### Langfuse 可观测性（可选）

Langfuse 提供 LLM 调用链路追踪、Token 用量统计和评估实验功能。

```bash
docker compose -f infra/langfuse-compose.yml up -d
```

| 服务 | 端口 | 用途 |
|------|------|------|
| Langfuse Web | 3000 | UI 与 REST API |
| Langfuse Worker | 3030（仅本机） | 后台任务处理（Redis 队列 → ClickHouse） |
| PostgreSQL | 5433 | Langfuse 专用数据库（独立于主服务） |
| ClickHouse | — | Trace 存储与分析 |
| Redis | — | Langfuse 内部队列 |
| MinIO | — | 事件与媒体对象存储 |

启动后访问 Langfuse UI：http://localhost:3000
- 默认账号：`admin@sunnyagent.local` / `changeme123`
- 默认 API Key：`pk-lf-sunny-dev` / `sk-lf-sunny-dev`

在 `app/.env` 中配置连接：

```env
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-sunny-dev
LANGFUSE_SECRET_KEY=sk-lf-sunny-dev
```

```bash
# 停止
docker compose -f infra/langfuse-compose.yml down
# 停止并清除数据
docker compose -f infra/langfuse-compose.yml down -v
```

### 3. 安装依赖
本项目使用 `poetry` 进行依赖管理。

```bash
# 安装 poetry (如果尚未安装)
pip install poetry

# 安装项目依赖
poetry install
```

### 3. 配置文件
复制示例配置文件并根据本地环境进行修改。

```bash
cp app/.env.example app/.env
```
请编辑 `app/.env` 文件，填入正确的数据库连接信息、API Key 等：
- `DATABASE_URL`: PostgreSQL 连接串
- `REDIS_URL`: Redis 连接串
- `LLM_API_KEY`: 大模型 API Key
- `BOCHA_API_KEY`: 搜索工具 API Key

### 4. 数据库初始化 (Alembic)
本项目使用 Alembic 进行数据库版本控制。**这是启动前的必做步骤**。

```bash
# 激活虚拟环境 (可选，如果直接使用 poetry run 则不需要)
poetry shell

# 执行数据库迁移，同步表结构到数据库
alembic upgrade head
```

## 🚦 启动项目

使用 `uvicorn` 启动 FastAPI 服务：

```bash
# 在项目根目录下执行
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
或者，如果你配置了 `APP_PORT` 环境变量（默认为 8000）：
```bash
python -m uvicorn app.main:app --host 0.0.0.0
```

服务启动后：
- **Swagger 文档**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Metrics 监控**: [http://localhost:8000/metrics](http://localhost:8000/metrics)

## 🗄️ 数据库迁移常用命令 (Alembic)

当修改了 `app/db/models` 下的模型定义后，需要生成并执行迁移脚本。

### 生成迁移脚本
```bash
# -m 后面跟注释，描述本次修改的内容
alembic revision --autogenerate -m "描述你的修改内容"
```
*注意：生成的脚本位于 `app/db/migrations/versions/` 目录下，建议检查生成的代码是否符合预期。*

### 执行迁移 (生效)
```bash
# 将数据库升级到最新版本
alembic upgrade head
```

### 回滚迁移
```bash
# 回滚到上一个版本
alembic downgrade -1
```

## 📂 目录结构摘要
```
.
├── alembic.ini              # Alembic 配置文件
├── app/
│   ├── main.py              # 应用入口
│   ├── config.py            # 配置加载
│   ├── api/                 # API 路由层
│   ├── db/                  # 数据库相关
│   │   ├── migrations/      # 迁移脚本目录
│   │   └── models/          # ORM 模型定义
│   ├── intent/              # 意图识别引擎
│   ├── execution/           # 执行路由与逻辑
│   ├── tools/               # 工具集 (builtin_tools)
│   ├── observability/       # Langfuse 集成与监控
│   ├── services/            # 业务服务层
│   └── utils/               # 通用工具（加密等）
├── infra/                   # 基础设施部署配置
│   ├── docker-compose.yml   # 核心服务（PostgreSQL/Redis/Milvus）
│   ├── langfuse-compose.yml # Langfuse 可观测性服务栈
│   └── init-db.sql          # 数据库初始化脚本
├── scripts/                 # 脚本与实验代码
└── pyproject.toml           # 项目依赖定义
```
