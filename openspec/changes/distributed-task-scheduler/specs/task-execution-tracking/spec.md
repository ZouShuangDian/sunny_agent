# Capability: 任务执行追踪

## 需求

提供完整的任务执行历史记录和状态追踪能力，支持审计、故障排查和性能分析。

### 功能性需求

#### REQ-1: 执行记录创建
- Scheduler 调度任务时创建 execution 记录
- 初始状态：`queued`
- 记录字段：
  - scheduled_task_id（关联任务定义）
  - scheduled_time（计划执行时间）
  - status（状态机）
  - retry_count（已重试次数）
  - worker_id（执行 Worker）
  - started_at（开始时间）
  - completed_at（完成时间）
  - result（执行结果，JSON）
  - error_message（错误信息）
  - idempotency_status（幂等性状态）

#### REQ-2: 状态流转
状态机：
```
queued → running → completed
   ↓        ↓         
retrying ← failed (最终状态)
```

- `queued`: 已入队，等待执行
- `running`: Worker 正在执行
- `completed`: 执行成功
- `failed`: 执行失败且重试次数用尽
- `retrying`: 失败但将重试

#### REQ-3: 执行历史查询
- 按任务 ID 查询执行历史
- 支持分页（默认 20 条/页）
- 支持按状态筛选
- 支持按时间范围筛选
- 返回执行耗时（completed_at - started_at）

#### REQ-4: 执行统计
- 任务成功率统计
- 平均执行耗时
- 失败原因分类统计
- 重试次数分布
- 最近 24 小时执行趋势

#### REQ-5: Webhook 通知
- 任务最终失败时触发 Webhook
- POST 请求，JSON 格式
- 包含：任务信息、执行记录、错误详情、重试次数
- 失败重试：2 次（共 3 次尝试）
- 重试间隔：30 秒、60 秒

#### REQ-6: 僵死任务标记
- Worker 失联超过 10 分钟的任务标记为僵死
- 自动重置为 `queued` 状态
- 记录僵死原因到 error_message
- 增加 retry_count

### 非功能性需求

#### REQ-7: 数据保留
- 执行记录保留 90 天
- 自动清理过期数据（每日凌晨执行）
- 清理前可导出到冷存储（可选）
- 保留统计摘要（聚合数据）

#### REQ-8: 性能
- 查询响应时间 < 100ms（单页）
- 支持索引优化
- 大数据量时分区表（未来扩展）

#### REQ-9: 审计
- 记录所有状态变更
- 包含时间戳和操作者
- 不可修改（只追加）
- 支持导出 CSV/JSON

### 数据模型

```sql
task_executions:
  - id: UUID PK
  - scheduled_task_id: UUID FK NOT NULL
  - status: VARCHAR(20) NOT NULL  -- queued/running/completed/failed
  - scheduled_time: TIMESTAMP NOT NULL
  - started_at: TIMESTAMP
  - completed_at: TIMESTAMP
  - worker_id: VARCHAR(100)
  - retry_count: INT DEFAULT 0
  - max_retries: INT DEFAULT 3
  - result: JSONB
  - error_message: TEXT
  - idempotency_status: VARCHAR(20)  -- processing/completed
  - idempotency_result: JSONB
  - webhook_attempts: INT DEFAULT 0
  - webhook_last_error: TEXT
  - created_at: TIMESTAMP DEFAULT NOW()
  - updated_at: TIMESTAMP DEFAULT NOW()

Indexes:
  - idx_task_executions_task_id (scheduled_task_id)
  - idx_task_executions_status (status)
  - idx_task_executions_scheduled_time (scheduled_time)
  - idx_task_executions_worker_status (worker_id, status)
```

### API 端点

```
GET /api/scheduler/tasks/{id}/executions     # 查询任务执行历史
GET /api/scheduler/executions/{id}           # 获取执行详情
GET /api/scheduler/executions                # 全局执行历史（管理员）
GET /api/scheduler/tasks/{id}/stats          # 任务执行统计
```

### Webhook Payload

```json
{
  "event": "task.failed",
  "timestamp": "2024-01-15T09:00:00Z",
  "task": {
    "id": "uuid",
    "name": "每日报表",
    "cron_expression": "0 9 * * *"
  },
  "execution": {
    "id": "uuid",
    "scheduled_time": "2024-01-15T09:00:00Z",
    "started_at": "2024-01-15T09:00:05Z",
    "failed_at": "2024-01-15T09:05:00Z",
    "retry_count": 3,
    "max_retries": 3
  },
  "error": {
    "type": "TimeoutError",
    "message": "Chat API timeout after 300s",
    "stack_trace": "..."
  }
}
```
