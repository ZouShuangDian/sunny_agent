# Capability: 分布式调度器（基于 arq）

## 需求

实现基于 arq 框架的分布式调度服务，支持 Cron 任务调度和延迟任务，无需自建复杂的时间轮和分布式锁。

### 功能性需求

#### REQ-1: arq 集成
- 使用 arq 框架作为任务队列和调度引擎
- 使用 Redis 作为消息代理
- 使用 arq 内置的 cron 调度机制
- 无需分布式锁和 Leader 选举（arq 自动处理）

#### REQ-2: Cron 任务调度
- 使用 arq cron 每秒检查数据库中到期的任务
- 支持秒级精度的定时任务（10秒/30秒等间隔）
- 使用 croniter 计算下次执行时间
- 任务入队时计算并更新 next_run_at

#### REQ-3: 任务入队
- 使用 `enqueue_job()` 将任务推送到 arq 队列
- 支持延迟执行（`_defer_by` 参数）
- 支持不同队列（default/high_priority）
- arq 自动处理任务序列化和反序列化

#### REQ-4: 双写保障
- 创建 execution 记录（status='queued'）
- 更新 task 的 next_run_at
- PostgreSQL 事务保证原子性
- 事务成功后入队 arq 任务

#### REQ-5: 单实例部署
- Scheduler 只需单实例部署（arq 不依赖 Leader 选举）
- 支持优雅关闭（on_shutdown 回调）
- 支持健康检查端点

### 非功能性需求

#### REQ-6: 性能
- 每秒可扫描并调度 100+ 个任务
- 数据库连接池大小：10
- 调度延迟 < 100ms

#### REQ-7: 可观测性
- 暴露 arq 内置指标（队列深度、处理速率等）
- 记录调度日志（任务 ID、执行时间、下次执行时间）
- 集成 Prometheus 监控

### 核心算法

#### arq Cron 调度
```python
from arq import cron

# 每秒执行一次
cron_jobs = [
    cron(
        schedule_tasks,           # 调度函数
        second='*/1',            # 每秒执行
        run_at_startup=True      # 启动时立即执行一次
    )
]
```

#### 任务调度流程
```python
async def schedule_tasks(ctx):
    # 1. 查询到期的任务
    tasks = await db.execute(
        select(ScheduledTask)
        .where(ScheduledTask.next_run_at <= now())
        .limit(100)
    )
    
    for task in tasks:
        # 2. 创建执行记录
        execution = await create_execution(task.id)
        
        # 3. 计算下次执行时间
        next_run = croniter(task.cron_expression, now()).get_next(datetime)
        task.next_run_at = next_run
        await db.commit()
        
        # 4. 入队 arq 任务
        await ctx['redis'].enqueue_job(
            'execute_chat_task',
            execution_id=str(execution.id),
            user_id=str(task.created_by),
            parameters=task.parameters
        )
```

### arq 配置

```python
from arq import create_pool
from arq.connections import RedisSettings

redis_settings = RedisSettings(
    host='localhost',
    port=6379,
    database=0
)

class Scheduler:
    functions = [schedule_tasks]
    cron_jobs = [
        cron(schedule_tasks, second='*/1', run_at_startup=True)
    ]
    redis_settings = redis_settings
    
    async def on_startup(self, ctx):
        ctx['redis'] = await create_pool(redis_settings)
    
    async def on_shutdown(self, ctx):
        await ctx['redis'].close()
```

### 监控指标

```python
# arq 内置指标（通过 health check 端点暴露）
- arq:queue_depth          # 队列深度
- arq:jobs_completed       # 已完成任务数
- arq:jobs_failed          # 失败任务数
- arq:jobs_retried         # 重试任务数

# 自定义指标
SCHEDULED_TASKS_TOTAL = Counter('scheduled_tasks_total', ...)
SCHEDULE_DELAY = Histogram('schedule_delay_seconds', ...)
```

### 对比：自建 vs arq

| 特性 | 自建方案 | arq 方案 |
|------|----------|----------|
| 代码量 | ~1500 行 | ~200 行 |
| 分布式锁 | 需要 Redlock | 不需要 |
| Leader 选举 | 需要 | 不需要 |
| 时间轮 | 自建 60 槽位 | arq 内置 |
| Cron 精度 | 秒级（自建） | 秒级（内置） |
| 延迟队列 | 自建 Sorted Set | arq 内置 |
| 复杂度 | 高 | 低 |
