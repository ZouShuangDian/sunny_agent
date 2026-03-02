# Agent Sunny — 整体架构 Spec

> 本文档描述 Agent Sunny 的完整系统设计，基于 `app/` 目录代码逆向整理，作为后续所有子模块 Spec 的索引入口。

---

## 1. 系统定位

Agent Sunny 是**舜宇集团制造业场景下的 AI Agent 平台**。
核心职责：接收用户自然语言请求 → 意图识别 → 路由至执行层（L1/L3）→ 工具调用/推理 → 输出校验 → 返回结构化答复。

---

## 2. 技术栈

| 层次 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 语言 | Python 3.11 |
| 数据库 | PostgreSQL（远程 115.190.21.247），schema：`sunny_agent` |
| ORM | SQLAlchemy 2.0（async） + Alembic（迁移） |
| 缓存 | Redis 7（远程 115.190.21.247:6379） |
| LLM | OpenAI 协议兼容，LiteLLM 格式（默认 DeepSeek-V3） |
| 向量数据库 | Milvus（HNSW + COSINE，用于 Prompt 语义检索） |
| Embedding | BAAI/bge-m3（dim=1024） |
| 配置管理 | pydantic-settings，从 `app/.env` 加载 |
| 日志 | structlog（结构化 JSON 日志） |
| 指标 | Prometheus + prometheus_client |
| 依赖管理 | Poetry（.venv 在项目根） |
| 沙箱执行 | sandbox-service HTTP sidecar（端口 8020） |

---

## 3. 模块总览

```
app/
├── main.py                  # FastAPI 入口，lifespan 管理
├── config.py                # 全局配置（pydantic-settings）
├── api/                     # HTTP 路由层
│   ├── chat.py              # POST /chat，POST /chat/stream
│   ├── files.py             # 文件上传/下载
│   ├── plugins.py           # Plugin 管理
│   └── health.py            # 健康检查
├── security/                # 安全网关（M01）
│   ├── auth.py              # JWT 鉴权 + 用户上下文
│   ├── login.py             # 登录/刷新/注销
│   ├── audit.py             # 审计日志（write-behind）
│   ├── injection_detector.py # 注入检测（stub）
│   └── rate_limiter.py      # 限流（stub）
├── intent/                  # 意图理解层（M03）
│   ├── intent_engine.py     # LLM 意图识别 + 路由决策
│   ├── context_builder.py   # 上下文组装（build + enrich）
│   ├── context_strategy.py  # 按意图类型加载扩展上下文
│   ├── codebook_service.py  # 码表实体解析 + Redis 缓存
│   ├── clarify_handler.py   # 追问处理
│   └── output_assembler.py  # 最终 IntentResult 组装
├── guardrails/              # 护栏层（M04）
│   ├── validator.py         # JSON修复 + 填充 + Schema校验 + 降级
│   ├── json_repairer.py     # 畸形 JSON 修复
│   ├── default_filler.py    # 缺失字段填充
│   ├── fallback_handler.py  # 降级结果生成
│   └── schemas.py           # IntentResult Pydantic 模型
├── execution/               # 执行层
│   ├── router.py            # ExecutionRouter（L1/L3 分发）
│   ├── l1/                  # L1 FastTrack（M05）
│   │   ├── fast_track.py    # Bounded Loop (max 3 steps)
│   │   ├── prompt_retriever.py # Milvus 语义检索 Prompt
│   │   ├── prompt_cache.py  # Prompt 缓存
│   │   ├── registry.py      # L1 注册
│   │   └── schemas.py
│   ├── l3/                  # L3 ReAct（M08）
│   │   ├── react_engine.py  # 编排器（Thinker→Actor→Observer）
│   │   ├── thinker.py       # LLM 决策
│   │   ├── actor.py         # 工具执行
│   │   ├── observer.py      # 熔断 + 预算 + 轨迹
│   │   ├── token_budget.py  # Token/时间/迭代次数限制
│   │   ├── prompts.py       # L3 系统 Prompt 构建
│   │   └── schemas.py
│   ├── session_context.py   # ContextVar: session_id
│   ├── user_context.py      # ContextVar: usernumb
│   ├── skill_context.py     # ContextVar: list[SkillInfo]
│   ├── plugin_context.py    # ContextVar: PluginCommandContext
│   ├── agent_context.py     # ContextVar: agent_context（SubAgent）
│   └── schemas.py           # ExecutionResult
├── tools/                   # 工具系统
│   ├── base.py              # BaseTool + ToolResult 抽象
│   ├── registry.py          # ToolRegistry（register + execute）
│   └── builtin_tools/       # 内置工具（11 个）
├── skills/                  # Skill 系统（DB 驱动）
│   └── service.py           # SkillService + SkillInfo
├── plugins/                 # Plugin 系统
│   └── service.py           # PluginService（DB + 文件）
├── subagents/               # SubAgent 系统
│   ├── loader.py            # agent.md 解析
│   ├── registry.py          # SubAgentRegistry（多目录）
│   └── executor.py          # LocalAgentExecutor 抽象基类
├── memory/                  # 记忆系统
│   ├── working_memory.py    # Redis Hash 工作记忆
│   ├── chat_persistence.py  # PostgreSQL 冷存储（write-behind）
│   └── schemas.py           # Message / ConversationHistory / SessionMeta
├── todo/                    # Todo 状态机
│   ├── store.py             # TodoStore（Redis，key=todo:{session_id}）
│   └── schemas.py           # TodoItem
├── llm/
│   └── client.py            # LLMClient（chat/chat_with_tools/chat_stream）
├── vectorstore/
│   ├── milvus_client.py     # Milvus 集合管理 + 检索
│   └── embedding.py         # BAAI/bge-m3 Embedding 调用
├── services/
│   └── prompt_service.py    # PromptService（Milvus 检索 + DB 意图类别）
├── validator/               # 输出校验层（M06）
│   ├── output_validator.py  # 三层校验编排
│   ├── numeric_validator.py # Layer 1：数值交叉校验
│   ├── hallucination_detector.py # Layer 2：幻觉检测
│   └── schemas.py
├── cache/
│   └── redis_client.py      # Redis 连接池 + RedisKeys 常量
├── db/
│   ├── engine.py            # AsyncEngine + async_session
│   ├── models/              # SQLAlchemy 模型（8 个表）
│   └── migrations/          # Alembic 迁移（head: f1e2d3c4b5a6）
├── observability/           # 可观测性（M13）
│   ├── logging_config.py    # structlog 初始化
│   ├── request_logger.py    # 请求日志中间件
│   ├── metrics.py           # Prometheus 指标定义
│   ├── metrics_middleware.py # 指标采集中间件
│   ├── context.py           # trace_id ContextVar
│   └── alerting.py          # 告警（stub）
└── prompts/
    └── markers.py           # TODO_REMINDER_MARKER 常量
```

---

## 4. 请求全链路

```
用户 HTTP 请求
    │
    ▼
[安全网关] JWT 鉴权 + 黑名单检查 → AuthenticatedUser
    │
    ▼
[路由判断]
    ├── /{plugin}:{command} → Plugin 快速路径（跳过意图分析）→ L3
    └── 普通消息 → 意图管线
                    │
                    ▼
             [ContextBuilder]
             build()  → Redis: history + last_intent
             enrich() → 按意图类型加载码表/知识/历史（桩）
                    │
                    ▼
             [IntentEngine] LLM 调用
             → intent_primary, route, complexity, confidence
             → needs_clarify, clarify_question
                    │
             ┌──────┴──────┐
             │ needs_clarify │ → 直接返回追问
             └──────┬──────┘
                    │
             [OutputAssembler] → IntentResult
             [GuardrailsValidator] JSON修复+校验+降级
                    │
                    ▼
             [ExecutionRouter]
             ┌──────┴──────┐
        standard_l1      deep_l3
             │                │
      [L1 FastTrack]   [L3 ReActEngine]
      Bounded Loop     Thinker→Actor→Observer
      max 3 steps      max 20 iter / 300s / 50 LLM calls
             │                │
             └──────┬──────────┘
                    │
             [OutputValidator] 数值校验 + 幻觉检测
                    │
                    ▼
             [记忆写入] Redis + PG（write-behind）
             [审计日志] write-behind PG
                    │
                    ▼
             HTTP Response / SSE Stream
```

---

## 5. 关键设计原则

### 5.1 ContextVar 请求隔离
所有请求级共享状态通过 Python ContextVar 传递，**不使用全局可变状态**：

| ContextVar | 内容 | 作用 |
|------------|------|------|
| `session_context` | session_id | Todo 读写、sandbox 路径 |
| `user_context` | usernumb | 沙箱用户隔离路径 |
| `skill_context` | list[SkillInfo] | 当前请求可用 Skill 目录 |
| `plugin_context` | PluginCommandContext | Plugin 命令元数据注入 |
| `agent_context` | agent_context | SubAgent 调用上下文 |
| `observability.context` | trace_id | 全链路追踪 |

所有 ContextVar 均用 `try/finally` + token 模式保证还原（即使异常也安全）。

### 5.2 DB 驱动配置（零重启热更新）
Skills、Plugins、Prompt Templates、Codebook、Intent Categories 全部存 DB，变更无需重启服务。

### 5.3 两速记忆
- **热层**（Redis TTL=30min）：会话历史、意图快照、元数据 → 低延迟在线访问
- **冷层**（PostgreSQL write-behind）：聊天记录、工具调用、推理轨迹 → 持久存储，Redis miss 时回源

### 5.4 工具分层（Tier）
- `tier=["L1", "L3"]`：通用工具（web_search, read_file 等），L1 也可调
- `tier=["L3"]`：高风险工具（bash_tool, write_file, skill_call 等），仅 L3 ReAct 可用

### 5.5 优雅降级链
```
IntentEngine 失败 → DEFAULT_RESULT（standard_l1, general_qa）
GuardrailsValidator 失败 → FallbackHandler 兜底 IntentResult
L3 熔断（超时/迭代超限）→ 基于已收集观察结果生成摘要，不额外 LLM 调用
OutputValidator 异常 → 静默跳过，返回原始输出（满置信度）
```

---

## 6. 数据库表清单

| 表名 | 描述 |
|------|------|
| `sunny_agent.users` | 用户信息（usernumb 唯一标识） |
| `sunny_agent.audit_logs` | 操作审计日志 |
| `sunny_agent.codebook` | 码表（术语归一化） |
| `sunny_agent.prompt_templates` | L1/L3 Prompt 模板 |
| `sunny_agent.skills` | Skill 元数据（system/user scope） |
| `sunny_agent.user_skill_settings` | 用户级 Skill 开关 |
| `sunny_agent.plugins` | Plugin 元数据 |
| `sunny_agent.plugin_commands` | Plugin 命令映射 |
| `sunny_agent.chat_sessions` | 会话索引 |
| `sunny_agent.chat_messages` | 聊天消息（含 tool_calls + reasoning_trace） |

Alembic head: `f1e2d3c4b5a6`

---

## 7. 子 Spec 索引

| 文件 | 覆盖模块 |
|------|---------|
| [security.md](./security.md) | 安全网关（M01）：JWT、审计、限流 |
| [intent-pipeline.md](./intent-pipeline.md) | 意图管线（M03）：上下文构建、意图引擎、护栏 |
| [execution-engine.md](./execution-engine.md) | 执行层（M05/M08）：L1 FastTrack、L3 ReAct |
| [tool-system.md](./tool-system.md) | 工具系统：BaseTool、内置工具清单、Skill、SubAgent |
| [memory-system.md](./memory-system.md) | 记忆系统：WorkingMemory、ChatPersistence、Todo |
| [plugin-system.md](./plugin-system.md) | Plugin 系统：上传、命令执行、隔离 |
| [observability.md](./observability.md) | 可观测性：日志、Prometheus、告警 |
| [data-models.md](./data-models.md) | 数据库模型 + Milvus Schema |
