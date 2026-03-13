# Feishu ARQ Integration - 架构说明

## 项目架构

本项目实现了飞书（Feishu）消息处理系统，采用 **外部 Webhook 服务 + 内部 Worker** 的分离架构。

```
┌─────────────────────────────────────────────────────────────────┐
│                        飞书开放平台                               │
│                         (Feishu Open Platform)                   │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       │ 事件推送
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                  外部 Webhook 服务                               │
│              (feishu-sunnyagent-api 项目)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ 签名验证     │  │ 消息解密     │  │ 去重/限流/过滤           │  │
│  │ Signature   │  │ Decrypt     │  │ Deduplication           │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│                          │                                      │
│                          ▼                                      │
│              Redis List: feishu:webhook:queue                   │
│              (外部服务推送到此队列)                               │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       │ BRPOP (阻塞读取)
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Feishu Worker (本项目)                        │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              message_transfer_loop (桥接器)              │    │
│  │  - Source: feishu:webhook:queue (Redis List)            │    │
│  │  - Target: arq:feishu:queue (ARQ Queue)                 │    │
│  │  - 可靠传输: processing:queue (临时存储)                 │    │
│  │  - 幂等校验: feishu:processed:{event_id}:{msg_id}       │    │
│  └────────────────────┬────────────────────────────────────┘    │
│                       │                                         │
│                       ▼                                         │
│              ARQ Queue: arq:feishu:queue                        │
│                       │                                         │
│                       ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           process_feishu_message (ARQ Task)             │    │
│  │  1. Debounce (Redis-based, 2s wait)                     │    │
│  │  2. Access Control (PostgreSQL + cache)                 │    │
│  │  3. User Resolution (open_id → user_id)                 │    │
│  │  4. Media Download (30MB limit, SHA256 dedup)           │    │
│  │  5. AI Pipeline (Agent execution)                       │    │
│  │  6. BlockStreaming (800-1200 chars buffer)              │    │
│  │  7. Reply via Feishu API                                │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

## 外部 Webhook 服务

### 项目信息
- **项目名称:** `feishu-sunnyagent-api`
- **项目路径:** `D:\ai_project_2026\feishu-sunnyagent-api`
- **Webhook URL:** https://larkchannel.51dnbsc.top/webhook
- **Git:** 独立仓库，独立部署

### 功能特性
- ✅ **签名验证:** SHA256(timestamp + nonce + encrypt_key + body)
- ✅ **消息解密:** AES-256-CBC (PKCS7 padding)
- ✅ **去重机制:** Redis-based，24小时窗口
- ✅ **限流保护:** 全局令牌桶 (100/s) + 用户滑动窗口 (5/min)
- ✅ **消息过滤:** 私聊、群@消息过滤，防止消息回环
- ✅ **消息过滤:** 私聊、群@消息过滤，防止消息回环
- ✅ **队列推送:** Redis List (`feishu:webhook:queue`)，使用 LPUSH

### 技术栈
- Python 3.11+
- FastAPI + Uvicorn
- Redis (aioredis) - List
- Pydantic + Pydantic-Settings
- Structlog
- Docker + Docker Compose

## 内部 Worker (本项目)

### 核心模块

#### 1. 数据库模型 (`app/db/models/feishu.py`)
- `FeishuAccessConfig` - 访问控制配置
- `FeishuGroupConfig` - 群组特定配置
- `FeishuUserBindings` - 用户绑定关系
- `FeishuMediaFiles` - 媒体文件元数据
- `FeishuMessageLogs` - 消息审计日志
- `FeishuChatSessionMapping` - 会话映射

#### 2. 核心服务

**FeishuClient** (`app/feishu/client.py`)
- Token 管理（Redis 缓存，TTL 7000s）
- 消息发送（文本、富文本）
- 流式卡片（创建、更新、关闭）
- 媒体下载
- 错误处理和重试（3次）
- 限流处理（指数退避）

**AccessController** (`app/feishu/access_control.py`)
- DM 策略检查（open/allowlist/disabled）
- 群组策略检查
- 白名单验证
- 配置缓存（5分钟 TTL）

**UserResolver** (`app/feishu/user_resolver.py`)
- open_id → employee_no → user_id 映射
- 用户绑定管理
- Redis 缓存（1小时 TTL）

**MediaDownloader** (`app/feishu/media_downloader.py`)
- 文件下载（8KB chunks）
- 30MB 大小限制
- SHA256 去重
- 重试逻辑（3次）

**DebounceManager** (`app/feishu/debounce.py`)
- 时间防抖（2秒等待，可配置）
- 无文本防抖（3秒等待，可配置）
- 消息合并（\n\n 分隔）
- 批量消费（默认 10 条）
- DebounceScanner（5秒扫描）
- should_debounce 钩子支持

**BlockStreaming** (`app/feishu/block_streaming.py`)
- 文本累积缓冲（min: 800, max: 1200 chars）
- 段落感知 flush
- 空闲检测（1000ms）
- 长文本分块（>2000 chars）
- 流式卡片 + 普通消息混合发送

#### 3. ARQ Worker

**Worker 配置** (`app/worker_feishu.py`)
- 独立队列: `arq:feishu:queue`
- 并发数: 10
- 任务超时: 300 秒
- 结果保留: 3600 秒

**消息消费** (`app/feishu/tasks.py`)
- 从 Redis Stream (`feishu:messages`) 消费
- 消费者组: `feishu_workers`
- 幂等校验（24小时 TTL）
- 自动 ACK 处理

## 配置

### 环境变量 (.env)

```bash
# 数据库
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/dbname

# Redis (与外部 Webhook 服务共用)
REDIS_URL=redis://localhost:6379/0

# 飞书应用凭证
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 注意：不需要 FEISHU_ENCRYPT_KEY 和 FEISHU_VERIFICATION_TOKEN
# 这些由外部 Webhook 服务 (feishu-sunnyagent-api) 管理
```

### 外部 Webhook 服务配置

外部服务配置在 `feishu-sunnyagent-api` 项目中：
- `.env` 文件中的 `FEISHU_ENCRYPT_KEY`
- `.env` 文件中的 `FEISHU_VERIFICATION_TOKEN`
- `.env` 文件中的 `FEISHU_APP_ID`

## 部署

### 1. 外部 Webhook 服务

```bash
cd D:\ai_project_2026\feishu-sunnyagent-api

# 启动服务
docker-compose up -d

# 或使用本地 Python
poetry install
poetry run uvicorn src.feishu_webhook.main:app --host 0.0.0.0 --port 8000
```

### 2. 数据库迁移

```bash
cd D:\ai_project_2026\sunny_agent

# 运行 Alembic 迁移
alembic upgrade head
```

### 3. Feishu Worker

```bash
cd D:\ai_project_2026\sunny_agent

# 启动 Worker
arq app.worker_feishu.WorkerSettings
```

### 4. 主应用（Web 界面）

```bash
cd D:\ai_project_2026\sunny_agent

# 启动 FastAPI 主应用
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 队列说明

### 消息流转

1. **飞书平台** → 推送事件到 **外部 Webhook 服务**
2. **外部 Webhook 服务** → 验证、解密、去重 → 推送到 **Redis List** (`feishu:webhook:queue`)
3. **Feishu Worker** → 使用 BRPOP 从 List 消费 → 入队到 **ARQ Queue** (`arq:feishu:queue`)
4. **ARQ Worker** → 处理任务 → 发送回复到飞书

### 队列对比

| 队列 | 类型 | 位置 | 用途 |
|------|------|------|------|
| `feishu:webhook:queue` | Redis List | 外部 Webhook 服务推送 | 接收飞书事件 |
| `feishu:processing:queue` | Redis List | 本项目 Worker (临时) | 可靠传输缓冲 |
| `arq:feishu:queue` | ARQ Queue | 本项目 Worker | 处理飞书消息 |
| `sunny:queue` | ARQ Queue | 本项目 Worker | 定时任务 |

## 监控

### 外部 Webhook 服务
- 健康检查: https://larkchannel.51dnbsc.top/health
- 指标: Stream 长度 (`feishu:messages`)、Pending 数量
- 日志: `/var/log/feishu/`

### 内部 Worker
- ARQ 监控: Redis keys `arq:*`
- 消息日志: `feishu_message_logs` 表
- 结构化日志: structlog

## 故障排查

### 消息未处理
1. 检查外部 Webhook 服务是否正常运行
2. 检查 Redis List 是否有消息: `LRANGE feishu:webhook:queue 0 -1`
3. 检查 Worker 是否启动并连接到正确的 Redis
4. 检查 processing 队列是否有积压: `LLEN feishu:processing:queue`

### 消息重复
- 外部服务有 24 小时去重窗口
- Worker 有额外的幂等校验（`feishu:processed:*`）
- 即使重复入队，ARQ 任务也会幂等处理

### Worker 崩溃
- List 中的消息会保留直到被消费
- 重启 Worker 后会自动重新消费
- processing 队列用于故障恢复，重启时会检查并重新处理

### 清理 processing 队列
如果 processing 队列有大量积压（可能是 Worker 崩溃导致）：
```bash
# 查看积压数量
LLEN feishu:processing:queue

# 清空 processing 队列（谨慎操作！）
DEL feishu:processing:queue
```

## 相关项目

- **外部 Webhook 服务:** `D:\ai_project_2026\feishu-sunnyagent-api`
- **本项目:** `D:\ai_project_2026\sunny_agent`

## 开发团队

- **外部 Webhook:** feishu-sunnyagent-api 团队
- **内部 Worker:** sunny_agent 团队
