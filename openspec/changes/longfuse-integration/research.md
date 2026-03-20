# Research: Langfuse 集成技术调研

**Date**: 2026-03-13
**Status**: Complete

---

## R1: LLM 调用追踪方式 — LiteLLM vs Langfuse SDK

### Decision: 使用 LiteLLM 内置 Langfuse Callback

### Rationale
SunnyAgent 使用 LiteLLM（`litellm.acompletion`）而非直接使用 LangChain/LangGraph 进行 LLM 调用。LiteLLM 内置了 Langfuse callback 支持，只需设置环境变量即可自动上报所有 LLM 调用的 Trace：

```python
# 方式 1：环境变量（全局生效）
LITELLM_CALLBACKS=langfuse

# 方式 2：代码设置
import litellm
litellm.success_callback = ["langfuse"]
litellm.failure_callback = ["langfuse"]
```

这种方式的优势：
- **零改动 LLMClient**：无需修改 `app/llm/client.py`
- **自动采集**：所有通过 `acompletion()` 的调用自动记录 model、tokens、latency、cost
- **Langfuse 原生 cost 计算**：LiteLLM callback 会传递 model 信息，Langfuse 自动匹配价格表

### Alternatives Considered
1. **Langfuse `@observe()` 装饰器**：需要改造每个调用点，侵入性大
2. **LangChain Callback Handler**：项目不使用 LangChain，不适用
3. **OpenTelemetry SDK**：过于重量级，需要额外基础设施

---

## R2: ReAct 引擎 Span 嵌套方案

### Decision: 使用 Langfuse Python SDK `@observe()` 装饰器 + `langfuse.trace()` 手动创建顶层 Trace

### Rationale
L3 ReAct 引擎的执行结构为：
```
Trace (一次对话请求)
└── Span: react_loop
    ├── Span: think (step 1) → Generation: LLM call
    ├── Span: act (step 1)
    │   ├── Span: tool_call_1
    │   └── Span: tool_call_2
    ├── Span: think (step 2) → Generation: LLM call
    └── Span: act (step 2)
```

实现方式：
1. 在 `app/api/chat.py` 的请求入口创建顶层 Trace，携带 `user_id` 和 `session_id`
2. 在 ReAct 引擎的关键方法上使用 `@observe()` 装饰器自动创建嵌套 Span
3. LiteLLM callback 自动将 LLM 调用作为 Generation 记录

注意：在 async generator 中，需使用 `langfuse.start_span()` 直接引用方式而非上下文管理器，避免 OpenTelemetry context 丢失（spec 中 Architecture Decision #7 已记录此决策）。

### Alternatives Considered
1. **纯 LiteLLM callback**：只能记录 LLM 调用，无法记录工具执行等非 LLM Span
2. **自定义 Middleware**：利用现有 ReActMiddleware 体系添加 Langfuse 中间件，但嵌套关系难以表达
3. **手动 span management 全程**：灵活但代码侵入性大

---

## R3: Langfuse 初始化环境变量（LANGFUSE_INIT_*）

### Decision: 使用 Langfuse v3 docker-compose 的 `LANGFUSE_INIT_*` 系列环境变量

### Rationale
Langfuse v3 支持通过环境变量在首次启动时自动初始化：

```yaml
environment:
  # 初始化组织
  LANGFUSE_INIT_ORG_ID: "sunny-org"
  LANGFUSE_INIT_ORG_NAME: "SunnyAgent"
  # 初始化项目
  LANGFUSE_INIT_PROJECT_ID: "sunny-project"
  LANGFUSE_INIT_PROJECT_NAME: "SunnyAgent"
  LANGFUSE_INIT_PROJECT_PUBLIC_KEY: "pk-lf-sunny-xxx"
  LANGFUSE_INIT_PROJECT_SECRET_KEY: "sk-lf-sunny-xxx"
  # 初始化管理员用户
  LANGFUSE_INIT_USER_EMAIL: "${LANGFUSE_ADMIN_EMAIL}"
  LANGFUSE_INIT_USER_PASSWORD: "${LANGFUSE_ADMIN_PASSWORD}"
  LANGFUSE_INIT_USER_NAME: "SunnyAgent Admin"
```

这些值只在首次启动（数据库为空）时生效，后续重启不会覆盖。

### Alternatives Considered
1. **Runtime Admin API 调用**：需要额外的 API Key 管理，复杂度高
2. **直接操作 Langfuse 数据库**：耦合度高，版本升级风险大

---

## R4: Langfuse 控制台代理登录

### Decision: 调用 Langfuse NextAuth API 获取 session cookie

### Rationale
Langfuse 使用 NextAuth.js 进行认证。可通过以下流程实现代理登录：

1. SunnyAgent 后端 `POST {LANGFUSE_HOST}/api/auth/callback/credentials` 发送 email + password
2. 获取响应中的 `Set-Cookie` header（`next-auth.session-token`）
3. 构造重定向 URL，前端设置 cookie 后跳转到 Langfuse

注意事项：
- Langfuse 和 SunnyAgent 如果不同域，需通过后端代理中转 cookie
- 降级方案：直接跳转到 `{LANGFUSE_HOST}/auth/sign-in`，管理员手动登录

### Alternatives Considered
1. **iframe 嵌入**：Langfuse 可能设置 X-Frame-Options，且体验差
2. **共享数据库 session**：侵入性极强，版本升级必破

---

## R5: Token 用量统计 — Langfuse API 可用性

### Decision: 使用 Langfuse Public API (`/api/public/traces`) 聚合用量数据

### Rationale
Langfuse v3 Public API 提供以下可用接口：

- `GET /api/public/traces` — 分页查询 Trace，支持 userId 筛选，返回 `totalCost`
- `GET /api/public/observations` — 查询 Span/Generation，包含 token 用量明细
- `GET /api/public/metrics/daily` — 按日聚合指标（**待 Phase 1.5 Spike 3 验证 v3 可用性**）
- `GET /api/public/metrics/usage` — 汇总用量指标（**待 Phase 1.5 Spike 3 验证 v3 可用性**）

SunnyAgent 后端需要：
1. 定期（或按需）从 Langfuse API 拉取数据
2. 在内存或 Redis 中缓存聚合结果（避免每次请求都调 Langfuse）
3. 按 user_id 维度聚合后返回给前端

### Alternatives Considered
1. **直接查 ClickHouse**：绕过 Langfuse API，但耦合底层存储，升级风险大
2. **实时代理转发**：每次前端请求都透传 Langfuse API，延迟高且无法自定义聚合

---

## R6: Trace 数据导出实现

### Decision: 通过 Langfuse Public API 分页拉取 + SunnyAgent 后端格式化输出

### Rationale
Langfuse `GET /api/public/traces` 支持分页和时间范围筛选。SunnyAgent 后端负责：
1. 按时间范围和 userId 过滤拉取 Trace 数据
2. 转换为 JSON 或 CSV 格式
3. 以文件下载方式返回给前端

对于大数据量（>10000 条），限制单次导出上限并提示用户缩小范围。

### Alternatives Considered
1. **ClickHouse 直连导出**：绕过 Langfuse API 直接查询 ClickHouse，性能更好但耦合底层存储
2. **后台异步任务导出**：使用 arq Worker 异步生成导出文件，适合大数据量但增加系统复杂度

---

## R7: 内置 Langfuse 服务 Docker Compose 管理

### Decision: SunnyAgent 后端通过 `subprocess` 调用 `docker compose` CLI

### Rationale
SunnyAgent 后端以 Python `asyncio.create_subprocess_exec` 方式调用：
- `docker compose -f infra/langfuse-compose.yml up -d` — 启动
- `docker compose -f infra/langfuse-compose.yml down` — 停止
- `docker compose -f infra/langfuse-compose.yml ps --format json` — 查状态

将 Langfuse 的 docker-compose 配置与现有 `infra/docker-compose.yml` 分离，使用独立的 `infra/langfuse-compose.yml`，避免影响现有服务。

### Alternatives Considered
1. **合并到现有 docker-compose.yml**：启停 Langfuse 会影响 PG/Redis/Milvus
2. **Python docker SDK**：额外依赖，且 compose 编排能力不如 CLI
