# Implementation Plan: Langfuse 可观测性集成

**Date**: 2026-03-13
**Branch**: `feature/langfuse`
**Spec**: `openspec/changes/longfuse-integration/spec.md`

---

## Technical Context

### 后端 (sunny_agent)

| Item | Value |
|------|-------|
| Language | Python 3.11+ |
| Framework | FastAPI + SQLAlchemy Async + LiteLLM |
| Database | PostgreSQL 15 (schema: sunny_agent) |
| Cache | Redis 7 |
| LLM Client | LiteLLM (`acompletion`) |
| Auth | JWT + Role-based (is_super_admin) |
| Execution Engine | L3 ReAct (Thinker → Actor → Observer) |
| Config | pydantic-settings + .env |
| Response Format | `ApiResponse` envelope (success/code/message/data) |
| Existing Observability | structlog + Prometheus + trace_id ContextVar |

### 前端 (sunny-agent-web)

| Item | Value |
|------|-------|
| Language | TypeScript |
| Framework | Vue 3 + Vite |
| UI Library | Element Plus |
| HTTP Client | Axios (src/utils/request.ts，Bearer token 认证) |
| State Management | Pinia |
| Routing | Vue Router 4 |
| API Pattern | `src/api/{feature}/index.ts` 按功能模块组织 |
| Existing Admin Panel | `src/components/admin-manage/` (用户管理、系统设置空壳、定时任务) |
| Admin Access | `UserRoleType.Admin` 角色可见管理面板 |
| Base URL | `{VITE_API_URL}/api`，开发环境默认 `http://127.0.0.1:8000/api` |

> **前端关键发现**: `system-manage/index.vue` 目前为空壳，可直接扩展为可观测性设置页面。Admin sidebar 已有三个 tab（USER / SYSTEM / SCHEDULED_TASK），新增 OBSERVABILITY tab 即可。

---

## Implementation Phases

### Phase 1: 基础设施与配置（P1 基础）

> 目标：Langfuse 服务可启动，配置项就绪，数据库模型创建

#### Task 1.1: 新增 Langfuse 配置项到 Settings

**文件**: `app/config.py`

新增配置项：
```python
# Langfuse
LANGFUSE_HOST: str = ""
LANGFUSE_PUBLIC_KEY: str = ""
LANGFUSE_SECRET_KEY: str = ""
LANGFUSE_ADMIN_EMAIL: str = ""
LANGFUSE_ADMIN_PASSWORD: str = ""
LANGFUSE_ENABLED: bool = False
LANGFUSE_SAMPLE_RATE: float = 1.0
LANGFUSE_FLUSH_INTERVAL: int = 5
```

#### Task 1.2: 创建 LangfuseConfig 数据库模型

**新建文件**: `app/db/models/langfuse_config.py`
**修改文件**: `app/db/models/__init__.py`（注册 import）

参考 `data-model.md` 中的 SQLAlchemy 定义。

#### Task 1.3: 创建 Alembic 迁移

```bash
alembic revision --autogenerate -m "add langfuse_config table"
alembic upgrade head
```

#### Task 1.4: 创建 Langfuse Docker Compose 配置

**新建文件**: `infra/langfuse-compose.yml`

包含 5 个服务：
- ClickHouse (`clickhouse/clickhouse-server:24.3`)
- Redis (`redis:7-alpine`) — Langfuse 专用，与 SunnyAgent Redis 独立
- MinIO (`minio/minio:latest`)
- PostgreSQL (`postgres:15`) — Langfuse 专用，与 SunnyAgent PG 独立
- Langfuse (`langfuse/langfuse:3`)

关键环境变量：
```yaml
langfuse:
  environment:
    DATABASE_URL: postgresql://langfuse:langfuse@langfuse-postgres:5432/langfuse
    CLICKHOUSE_URL: http://langfuse-clickhouse:8123
    REDIS_HOST: langfuse-redis
    S3_ENDPOINT: http://langfuse-minio:9000
    S3_ACCESS_KEY_ID: minioadmin
    S3_SECRET_ACCESS_KEY: minioadmin
    S3_BUCKET_NAME: langfuse
    # 自动初始化
    LANGFUSE_INIT_ORG_ID: sunny-org
    LANGFUSE_INIT_ORG_NAME: SunnyAgent
    LANGFUSE_INIT_PROJECT_NAME: SunnyAgent
    LANGFUSE_INIT_PROJECT_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY}
    LANGFUSE_INIT_PROJECT_SECRET_KEY: ${LANGFUSE_SECRET_KEY}
    LANGFUSE_INIT_USER_EMAIL: ${LANGFUSE_ADMIN_EMAIL}
    LANGFUSE_INIT_USER_PASSWORD: ${LANGFUSE_ADMIN_PASSWORD}
    LANGFUSE_INIT_USER_NAME: SunnyAgent Admin
  ports:
    - "3000:3000"
```

#### Task 1.5: 添加 Python 依赖

```bash
poetry add langfuse cryptography
```

#### Task 1.6: 创建加密工具模块

**新建文件**: `app/utils/crypto.py`

实现 `encrypt_secret()` / `decrypt_secret()` / `generate_encryption_key()`，使用 `cryptography.fernet.Fernet`，密钥从 `ENCRYPTION_KEY` 环境变量获取。参考 `data-model.md` 中的加密方案定义。

若 `.env` 中 `ENCRYPTION_KEY` 为空，启动时自动调用 `generate_encryption_key()` 生成并追加写入 `.env`。

#### Task 1.7: 实现配置加载逻辑（Source of Truth）

**新建文件**: `app/services/langfuse_config_loader.py`

```python
async def load_langfuse_config(db: AsyncSession) -> LangfuseConfig:
    """
    配置加载优先级：
    1. 数据库 langfuse_config 表（如果记录存在且 initialized=True）
    2. .env 文件（首次启动或数据库无记录时），读取后写入数据库
    """
```

在 `app/main.py` lifespan 中调用，确保启动时配置已就绪。

**验收**:
- `docker compose -f infra/langfuse-compose.yml up -d` 可启动
- Langfuse 可通过 `http://localhost:3000` 访问
- 使用 .env 中配置的 email/password 可登录
- 数据库迁移成功，langfuse_config 表已创建
- `ENCRYPTION_KEY` 自动生成并写入 .env
- Secret Key 在数据库中以密文存储

---

### Phase 1.5: 技术 Spike（阻塞 Phase 2）

> 目标：验证两个关键技术假设，明确 Phase 2 实现方案。如验证失败需调整后续方案。

#### Spike 1: LiteLLM Langfuse Callback 与 async generator 兼容性

**验证步骤**:
1. 启动 Langfuse 内置服务
2. 配置 `litellm.success_callback = ["langfuse"]`
3. 调用 `litellm.acompletion(..., stream=True)` 并通过 `async for chunk in response` 消费
4. 检查 Langfuse 中是否生成完整的 Generation 记录（包含 model、total_tokens、cost）

**如果不兼容**:
- Phase 2 Task 2.2 改为手动 Generation 记录方案：在 `app/llm/client.py` 的 `chat_stream()` 中手动调用 `trace.generation(name="llm_call", model=..., usage={...})`
- 需在流式消费完成后聚合 token 用量再上报

#### Spike 2: LANGFUSE_INIT_* 环境变量幂等性

**验证步骤**:
1. 使用 `LANGFUSE_INIT_*` 环境变量首次启动 Langfuse，确认组织、项目、API Key 自动创建
2. 停止容器（`docker compose down`，保留 volume），重新启动，确认不重复创建
3. 删除 volume（`docker compose down -v`），重新启动，确认重新创建——此时验证旧 API Key 是否失效

**如果幂等性有问题**:
- 增加启动时 API Key 有效性校验逻辑
- 在 `langfuse_config_loader.py` 中增加 Key 失效检测和自动重新初始化

#### Spike 3: Langfuse v3 Public API 用量端点可用性

**验证步骤**:
1. 启动 Langfuse v3 内置服务，发送几条 Trace
2. 验证 `GET /api/public/metrics/daily` 是否存在且返回预期格式
3. 验证 `GET /api/public/metrics/usage` 是否存在且返回预期格式
4. 如不存在，验证 `GET /api/public/traces` 分页接口的返回字段（是否包含 token 用量和 cost）

**如果 metrics 端点不可用**:
- Phase 3 Task 3.1 中的 `get_usage_summary()` / `get_usage_daily()` 改用 `GET /api/public/traces` 分页拉取
- SunnyAgent 后端自行按日聚合 token/cost 数据
- Redis 缓存策略不变（5min TTL）

**验收**: 输出技术验证报告，明确 Phase 2 和 Phase 3 的最终实现方案

---

### Phase 2: Trace 集成（P1 核心）

> 目标：Agent 执行自动产生 Trace，Span 正确嵌套，携带 user_id 和 session_id

#### Task 2.1: 初始化 Langfuse 客户端

**新建文件**: `app/observability/langfuse_client.py`

```python
"""
Langfuse 客户端：全局单例，惰性初始化
"""
from langfuse import Langfuse
from app.config import get_settings

_langfuse: Langfuse | None = None

def get_langfuse() -> Langfuse | None:
    """获取 Langfuse 客户端，未启用时返回 None"""
    global _langfuse
    settings = get_settings()
    if not settings.LANGFUSE_ENABLED:
        return None
    if _langfuse is None:
        _langfuse = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
            sample_rate=settings.LANGFUSE_SAMPLE_RATE,
            flush_interval=settings.LANGFUSE_FLUSH_INTERVAL,
        )
    return _langfuse

async def shutdown_langfuse():
    """优雅关闭，flush 缓冲数据"""
    global _langfuse
    if _langfuse:
        _langfuse.flush()
        _langfuse = None
```

#### Task 2.2: 配置 LiteLLM Langfuse Callback

**修改文件**: `app/main.py`（lifespan）

在应用启动时配置 LiteLLM callback：
```python
if settings.LANGFUSE_ENABLED:
    import litellm
    litellm.success_callback = ["langfuse"]
    litellm.failure_callback = ["langfuse"]
```

在应用关闭时 flush：
```python
from app.observability.langfuse_client import shutdown_langfuse
await shutdown_langfuse()
```

#### Task 2.3: 在 Chat API 入口创建顶层 Trace

**修改文件**: `app/api/chat.py`

在 chat 请求处理入口创建 Langfuse Trace，携带 user_id 和 session_id：

```python
langfuse = get_langfuse()
trace = None
if langfuse:
    trace = langfuse.trace(
        name="chat_request",
        user_id=current_user.usernumb,
        session_id=session_id,
        metadata={"source": "api", "model": settings.LLM_DEFAULT_MODEL},
    )
```

将 trace 对象通过 ContextVar 传递给下游。

#### Task 2.4: 新增 Langfuse Trace ContextVar

**修改文件**: `app/observability/context.py`

```python
from langfuse.client import StatefulTraceClient
langfuse_trace_var: contextvars.ContextVar[StatefulTraceClient | None] = (
    contextvars.ContextVar("langfuse_trace", default=None)
)
```

#### Task 2.5: 在 ReAct 引擎中添加 Span

**修改文件**: `app/execution/l3/react_engine.py`

在 `run()` 方法中创建 `react_loop` Span：
```python
trace = langfuse_trace_var.get()
if trace:
    span = trace.span(name="react_loop", metadata={"max_iterations": cfg.max_iterations})
```

**修改文件**: `app/execution/l3/thinker.py`

在 `think()` 中创建 `think` Span（记录 LLM 调用参数和结果）。

**修改文件**: `app/execution/l3/actor.py`

在 `act()` 中为每个 tool_call 创建 `tool:{name}` Span。

#### Task 2.6: 错误信息记录到 Trace

在异常处理中将错误信息更新到 Trace/Span：
```python
if trace:
    trace.update(status_message=str(error), level="ERROR")
```

#### Task 2.7: 实现 Trace 数据 PII 脱敏

**新建文件**: `app/observability/pii_filter.py`

实现 `before_send` hook，在 Langfuse SDK flush 前过滤 input/output 中的 PII 数据：

```python
"""
Trace 数据 PII 脱敏层
在 Langfuse SDK 上报前过滤敏感信息，满足 NFR-008
"""
import re

# 内置 PII 模式
BUILTIN_PII_PATTERNS = [
    (r'\b1[3-9]\d{9}\b', '[PHONE_REDACTED]'),              # 手机号
    (r'\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b',
     '[ID_CARD_REDACTED]'),                                   # 身份证号
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
     '[EMAIL_REDACTED]'),                                     # 邮箱
    (r'(?i)(?:password|passwd|pwd|secret|token|api_key)\s*[:=]\s*\S+',
     '[CREDENTIAL_REDACTED]'),                                # 密码/密钥键值对
]

def scrub_pii(text: str, extra_patterns: list[tuple[str, str]] | None = None) -> str:
    """对文本进行 PII 脱敏"""
    all_patterns = BUILTIN_PII_PATTERNS + (extra_patterns or [])
    for pattern, replacement in all_patterns:
        text = re.sub(pattern, replacement, text)
    return text
```

在 `langfuse_client.py` 初始化时注册脱敏 hook：
```python
_langfuse = Langfuse(
    ...,
    sdk_integration="sunnyagent",
)
# 注册 input/output 脱敏回调（具体 hook 机制取决于 Spike 1 验证结果）
```

**验收**:
- 发送一条 chat 消息后，在 Langfuse 界面可看到完整 Trace
- Trace 包含 user_id（usernumb）和 session_id
- Span 层级正确：chat_request → react_loop → think/act → tool_calls
- LLM 调用自动记录 model、tokens、cost
- Agent 执行出错时 Trace 记录错误信息
- Langfuse 不可用时 Agent 正常工作（优雅降级）
- Trace 中的用户输入经过 PII 脱敏（手机号、身份证号等被替换为 `[REDACTED]`）

---

### Phase 3: 可观测性 API（P1）

> 目标：前端可调用 API 获取 Langfuse 状态、用量统计

#### Task 3.1: 创建 Observability Service

**新建文件**: `app/services/observability.py`

封装所有 Langfuse 交互逻辑：
- `get_status()` — 健康检查（调用 Langfuse `/api/public/health`）
- `get_console_url()` — 代理登录获取跳转 URL
- `get_usage_summary(start, end, user_id)` — 用量汇总
- `get_usage_daily(start, end, user_id)` — 按日趋势
- `get_usage_by_user(start, end)` — 用户分布
- `refresh_usage()` — 手动刷新缓存
- `export_traces(start, end, format, user_id)` — 数据导出

内部使用 `httpx.AsyncClient` 调用 Langfuse API，结果缓存到 Redis。

#### Task 3.2: 创建 Observability Router

**新建文件**: `app/api/observability.py`

实现 spec 中定义的 6 个 API 端点：
1. `GET /api/v1/observability/status` — 已登录用户
2. `GET /api/v1/observability/console-url` — 管理员
3. `GET /api/v1/observability/usage/summary` — 权限控制
4. `GET /api/v1/observability/usage/daily` — 权限控制
5. `GET /api/v1/observability/usage/by-user` — 仅管理员
6. `POST /api/v1/observability/usage/refresh` — 管理员

权限控制：
- 使用 `get_current_user` 依赖获取当前用户
- 管理员判断：`is_super_admin(user)` 或检查 permissions 包含 "admin"
- 普通用户用量查询自动注入 `user_id=current_user.usernumb`

#### Task 3.3: 注册路由

**修改文件**: `app/main.py`

```python
from app.api.observability import router as observability_router
app.include_router(observability_router, prefix="/api/v1/observability", tags=["observability"])
```

**验收**:
- 所有 6 个 API 端点可调用
- 管理员可查看所有用户数据
- 普通用户只能查看自己的数据
- 用量数据与 Langfuse 一致
- Redis 缓存生效（5 分钟 TTL）

---

### Phase 4: Langfuse 服务管理 API（P1）

> 目标：管理员可通过 API 启停内置服务、配置外部服务

#### Task 4.1: 创建 Langfuse 服务管理 Service

**新建文件**: `app/services/langfuse_manager.py`

封装内置服务管理：
- `start_builtin()` — `docker compose -f infra/langfuse-compose.yml up -d`
- `stop_builtin()` — `docker compose -f infra/langfuse-compose.yml down`
- `get_builtin_status()` — `docker compose ps --format json`
- `validate_connection(url)` — 检测外部服务连通性
- `update_config(config)` — 仅更新数据库 `langfuse_config` 表（运行时配置变更不修改 .env，.env 仅用于首次初始化种子值）

通过 `asyncio.create_subprocess_exec` 异步调用 Docker CLI。

#### Task 4.2: 扩展 Observability Router

**修改文件**: `app/api/observability.py`

新增管理端点：
1. `GET /api/v1/observability/config` — 获取配置
2. `PUT /api/v1/observability/config` — 更新配置
3. `POST /api/v1/observability/config/validate` — 验证连接
4. `POST /api/v1/observability/config/initialize` — 手动初始化
5. `POST /api/v1/observability/builtin-service/start` — 启动内置服务
6. `POST /api/v1/observability/builtin-service/stop` — 停止内置服务
7. `GET /api/v1/observability/builtin-service/status` — 内置服务状态

所有管理端点仅管理员可访问。

**验收**:
- 管理员可启动/停止内置 Langfuse 服务
- 管理员可配置外部 Langfuse 服务并验证连通性
- 配置变更持久化到数据库（运行时变更不写 .env）
- 非管理员调用管理端点返回 403

---

### Phase 5: Trace 数据导出（P2）

> 目标：支持导出 Trace 数据为 JSON/CSV 格式

#### Task 5.1: 实现 Trace 导出逻辑

**修改文件**: `app/services/observability.py`

在 ObservabilityService 中添加：
- `export_traces(start, end, format, user_id)` — 从 Langfuse API 分页拉取 Trace
- JSON 格式：返回结构化 JSON 文件
- CSV 格式：使用 Python `csv` 模块生成扁平化 CSV
- 数据量上限：10000 条，超出返回错误提示

#### Task 5.2: 创建导出端点

**修改文件**: `app/api/observability.py`

```python
@router.get("/traces/export")
async def export_traces(
    startDate: str,
    endDate: str,
    format: Literal["json", "csv"],
    userId: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    # 权限控制：普通用户只能导出自己的
    # 返回 StreamingResponse with Content-Disposition
```

**验收**:
- JSON 导出文件可被标准解析器解析
- CSV 导出文件可被 Excel 打开
- 管理员可导出所有用户数据
- 普通用户只能导出自己的数据
- 超过 10000 条时返回错误提示

---

### Phase 6: 测试数据集与评估（P2）

> 目标：支持通过 Langfuse UI/SDK 管理测试数据集和运行评估

#### Task 6.1: 编写评估脚本模板

**新建文件**: `scripts/langfuse_eval.py`

提供评估脚本示例：
```python
"""
Langfuse Experiment 评估脚本模板
- 从 Langfuse Dataset 读取测试用例
- 调用 SunnyAgent /api/chat 接口
- 使用 LLM-as-a-Judge 评估结果
"""
```

此任务主要是文档和示例，实际的 Dataset 管理使用 Langfuse 原生 UI/SDK。

**验收**:
- 评估脚本可运行并产生 Experiment 结果
- Langfuse UI 中可查看评估得分

---

### Phase 7: 前端可观测性 Tab（P1，sunny-agent-web 仓库）

> 目标：在管理面板中实现可观测性 Tab，展示 Langfuse 状态、服务管理、用量统计和数据导出
>
> **仓库**: `sunny-agent-web`（独立前端仓库）
> **前置**: 后端 Phase 3 + Phase 4 API 就绪

#### Task 7.1: 创建可观测性 API 模块

**新建文件**: `src/api/observability/index.ts`

封装后端 14 个 observability API 调用：
- `getStatus()` / `getConsoleUrl()` / `getConfig()` / `updateConfig()`
- `validateConnection()` / `initializeProject()`
- `startBuiltinService()` / `stopBuiltinService()` / `getBuiltinStatus()`
- `getUsageSummary()` / `getUsageDaily()` / `getUsageByUser()` / `refreshUsage()`
- `exportTraces()`

**新建文件**: `src/api/observability/types.ts`

定义 TypeScript 类型（LangfuseStatus, UsageSummary, DailyUsage, UserUsage 等），对应 api-spec.md 第三节数据模型。

#### Task 7.2: 扩展管理面板侧边栏

**修改文件**: `src/components/admin-manage/admin-sidebar/index.vue`

在现有 3 个 tab（USER / SYSTEM / SCHEDULED_TASK）基础上新增第 4 个 tab：
- Label: "可观测性" 或 "Observability"
- Icon: 使用 `lucide-vue-next` 中合适的图标（如 `Activity`）
- 条件显示：仅管理员可见

#### Task 7.3: 实现可观测性页面主框架

**新建文件**: `src/components/admin-manage/observability/index.vue`

页面分为 3 个区域（可用 Element Plus `el-tabs` 或卡片布局）：
1. **Langfuse 状态卡片** — 服务状态指示器、版本、跳转按钮
2. **服务管理区域** — 内置/外部服务配置（仅管理员）
3. **Token 用量统计区域** — 日期选择器 + 统计卡片 + 趋势图

#### Task 7.4: 实现 Langfuse 状态与服务管理组件

**新建文件**: `src/components/admin-manage/observability/langfuse-status.vue`

- 状态指示器（绿色/红色/灰色圆点 + 文字）
- "打开 Langfuse 控制台"按钮（调用 `getConsoleUrl()`，新标签页打开）
- 服务模式切换（内置 / 外部 / 未配置）
- 内置服务：启动/停止按钮，组件状态列表
- 外部服务：URL 输入 + 验证按钮 + API Key 配置表单

#### Task 7.5: 实现 Token 用量统计组件

**新建文件**: `src/components/admin-manage/observability/usage-stats.vue`

- 日期范围选择器（`el-date-picker` type="daterange"，默认今天）
- 汇总卡片：总调用次数、总 Token 数（输入/输出）、预估费用
- 按日趋势柱状图（可用 Element Plus 自带或简单 CSS 柱状图，避免引入 echarts）
- 管理员：用户筛选下拉 + 用户分布表格
- 普通用户：仅显示个人数据
- 刷新按钮
- 数据导出按钮（选择 JSON/CSV 格式，触发文件下载）

#### Task 7.6: 连接 Pinia Store（可选）

**新建文件**: `src/store/observability.ts`

如需跨组件共享状态（如 Langfuse 状态缓存），创建 Pinia store。轻量实现可直接在组件中管理状态。

**验收**:
- 管理面板新增可观测性 Tab
- 管理员可在 UI 上启停内置服务、配置外部服务
- 用量统计区域正确展示日期范围内的数据和趋势图
- 管理员可看全部用户数据并筛选，普通用户只看自己
- 导出按钮触发文件下载
- "打开控制台"按钮在新标签页跳转 Langfuse

---

## 文件变更清单

### 后端新建文件 (sunny_agent)
| 文件 | Phase | 说明 |
|------|-------|------|
| `infra/langfuse-compose.yml` | 1 | Langfuse 服务 Docker Compose |
| `app/db/models/langfuse_config.py` | 1 | 数据库模型 |
| `app/db/migrations/versions/xxx_add_langfuse_config.py` | 1 | 数据库迁移 |
| `app/utils/crypto.py` | 1 | Fernet 加密工具（encrypt_secret / decrypt_secret） |
| `app/services/langfuse_config_loader.py` | 1 | 配置加载逻辑（DB 优先，.env 回退） |
| `app/observability/langfuse_client.py` | 2 | Langfuse SDK 客户端 |
| `app/observability/pii_filter.py` | 2 | Trace 数据 PII 脱敏层 |
| `app/services/observability.py` | 3 | 可观测性业务逻辑 |
| `app/api/observability.py` | 3 | API 路由 |
| `app/services/langfuse_manager.py` | 4 | 内置服务管理 |
| `scripts/langfuse_eval.py` | 6 | 评估脚本模板 |

### 后端修改文件 (sunny_agent)
| 文件 | Phase | 变更 |
|------|-------|------|
| `app/config.py` | 1 | 新增 LANGFUSE_*、ENCRYPTION_KEY、LANGFUSE_PII_PATTERNS 配置项 |
| `app/db/models/__init__.py` | 1 | 注册 LangfuseConfig import |
| `pyproject.toml` | 1 | 添加 langfuse、cryptography 依赖 |
| `app/main.py` | 1, 2, 3 | 配置加载 + LiteLLM callback + 路由注册 + shutdown |
| `app/observability/context.py` | 2 | 新增 langfuse_trace_var |
| `app/api/chat.py` | 2 | 创建顶层 Trace |
| `app/execution/l3/react_engine.py` | 2 | react_loop Span |
| `app/execution/l3/thinker.py` | 2 | think Span |
| `app/execution/l3/actor.py` | 2 | tool_call Span |
| `app/cache/redis_client.py` | 3 | 新增 Langfuse 缓存 key |

### 前端新建文件 (sunny-agent-web)
| 文件 | Phase | 说明 |
|------|-------|------|
| `src/api/observability/index.ts` | 7 | API 调用模块 |
| `src/api/observability/types.ts` | 7 | TypeScript 类型定义 |
| `src/components/admin-manage/observability/index.vue` | 7 | 可观测性页面主框架 |
| `src/components/admin-manage/observability/langfuse-status.vue` | 7 | 状态与服务管理组件 |
| `src/components/admin-manage/observability/usage-stats.vue` | 7 | 用量统计与导出组件 |
| `src/store/observability.ts` | 7 | Pinia store（可选） |

### 前端修改文件 (sunny-agent-web)
| 文件 | Phase | 变更 |
|------|-------|------|
| `src/components/admin-manage/admin-sidebar/index.vue` | 7 | 新增 OBSERVABILITY tab |
| `src/components/admin-manage/index.vue` | 7 | 路由到可观测性组件 |

---

## 依赖关系

```
Phase 1 (基础设施 + 加密工具 + 配置加载)           [后端]
    │
    ▼
Phase 1.5 (技术 Spike)                             [后端]
    │
    ▼
Phase 2 (Trace 集成 + PII 脱敏) ───────┐           [后端]
    │                                   │
    ▼                                   ▼
Phase 3 (可观测性 API)          Phase 4 (服务管理 API)  [后端]
    │                                   │
    ▼                                   │
Phase 5 (Trace 导出) ◀─────────────────┘           [后端]
    │
    ▼
Phase 6 (测试评估)                                  [后端]

Phase 7 (前端可观测性 Tab)                          [前端 sunny-agent-web]
    ▲ 依赖 Phase 3 + Phase 4 后端 API 就绪
```

Phase 1 → Phase 1.5 → Phase 2 严格顺序。Phase 3 和 Phase 4 可并行。Phase 5 依赖 Phase 3。Phase 6 可独立进行。**Phase 7 (前端)** 依赖后端 API 就绪（Phase 3 + 4），可与 Phase 5/6 并行。

---

## 风险检查项

| 风险 | 缓解 | 状态 |
|------|------|------|
| LiteLLM Langfuse callback 与 async generator 兼容性 | **Phase 1.5 Spike 1** 验证，不兼容则改用手动 Generation 记录 | ⏳ Phase 1.5 验证 |
| Langfuse `LANGFUSE_INIT_*` 环境变量幂等性与 volume 丢失场景 | **Phase 1.5 Spike 2** 验证，增加 Key 有效性校验和自动重新初始化 | ⏳ Phase 1.5 验证 |
| Langfuse v3 `/api/public/metrics/*` 端点可用性 | **Phase 1.5 Spike 3** 验证，不可用则改用 `/api/public/traces` 分页拉取 + 后端聚合 | ⏳ Phase 1.5 验证 |
| PII 数据泄露到 Trace（NFR-008） | Phase 2 Task 2.7 实现 `pii_filter.py` 脱敏层 | ✅ 已设计 |
| Secret Key 明文存储 | Phase 1 Task 1.6 实现 Fernet 加密，`ENCRYPTION_KEY` 环境变量管理 | ✅ 已设计 |
| 配置双重存储（DB + .env）一致性 | Phase 1 Task 1.7 明确 DB 为 source of truth，.env 仅为种子值 | ✅ 已设计 |
| NextAuth 代理登录跨域 cookie 问题 | 实现降级方案（手动登录跳转） | 已设计 |
| Docker compose CLI 权限问题（SunnyAgent 进程需 docker 权限） | 文档说明：运行 SunnyAgent 的用户需在 docker 组中 | 已识别 |
| Langfuse Public API 分页性能（大量 Trace 导出） | 限制单次导出 10000 条 | 已设计 |
