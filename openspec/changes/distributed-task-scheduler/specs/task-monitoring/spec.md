# Capability: 任务调度监控

## 需求

提供全面的监控和告警能力，确保任务调度系统的健康状态可观测，问题可及时发现和处理。

### 功能性需求

#### REQ-1: Prometheus 指标暴露
- 所有服务暴露 Prometheus 指标端点
- Scheduler 指标：
  - `tasks_scheduled_total`：已调度任务数
  - `schedule_delay_seconds`：调度延迟分布
  - `outbox_pending_total`：待处理 Outbox 消息数
  - `outbox_failed_total`：发送失败的 Outbox 消息数
  
- Worker 指标：
  - `worker_health`：Worker 健康状态
  - `worker_load`：当前负载（执行任务数）
  - `task_execution_total`：任务执行总数（按状态）
  - `task_execution_duration_seconds`：执行耗时分布
  - `idempotency_lock_acquired_total`：幂等性锁获取成功数
  - `idempotency_conflict_total`：幂等性冲突数

- 队列指标：
  - `task_queue_depth`：队列深度
  - `task_queue_age_seconds`：任务在队列中等待时间

- 系统指标：
  - `stale_tasks_total`：僵死任务数量
  - `worker_heartbeat_age_seconds`：Worker 心跳年龄

#### REQ-2: 健康检查端点
- Scheduler：`/health` - 返回最后调度时间和 Outbox 积压状态
- Worker：`/health` - 返回负载和心跳状态（使用 arq 内置健康检查）
- 支持 Kubernetes liveness/readiness probe

#### REQ-3: 日志规范
- 结构化日志（JSON 格式）
- 关键字段：
  - `execution_id`：执行 ID
  - `task_id`：任务 ID
  - `worker_id`：Worker ID
  - `event`：事件类型
  - `duration_ms`：操作耗时
- 日志级别：INFO（正常）、WARNING（异常）、ERROR（失败）

#### REQ-4: 告警规则

**P0 - 紧急**
- 所有 Worker 离线（> 1 分钟）
- Outbox 积压 > 1000（持续 5 分钟）
- 任务调度延迟 > 5 分钟（持续 2 分钟）

**P1 - 重要**
- 队列堆积 > 1000 个任务（持续 5 分钟）
- 僵死任务 > 0 个（持续 1 分钟）
- 任务失败率 > 10%（过去 5 分钟）
- Worker 心跳丢失（> 2 分钟）
- Outbox 发送失败率 > 5%

**P2 - 警告**
- 幂等性缓存命中率 < 90%
- 调度器无活动（> 5 分钟）
- Worker 负载过高（> 80% 持续 5 分钟）

#### REQ-5: Grafana Dashboard

**Dashboard 页面设计：**

第一行：概览卡片
- 队列深度（实时）
- Outbox 积压（实时）
- 今日成功数
- 今日失败数
- 平均执行耗时（过去 1 小时）

第二行：趋势图
- 任务执行速率（成功/失败/重试）- 24 小时
- 调度延迟趋势（P50/P95/P99）- 24 小时
- Outbox 处理速率

第三行：Worker 状态
- Worker 列表（健康状态、负载、最后心跳）
- Worker 执行中任务数

第四行：调度器状态
- 调度器实例列表
- 最后调度时间
- Outbox 积压趋势

第五行：告警面板
- 当前活跃告警列表
- 告警历史

### 非功能性需求

#### REQ-6: 性能影响
- 指标采集对性能影响 < 1%
- 日志异步写入，不阻塞业务逻辑
- 健康检查响应时间 < 100ms

#### REQ-7: 可配置性
- 告警阈值可配置
- 日志级别可配置
- 指标保留时间可配置（默认 15 天）
- Dashboard 可自定义

#### REQ-8: 集成
- 与现有 Prometheus 集成
- 与现有 Grafana 集成
- 支持企业微信/钉钉/Slack 告警通知（Webhook）

### 监控指标定义

```python
from prometheus_client import Counter, Histogram, Gauge

# Scheduler 指标
TASKS_SCHEDULED = Counter(
    'tasks_scheduled_total',
    'Total number of tasks scheduled',
    ['status']  # success, failed
)

SCHEDULE_DELAY = Histogram(
    'task_schedule_delay_seconds',
    'Delay between scheduled time and actual execution',
    buckets=[1, 5, 10, 30, 60, 120, 300, 600]
)

OUTBOX_PENDING = Gauge(
    'outbox_pending_total',
    'Number of pending outbox messages'
)

OUTBOX_FAILED = Counter(
    'outbox_failed_total',
    'Number of failed outbox sends'
)

# Worker 指标
WORKER_HEALTH = Gauge(
    'worker_health',
    'Worker health status (1=healthy, 0=unhealthy)',
    ['worker_id']
)

WORKER_LOAD = Gauge(
    'worker_load',
    'Number of tasks currently being processed',
    ['worker_id']
)

TASK_EXECUTION_DURATION = Histogram(
    'task_execution_duration_seconds',
    'Task execution duration',
    buckets=[1, 5, 10, 30, 60, 120, 300, 600]
)

TASK_EXECUTION_TOTAL = Counter(
    'task_execution_total',
    'Total task executions',
    ['status']  # success, failed, retry
)

IDEMPOTENCY_LOCK_ACQUIRED = Counter(
    'idempotency_lock_acquired_total',
    'Number of successful idempotency lock acquisitions'
)

IDEMPOTENCY_CONFLICT = Counter(
    'idempotency_conflict_total',
    'Number of idempotency conflicts'
)

# 队列指标
QUEUE_DEPTH = Gauge(
    'task_queue_depth',
    'Number of tasks in queue',
    ['queue_name']  # default, high_priority
)

# 系统指标
STALE_TASKS = Gauge(
    'stale_tasks_total',
    'Number of stale tasks detected'
)

WORKER_HEARTBEAT_AGE = Gauge(
    'worker_heartbeat_age_seconds',
    'Time since last worker heartbeat',
    ['worker_id']
)
```

### 日志格式示例

```json
{
  "timestamp": "2024-01-15T09:00:00.123Z",
  "level": "INFO",
  "logger": "scheduler",
  "event": "task_scheduled",
  "message": "Task scheduled successfully",
  "execution_id": "018f3...",
  "task_id": "task-123",
  "duration_ms": 15
}

{
  "timestamp": "2024-01-15T09:00:05.456Z",
  "level": "ERROR",
  "logger": "worker",
  "event": "task_execution_failed",
  "message": "Task execution failed",
  "execution_id": "018f3...",
  "task_id": "task-123",
  "worker_id": "worker-01",
  "error_type": "TimeoutError",
  "error_message": "Chat API timeout",
  "retry_count": 1,
  "max_retries": 3
}

{
  "timestamp": "2024-01-15T09:00:10.789Z",
  "level": "INFO",
  "logger": "scheduler",
  "event": "outbox_message_sent",
  "message": "Outbox message sent to queue",
  "outbox_id": "outbox-456",
  "execution_id": "018f3..."
}
```
