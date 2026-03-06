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
│   │  (Vue.js)   │  ◀── Response ───   │    (FastAPI)        │     │
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
  "code": 40001,
  "message": "错误描述",
  "data": null
}
```

### 错误码定义

| 错误码 | 说明 |
|--------|------|
| 0 | 成功 |
| 40001 | 参数错误 |
| 40101 | 未登录 |
| 40301 | 权限不足 |
| 50001 | Langfuse 服务不可用 |
| 50002 | Langfuse 未初始化 |

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

**权限**: 已登录用户

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

### 1.3 获取用量汇总统计

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

### 1.4 获取用量趋势（按日）

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

### 1.5 获取用户分布统计

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

### 1.6 刷新用量数据

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

## 二、Langfuse API（后端调用）

> SunnyAgent 后端调用 Langfuse Server 的 API

### 2.1 健康检查

```http
GET {LANGFUSE_HOST}/api/public/health
```

**请求头**:
```
Authorization: Basic base64(LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY)
```

**响应**:
```json
{
  "status": "OK",
  "version": "3.63.0"
}
```

---

### 2.2 获取每日用量指标

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

---

## 四、配置项

### 环境变量

| 变量名 | 必填 | 说明 |
|--------|------|------|
| LANGFUSE_HOST | 是 | Langfuse 服务地址 |
| LANGFUSE_PUBLIC_KEY | 是* | Langfuse Public Key（自动初始化后生成） |
| LANGFUSE_SECRET_KEY | 是* | Langfuse Secret Key（自动初始化后生成） |
| LANGFUSE_ADMIN_API_KEY | 是 | 管理员 API Key（用于自动初始化） |
| LANGFUSE_ORG_ID | 是 | 组织 ID |

> *注: 首次启动时由系统自动生成并存储到数据库

---

## 五、费用计算规则

Token 费用预估基于以下默认价格（可配置）：

| 类型 | 价格 (USD / 1M tokens) |
|------|------------------------|
| 输入 Token | $2.00 |
| 输出 Token | $8.00 |

计算公式：
```
estimatedCost = (inputTokens * inputPrice + outputTokens * outputPrice) / 1_000_000
```
