# Langfuse 集成 API 接口规范

**版本**: 1.0.0
**更新日期**: 2026-03-06
**架构**: 前后端分离

## 概述

本文档定义了 SunnyAgent 与 Langfuse 集成的所有 API 接口，包括：

1. **SunnyAgent 后端 API** - 前端 UI 调用的接口
2. **Langfuse API** - SunnyAgent 后端调用的外部接口

## 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                           SunnyAgent 系统                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   ┌─────────────┐                      ┌─────────────────────┐     │
│   │   前端 UI   │  ── HTTP/JSON ──▶   │  SunnyAgent 后端    │     │
│   │(sunny-agent │  ◀── Response ───   │    (FastAPI)        │     │
│   │   -web)     │                      │  (sunny_agent)      │     │
│   └─────────────┘                      └──────────┬──────────┘     │
│                                                   │                 │
│                                                   │ HTTP/JSON       │
│                                                   ▼                 │
│                                        ┌─────────────────────┐     │
│                                        │    Langfuse SDK     │     │
│                                        │  (langfuse-python)  │     │
│                                        └──────────┬──────────┘     │
│                                                   │                 │
└───────────────────────────────────────────────────┼─────────────────┘
                                                    │
                                                    ▼
                                         ┌─────────────────────┐
                                         │   Langfuse Server   │
                                         │   (Self-hosted)     │
                                         └─────────────────────┘
```

---

## 一、SunnyAgent 后端 API

> Base URL: `/api/v1/observability`

### 通用响应格式

**成功响应**:
```json
{
  "code": 0,
  "message": "success",
  "data": { ... }
}
```

**错误响应**:
```json
{
  "code": 40000,
  "message": "错误描述",
  "data": null
}
```

### 错误码定义

> 遵循现有 codebase 约定：`code = http_status * 100`，子错误码在基础码上 +1/+2

| 错误码 | 说明 |
|--------|------|
| 0 | 成功 |
| 40000 | 参数错误 |
| 40100 | 未登录 |
| 40300 | 权限不足 |
| 50000 | Langfuse 服务不可用 |
| 50001 | Langfuse 未初始化（`50000` 子码） |

---

### 1.1 获取 Langfuse 状态

检查 Langfuse 服务运行状态和初始化状态。

```http
GET /api/v1/observability/status
```

**权限**: 已登录用户

**请求头**:
```
Authorization: Bearer <token>
```

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "status": "healthy",
    "initialized": true,
    "langfuseUrl": "https://langfuse.example.com",
    "version": "3.63.0",
    "lastCheckAt": "2026-03-06T10:30:00Z",
    "statusText": "运行正常"
  }
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 服务状态: `healthy`, `unhealthy`, `unknown` |
| initialized | boolean | 是否已完成自动初始化 |
| langfuseUrl | string | Langfuse 服务地址 |
| version | string | Langfuse 服务版本 |
| lastCheckAt | string | 最后检查时间 (ISO 8601) |
| statusText | string | 状态描述文本 |

---

### 1.2 获取控制台跳转链接

获取带认证信息的 Langfuse 控制台 URL，用于免登录跳转。

```http
GET /api/v1/observability/console-url
```

**权限**: 仅管理员（FR-035：仅对管理员显示控制台入口）

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "url": "https://langfuse.example.com/project/proj_xxx?auth_token=yyy",
    "expiresAt": "2026-03-06T11:30:00Z",
    "expiresIn": 3600
  }
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| url | string | 带认证的跳转 URL |
| expiresAt | string | 链接过期时间 |
| expiresIn | number | 有效期（秒） |

---

### 1.3 获取 Langfuse 配置

获取当前 Langfuse 服务配置信息。

```http
GET /api/v1/observability/config
```

**权限**: 管理员

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "serviceMode": "builtin",
    "builtinService": {
      "enabled": true,
      "status": "running",
      "url": "http://localhost:3000"
    },
    "externalService": {
      "url": "",
      "configured": false
    },
    "langfuseUrl": "http://localhost:3000",
    "publicKey": "pk-lf-xxx",
    "secretKeyConfigured": true,
    "projectInitialized": true,
    "initializedAt": "2026-03-06T08:00:00Z"
  }
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| serviceMode | string | 服务模式: `builtin`（内置）, `external`（外部）, `none`（未配置） |
| builtinService | object | 内置服务状态 |
| builtinService.enabled | boolean | 内置服务是否启用 |
| builtinService.status | string | 内置服务状态: `running`, `stopped`, `starting`, `error` |
| builtinService.url | string | 内置服务访问地址 |
| externalService | object | 外部服务配置 |
| externalService.url | string | 外部服务地址 |
| externalService.configured | boolean | 外部服务是否已配置 |
| langfuseUrl | string | 当前使用的 Langfuse 服务地址 |
| publicKey | string | Public Key（部分脱敏显示） |
| secretKeyConfigured | boolean | Secret Key 是否已配置 |
| projectInitialized | boolean | 项目是否已初始化 |
| initializedAt | string | 初始化时间 |

---

### 1.4 启动内置 Langfuse 服务

启动内置的 Langfuse v3 服务（含完整依赖栈）。

```http
POST /api/v1/observability/builtin-service/start
```

**权限**: 管理员

**请求体**: 无

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "success": true,
    "status": "starting",
    "url": "http://localhost:3000",
    "message": "内置服务正在启动，预计需要 30-60 秒"
  }
}
```

---

### 1.5 停止内置 Langfuse 服务

停止内置的 Langfuse 服务及相关组件。

```http
POST /api/v1/observability/builtin-service/stop
```

**权限**: 管理员

**请求体**: 无

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "success": true,
    "status": "stopped",
    "message": "内置服务已停止"
  }
}
```

---

### 1.6 获取内置服务状态

获取内置 Langfuse 服务的详细状态。

```http
GET /api/v1/observability/builtin-service/status
```

**权限**: 管理员

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "enabled": true,
    "status": "running",
    "url": "http://localhost:3000",
    "version": "3.63.0",
    "components": {
      "langfuse": "running",
      "clickhouse": "running",
      "redis": "running",
      "minio": "running",
      "postgres": "running"
    },
    "startedAt": "2026-03-06T08:00:00Z",
    "uptime": 3600
  }
}
```

---

### 1.7 更新 Langfuse 配置

更新 Langfuse 服务配置（服务模式、URL 和 API Keys）。

```http
PUT /api/v1/observability/config
```

**权限**: 管理员

**请求体**:

```json
{
  "langfuseUrl": "https://langfuse.example.com",
  "publicKey": "pk-lf-xxx",
  "secretKey": "sk-lf-xxx"
}
```

**请求参数说明**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| langfuseUrl | string | 是 | Langfuse 服务地址 |
| publicKey | string | 否 | Public Key（不传则保持原值） |
| secretKey | string | 否 | Secret Key（不传则保持原值） |

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "success": true,
    "langfuseUrl": "https://langfuse.example.com",
    "connectionValid": true,
    "message": "配置更新成功，连接验证通过"
  }
}
```

**错误响应**:

```json
{
  "code": 50000,
  "message": "Langfuse 服务连接失败",
  "data": {
    "success": false,
    "langfuseUrl": "https://langfuse.example.com",
    "connectionValid": false,
    "message": "无法连接到指定的 Langfuse 服务，请检查 URL 是否正确"
  }
}
```

---

### 1.8 验证 Langfuse 连接

验证指定的 Langfuse URL 是否可连接（不保存配置）。

```http
POST /api/v1/observability/config/validate
```

**权限**: 管理员

**请求体**:

```json
{
  "langfuseUrl": "https://langfuse.example.com"
}
```

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "valid": true,
    "version": "3.63.0",
    "latency": 125,
    "message": "连接成功"
  }
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| valid | boolean | 连接是否有效 |
| version | string | Langfuse 服务版本（连接成功时返回） |
| latency | number | 连接延迟（毫秒） |
| message | string | 验证结果描述 |

---

### 1.9 初始化 Langfuse 项目

手动触发内置 Langfuse 服务的项目初始化（通常由 `LANGFUSE_INIT_*` 环境变量自动完成，此端点用于初始化失败时的手动重试）。外部服务场景下，管理员应通过 `PUT /config` 手动提供已有的 API Key，不调用此端点。

```http
POST /api/v1/observability/config/initialize
```

**权限**: 管理员

**请求体**: 无（使用已配置的 Langfuse 服务地址，仅适用于内置服务模式）

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "success": true,
    "projectId": "proj_xxx",
    "projectName": "SunnyAgent",
    "publicKey": "pk-lf-xxx",
    "secretKeyConfigured": true,
    "initializedAt": "2026-03-06T10:00:00Z",
    "message": "项目初始化成功"
  }
}
```

**错误响应**:

```json
{
  "code": 50000,
  "message": "初始化失败",
  "data": {
    "success": false,
    "error": "LANGFUSE_NOT_CONNECTED",
    "message": "请先配置并验证 Langfuse 服务地址"
  }
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| success | boolean | 初始化是否成功 |
| projectId | string | 创建的项目 ID |
| projectName | string | 项目名称 |
| publicKey | string | 生成的 Public Key |
| secretKeyConfigured | boolean | Secret Key 是否已配置 |
| initializedAt | string | 初始化时间 |

---

### 1.10 获取用量汇总统计

获取指定时间范围内的 Token 用量汇总。

```http
GET /api/v1/observability/usage/summary
```

**权限**:
- 管理员: 可查看所有用户或指定用户
- 普通用户: 仅查看自己的数据

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| startDate | string | 是 | - | 起始日期 (YYYY-MM-DD) |
| endDate | string | 是 | - | 结束日期 (YYYY-MM-DD) |
| userId | string | 否 | 当前用户 | 用户ID（仅管理员可指定） |

**请求示例**:
```http
GET /api/v1/observability/usage/summary?startDate=2026-03-01&endDate=2026-03-06
```

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "totalCalls": 27,
    "totalTokens": 305300,
    "inputTokens": 295800,
    "outputTokens": 9500,
    "estimatedCost": 0.83,
    "currency": "USD",
    "costBreakdown": {
      "inputCost": 0.59,
      "outputCost": 0.24
    },
    "period": {
      "startDate": "2026-03-01",
      "endDate": "2026-03-06",
      "days": 6
    }
  }
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| totalCalls | number | 总调用次数 |
| totalTokens | number | 总 Token 数 |
| inputTokens | number | 输入 Token 数 |
| outputTokens | number | 输出 Token 数 |
| estimatedCost | number | 预估费用 |
| currency | string | 货币单位 |
| costBreakdown | object | 费用明细 |
| period | object | 查询时间段信息 |

---

### 1.11 获取用量趋势（按日）

获取按日维度的用量趋势数据，用于绘制趋势图。

```http
GET /api/v1/observability/usage/daily
```

**权限**:
- 管理员: 可查看所有用户或指定用户
- 普通用户: 仅查看自己的数据

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| startDate | string | 是 | - | 起始日期 (YYYY-MM-DD) |
| endDate | string | 是 | - | 结束日期 (YYYY-MM-DD) |
| userId | string | 否 | 当前用户 | 用户ID（仅管理员可指定） |

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "date": "2026-03-04",
        "totalCalls": 10,
        "totalTokens": 120000,
        "inputTokens": 115000,
        "outputTokens": 5000,
        "estimatedCost": 0.32
      },
      {
        "date": "2026-03-05",
        "totalCalls": 17,
        "totalTokens": 185300,
        "inputTokens": 180800,
        "outputTokens": 4500,
        "estimatedCost": 0.51
      }
    ],
    "summary": {
      "totalCalls": 27,
      "totalTokens": 305300,
      "estimatedCost": 0.83
    }
  }
}
```

---

### 1.12 获取用户分布统计

获取按用户维度的用量分布，用于管理员查看所有用户的使用情况。

```http
GET /api/v1/observability/usage/by-user
```

**权限**: 仅管理员

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| startDate | string | 是 | - | 起始日期 (YYYY-MM-DD) |
| endDate | string | 是 | - | 结束日期 (YYYY-MM-DD) |
| sortBy | string | 否 | totalTokens | 排序字段: `totalTokens`, `totalCalls`, `estimatedCost` |
| sortOrder | string | 否 | desc | 排序方向: `asc`, `desc` |
| limit | number | 否 | 50 | 返回数量限制 |
| offset | number | 否 | 0 | 分页偏移 |

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "userId": "user_admin",
        "userName": "管理员",
        "email": "admin@example.com",
        "totalCalls": 27,
        "totalTokens": 305300,
        "inputTokens": 295800,
        "outputTokens": 9500,
        "estimatedCost": 0.83,
        "lastActiveAt": "2026-03-06T09:15:00Z"
      },
      {
        "userId": "user_001",
        "userName": "张三",
        "email": "zhangsan@example.com",
        "totalCalls": 12,
        "totalTokens": 128500,
        "inputTokens": 120000,
        "outputTokens": 8500,
        "estimatedCost": 0.35,
        "lastActiveAt": "2026-03-05T16:30:00Z"
      }
    ],
    "pagination": {
      "total": 15,
      "limit": 50,
      "offset": 0,
      "hasMore": false
    }
  }
}
```

---

### 1.13 刷新用量数据

手动触发用量数据刷新，从 Langfuse 同步最新数据。

```http
POST /api/v1/observability/usage/refresh
```

**权限**: 管理员

**请求体**: 无

**响应**:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "success": true,
    "lastSyncAt": "2026-03-06T10:35:00Z",
    "syncDuration": 1250,
    "recordsUpdated": 156
  }
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| success | boolean | 刷新是否成功 |
| lastSyncAt | string | 最后同步时间 |
| syncDuration | number | 同步耗时（毫秒） |
| recordsUpdated | number | 更新的记录数 |

---

### 1.14 导出 Trace 数据

导出指定时间范围内的 Trace 数据为 JSON 或 CSV 格式。

```http
GET /api/v1/observability/traces/export
```

**权限**:
- 管理员: 可导出所有用户或指定用户的数据
- 普通用户: 仅导出自己的数据

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| startDate | string | 是 | - | 起始日期 (YYYY-MM-DD) |
| endDate | string | 是 | - | 结束日期 (YYYY-MM-DD) |
| format | string | 是 | - | 导出格式: `json`, `csv` |
| userId | string | 否 | 当前用户 | 用户ID（仅管理员可指定，传 `all` 导出所有用户） |

**请求示例**:
```http
GET /api/v1/observability/traces/export?startDate=2026-03-01&endDate=2026-03-06&format=json
```

**响应**:

- Content-Type: `application/json` 或 `text/csv`
- Content-Disposition: `attachment; filename="traces_2026-03-01_2026-03-06.json"`

**JSON 格式响应示例**:
```json
{
  "exportedAt": "2026-03-06T10:30:00Z",
  "period": {
    "startDate": "2026-03-01",
    "endDate": "2026-03-06"
  },
  "totalCount": 27,
  "traces": [
    {
      "traceId": "trace_xxx",
      "userId": "user_001",
      "sessionId": "session_xxx",
      "name": "agent_execution",
      "startTime": "2026-03-05T09:15:00Z",
      "endTime": "2026-03-05T09:15:03Z",
      "duration": 3000,
      "status": "success",
      "inputTokens": 1500,
      "outputTokens": 200,
      "totalTokens": 1700,
      "estimatedCost": 0.0046,
      "metadata": {}
    }
  ]
}
```

**CSV 格式响应示例**:
```csv
trace_id,user_id,session_id,name,start_time,end_time,duration_ms,status,input_tokens,output_tokens,total_tokens,estimated_cost
trace_xxx,user_001,session_xxx,agent_execution,2026-03-05T09:15:00Z,2026-03-05T09:15:03Z,3000,success,1500,200,1700,0.0046
```

**错误响应**:

无数据时返回:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "totalCount": 0,
    "traces": [],
    "warning": "指定时间范围内无 Trace 数据"
  }
}
```

数据量超限时返回:
```json
{
  "code": 40000,
  "message": "导出数据量超过限制",
  "data": {
    "requestedCount": 15000,
    "maxLimit": 10000,
    "suggestion": "请缩小时间范围或分批导出"
  }
}
```

---

## 二、Langfuse API（后端调用）

> SunnyAgent 后端调用 Langfuse Server 的 API

### 2.1 健康检查

```http
GET {LANGFUSE_HOST}/api/public/health
```

**请求头**: 无需认证（公开端点）

**响应**:
```json
{
  "status": "OK",
  "version": "3.63.0"
}
```

---

### 2.2 获取每日用量指标

> ⚠️ **待验证**：此端点在 Langfuse v3 中的可用性需在 Phase 1.5 Spike 3 中确认。如不可用，改用 `GET /api/public/traces` 分页拉取后在 SunnyAgent 后端聚合。

```http
GET {LANGFUSE_HOST}/api/public/metrics/daily
```

**请求参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| traceName | string | Trace 名称过滤 |
| userId | string | 用户 ID 过滤 |
| fromTimestamp | string | 起始时间 (ISO 8601) |
| toTimestamp | string | 结束时间 (ISO 8601) |

**响应**:
```json
{
  "data": [
    {
      "date": "2026-03-04",
      "countTraces": 10,
      "countObservations": 45,
      "totalTokens": 120000,
      "inputTokens": 115000,
      "outputTokens": 5000,
      "totalCost": 0.32
    }
  ]
}
```

---

### 2.3 获取汇总用量

> ⚠️ **待验证**：同 2.2，此端点可用性需在 Phase 1.5 Spike 3 中确认。

```http
GET {LANGFUSE_HOST}/api/public/metrics/usage
```

**请求参数**: 同上

**响应**:
```json
{
  "totalTraces": 27,
  "totalObservations": 135,
  "totalTokens": 305300,
  "inputTokens": 295800,
  "outputTokens": 9500,
  "totalCost": 0.83
}
```

---

### 2.4 创建项目（自动初始化）

```http
POST {LANGFUSE_HOST}/api/v1/projects
```

**请求头**:
```
Authorization: Bearer <ADMIN_API_KEY>
Content-Type: application/json
```

**请求体**:
```json
{
  "name": "SunnyAgent",
  "organizationId": "org_xxx"
}
```

**响应**:
```json
{
  "id": "proj_xxx",
  "name": "SunnyAgent",
  "createdAt": "2026-03-06T08:00:00Z"
}
```

---

### 2.5 生成 API Key（自动初始化）

```http
POST {LANGFUSE_HOST}/api/v1/projects/{projectId}/api-keys
```

**请求体**:
```json
{
  "note": "SunnyAgent Auto Generated"
}
```

**响应**:
```json
{
  "id": "key_xxx",
  "publicKey": "pk-lf-xxx",
  "secretKey": "sk-lf-xxx",
  "createdAt": "2026-03-06T08:00:00Z"
}
```

---

## 三、数据模型

### UsageSummary

```typescript
interface UsageSummary {
  totalCalls: number;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCost: number;
  currency: string;
}
```

### DailyUsage

```typescript
interface DailyUsage {
  date: string;          // YYYY-MM-DD
  totalCalls: number;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCost: number;
}
```

### UserUsage

```typescript
interface UserUsage {
  userId: string;
  userName: string;
  email?: string;
  totalCalls: number;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCost: number;
  lastActiveAt: string;
}
```

### LangfuseStatus

```typescript
interface LangfuseStatus {
  status: 'healthy' | 'unhealthy' | 'unknown';
  initialized: boolean;
  langfuseUrl: string;
  version?: string;
  lastCheckAt: string;
  statusText: string;
}
```

### TraceExportItem

```typescript
interface TraceExportItem {
  traceId: string;
  userId: string;
  sessionId: string;
  name: string;
  startTime: string;       // ISO 8601
  endTime: string;         // ISO 8601
  duration: number;        // 毫秒
  status: 'success' | 'error';
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  estimatedCost: number;
  metadata?: Record<string, any>;
}
```

### TraceExportResponse

```typescript
interface TraceExportResponse {
  exportedAt: string;      // ISO 8601
  period: {
    startDate: string;     // YYYY-MM-DD
    endDate: string;       // YYYY-MM-DD
  };
  totalCount: number;
  traces: TraceExportItem[];
  warning?: string;
}
```

---

## 四、配置项

### 环境变量

| 变量名 | 必填 | 说明 |
|--------|------|------|
| LANGFUSE_HOST | 是 | Langfuse 服务地址 |
| LANGFUSE_PUBLIC_KEY | 是 | Langfuse Public Key（内置服务由 docker-compose 预配置生成） |
| LANGFUSE_SECRET_KEY | 是 | Langfuse Secret Key（内置服务由 docker-compose 预配置生成） |
| LANGFUSE_ADMIN_EMAIL | 是 | Langfuse 管理员邮箱（内置服务同时用于 `LANGFUSE_INIT_USER_EMAIL`） |
| LANGFUSE_ADMIN_PASSWORD | 是 | Langfuse 管理员密码（内置服务同时用于 `LANGFUSE_INIT_USER_PASSWORD`） |

> 内置服务场景下，以上配置同时作为 docker-compose 的 `LANGFUSE_INIT_*` 环境变量，Langfuse 启动时自动创建组织、项目、管理员账号和 API Key

---

## 五、费用计算规则

费用直接使用 Langfuse 原生的 `totalCost` 字段聚合。Langfuse v3 内置模型价格表，在 Trace 记录时按实际使用的模型自动计算费用，SunnyAgent 无需自行维护价格配置。

> 如 Langfuse 未配置某模型价格，`totalCost` 可能为 0，此时前端应显示"费用不可用"提示。
