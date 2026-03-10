# 基于 arq 的定时任务调度设计

## 为什么引入 arq

### 当前方案的痛点

```
自建调度器的问题：
┌─────────────────────────────────────────────────────────────┐
│ 1. 复杂度高                                                  │
│    • 需要实现时间轮算法                                       │
│    • 需要管理分布式锁（Redlock）                              │
│    • 需要处理 Leader 选举                                     │
│    • 需要僵死任务检测                                         │
│                                                             │
│ 2. 维护成本高                                                │
│    • 自定义代码量大                                           │
│    • 边界情况处理复杂                                         │
│    • 测试覆盖困难                                             │
│                                                             │
│ 3. 功能受限                                                  │
│    • 秒级调度精度难保证                                       │
│    • 任务依赖、工作流难以实现                                 │
│    • 缺乏内置监控                                             │
└─────────────────────────────────────────────────────────────┘
```

### arq 的优势

```
arq 提供的开箱即用功能：
┌─────────────────────────────────────────────────────────────┐
│ ✓ 异步任务队列（基于 Redis）                                  │
│ ✓ Cron 任务调度（支持秒级）                                   │
│ ✓ 延迟任务（指定未来时间执行）                                 │
│ ✓ 自动重试（指数退避）                                        │
│ ✓ 任务依赖（job 依赖其他 job）                                │
│ ✓ 内置并发控制（max_jobs）                                    │
│ ✓ 优雅关闭（SIGTERM 处理）                                    │
│ ✓ 内置监控端点（health check）                                │
│ ✓ 类型安全（基于 Pydantic）                                   │
└─────────────────────────────────────────────────────────────┘
```

## 新架构设计

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    基于 arq 的简化架构                                        │
└─────────────────────────────────────────────────────────────────────────────┘

  Frontend ──► Main API ──► PostgreSQL (任务定义 + 执行历史)
                              │
                              │ 1. 创建任务 (Cron/延迟)
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              arq Scheduler                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  arq 内置调度器：                                                      │  │
│  │  • Cron 任务：每秒检查是否有任务到期                                    │  │
│  │  • 延迟任务：使用 Redis Sorted Set，自动触发                            │  │
│  │  • 无需自建时间轮                                                       │  │
│  │  • 无需分布式锁（arq 自动处理）                                          │  │
│  │                                                                       │  │
│  │  任务入队：                                                             │  │
│  │  enqueue_job('execute_chat_task',                                     │  │
│  │              _queue_name='default',                                   │  │
│  │              _defer_by=60)          # 延迟 60 秒                        │  │
│  │                                                                       │  │
│  └─────────────────────────────────┬─────────────────────────────────────┘  │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Redis (arq Broker)                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  arq:queue:{queue_name}      - 任务队列 (List)                        │  │
│  │  arq:delay_queue             - 延迟队列 (Sorted Set)                  │  │
│  │  arq:health:{worker_id}      - Worker 健康状态                         │  │
│  │  arq:stats                   - 统计信息                                │  │
│  │                                                                       │  │
│  └─────────────────────────────────┬─────────────────────────────────────┘  │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           arq Worker Cluster                                 │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐      │
│  │    Worker-1      │    │    Worker-2      │    │    Worker-N      │      │
│  │                  │    │                  │    │                  │      │
│  │  ┌────────────┐  │    │  ┌────────────┐  │    │  ┌────────────┐  │      │
│  │  │ arq Worker │  │    │  │ arq Worker │  │    │  │ arq Worker │  │      │
│  │  │            │  │    │  │            │  │    │  │            │  │      │
│  │  │ • 自动消费  │  │    │  │ • 自动消费  │  │    │  │ • 自动消费  │  │      │
│  │  │ • 自动重试  │  │    │  │ • 自动重试  │  │    │  │ • 自动重试  │  │      │
│  │  │ • 并发控制  │  │    │  │ • 并发控制  │  │    │  │ • 并发控制  │  │      │
│  │  │ • 优雅关闭  │  │    │  │ • 优雅关闭  │  │    │  │ • 优雅关闭  │  │      │
│  │  └──────┬─────┘  │    │  └──────┬─────┘  │    │  └──────┬─────┘  │      │
│  │         │        │    │         │        │    │         │        │      │
│  │         ▼        │    │         ▼        │    │         ▼        │      │
│  │  ┌────────────┐  │    │  ┌────────────┐  │    │  ┌────────────┐  │      │
│  │  │ execute_   │  │    │  │ execute_   │  │    │  │ execute_   │  │      │
│  │  │ chat_task  │  │    │  │ chat_task  │  │    │  │ chat_task  │  │      │
│  │  │            │  │    │  │            │  │    │  │            │  │      │
│  │  │ 1. 幂等性  │  │    │  │ 1. 幂等性  │  │    │  │ 1. 幂等性  │  │      │
│  │  │ 2. Chat API│  │    │  │ 2. Chat API│  │    │  │ 2. Chat API│  │      │
│  │  │ 3. 结果保存│  │    │  │ 3. 结果保存│  │    │  │ 3. 结果保存│  │      │
│  │  │ 4. 重试/通知│  │    │  │ 4. 重试/通知│  │    │  │ 4. 重试/通知│  │      │
│  │  └────────────┘  │    │  └────────────┘  │    │  └────────────┘  │      │
│  └──────────────────┘    └──────────────────┘    └──────────────────┘      │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Chat API 服务                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## arq 核心概念

### 1. Worker 配置

```python
# services/worker/main.py
import asyncio
from arq import create_pool, Worker
from arq.connections import RedisSettings
from app.config import get_settings

settings = get_settings()

# Redis 配置
redis_settings = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    database=0,
)

# Worker 配置
class TaskWorker(Worker):
    """自定义 arq Worker"""
    
    # 并发控制
    max_jobs = 10  # 同时执行的最大任务数
    
    # 任务超时
    job_timeout = 300  # 5 分钟
    
    # 重试配置
    retry_jobs = True
    max_tries = 3  # 最大重试次数
    
    # 优雅关闭
    handle_ctrl_c = True
    
    # 健康检查
    health_check_interval = 30  # 每 30 秒报告健康状态
    
    # 队列配置
    queue_name = 'default'
    
    async def on_startup(self, ctx):
        """Worker 启动时执行"""
        logger.info(f"Worker {self.worker_id} 启动")
        # 初始化数据库连接池等
        ctx['db'] = await create_async_engine(...)
        ctx['redis'] = await create_pool(redis_settings)
    
    async def on_shutdown(self, ctx):
        """Worker 关闭时执行"""
        logger.info(f"Worker {self.worker_id} 关闭")
        # 清理资源
        await ctx['db'].dispose()
        await ctx['redis'].close()

# 启动 Worker
async def run_worker():
    worker = TaskWorker(
        redis_settings=redis_settings,
        burst=False,  # False = 持续运行, True = 执行完队列后退出
    )
    await worker.main()

if __name__ == '__main__':
    asyncio.run(run_worker())
```

### 2. 任务函数

```python
# app/scheduler/tasks.py
from arq import Retry
from datetime import datetime
import structlog

logger = structlog.get_logger()

async def execute_chat_task(ctx, execution_id: str, user_id: str, parameters: dict):
    """
    arq 任务函数
    
    Args:
        ctx: arq 上下文，包含 db、redis 等
        execution_id: 执行 ID（用于幂等性）
        user_id: 用户 ID
        parameters: Chat API 参数
    """
    from app.scheduler.idempotency import IdempotencyController
    from app.scheduler.service import TaskExecutionService
    from app.chat.service import ChatService
    
    db = ctx['db']
    redis = ctx['redis']
    
    # 1. 幂等性检查
    idempotency = IdempotencyController(redis)
    should_execute, cached = await idempotency.check_or_create(
        execution_id,
        attempt=ctx.get('job_try', 1)  # arq 提供的重试次数
    )
    
    if not should_execute:
        if cached:
            logger.info("幂等性命中：任务已完成", execution_id=execution_id)
            return cached
        else:
            # 任务正在处理中，稍后重试
            raise Retry(defer=60)  # 60 秒后重试
    
    try:
        # 2. 更新执行状态为 running
        execution_service = TaskExecutionService(db)
        await execution_service.mark_running(execution_id, ctx['worker_id'])
        
        # 3. 调用 Chat API
        chat_service = ChatService(db)
        result = await chat_service.chat_completion(
            user_id=user_id,
            **parameters
        )
        
        # 4. 标记完成
        await idempotency.mark_completed(execution_id, result)
        await execution_service.mark_completed(execution_id, result)
        
        logger.info("任务执行成功", execution_id=execution_id)
        return result
        
    except Exception as e:
        # 5. 失败处理
        await idempotency.mark_failed_for_retry(execution_id)
        await execution_service.mark_failed(execution_id, str(e))
        
        # arq 自动重试
        raise Retry(
            defer=calculate_backoff(ctx.get('job_try', 1))
        ) from e

def calculate_backoff(attempt: int) -> int:
    """计算重试延迟（指数退避）"""
    delays = [0, 60, 300, 900]  # 0s, 1min, 5min, 15min
    return delays[min(attempt - 1, len(delays) - 1)]
```

### 3. 调度器（使用 arq cron）

```python
# services/scheduler/main.py
import asyncio
from arq import create_pool, cron
from arq.connections import RedisSettings
from datetime import datetime
from app.config import get_settings

settings = get_settings()

redis_settings = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
)

async def schedule_cron_tasks(ctx):
    """
    定期扫描数据库，将到期的 Cron 任务入队
    由 arq 的 cron 机制触发
    """
    from app.db.models import ScheduledTask
    from sqlalchemy import select, update
    from sqlalchemy.ext.asyncio import AsyncSession
    
    db: AsyncSession = ctx['db']
    redis = ctx['redis']
    
    # 查询到期的任务
    now = datetime.utcnow()
    stmt = select(ScheduledTask).where(
        ScheduledTask.next_run_at <= now,
        ScheduledTask.is_active == True
    )
    
    result = await db.execute(stmt)
    tasks = result.scalars().all()
    
    for task in tasks:
        # 创建执行记录
        from app.scheduler.service import TaskExecutionService
        execution_service = TaskExecutionService(db)
        execution = await execution_service.create_execution(task.id)
        
        # 入队 arq 任务
        await redis.enqueue_job(
            'execute_chat_task',
            execution_id=str(execution.id),
            user_id=str(task.created_by),
            parameters=task.parameters,
            _queue_name='default',
            # arq 会自动处理延迟执行
        )
        
        # 更新下次执行时间
        from croniter import croniter
        next_run = croniter(task.cron_expression, now).get_next(datetime)
        task.next_run_at = next_run
        await db.commit()
        
        logger.info("任务已入队", task_id=str(task.id), execution_id=str(execution.id))


class TaskScheduler:
    """arq 定时任务调度器"""
    
    # 使用 arq 的 cron 装饰器
    cron_jobs = [
        # 每秒检查一次（高频任务支持）
        cron(
            schedule_cron_tasks,
            hour=None,  # 每小时
            minute=None,  # 每分钟
            second=0,  # 每秒（如果支持）
            run_at_startup=True,
        ),
        
        # 或者使用更简单的每秒触发
        cron(
            schedule_cron_tasks,
            second='*/1',  # 每秒执行
            run_at_startup=True,
        ),
    ]
    
    redis_settings = redis_settings
    
    async def on_startup(self, ctx):
        """调度器启动"""
        logger.info("Scheduler 启动")
        ctx['db'] = await create_async_engine(...)
        ctx['redis'] = await create_pool(redis_settings)
    
    async def on_shutdown(self, ctx):
        """调度器关闭"""
        logger.info("Scheduler 关闭")
        await ctx['db'].dispose()
        await ctx['redis'].close()

# 启动调度器
async def run_scheduler():
    scheduler = TaskScheduler()
    await scheduler.main()

if __name__ == '__main__':
    asyncio.run(run_scheduler())
```

### 4. 简化后的架构优势

```
┌─────────────────────────────────────────────────────────────────┐
│                   架构对比                                       │
├─────────────────────────────┬───────────────────────────────────┤
│        自建方案             │          arq 方案                 │
├─────────────────────────────┼───────────────────────────────────┤
│ Scheduler: 自建             │ Scheduler: arq cron               │
│ • 时间轮算法                │ • 内置 cron 支持                  │
│ • 分布式锁                  │ • 无需锁                          │
│ • Leader 选举               │ • 单实例即可                      │
│ • 僵死检测                  │ • arq 自动管理                    │
├─────────────────────────────┼───────────────────────────────────┤
│ Worker: 自建                │ Worker: arq Worker                │
│ • BRPOP/ZRANGEBYSCORE       │ • 自动消费                        │
│ • 手动重试                  │ • 自动重试                        │
│ • 心跳管理                  │ • 内置健康检查                    │
│ • 优雅关闭                  │ • 内置优雅关闭                    │
├─────────────────────────────┼───────────────────────────────────┤
│ 代码量: ~3000 行            │ 代码量: ~500 行                   │
│ 复杂度: 高                  │ 复杂度: 低                        │
│ 维护成本: 高                │ 维护成本: 低                      │
│ 可靠性: 中（自定义）        │ 可靠性: 高（经过验证）             │
└─────────────────────────────┴───────────────────────────────────┘
```

## 保留的自定义组件

虽然使用 arq，但以下组件仍需自定义实现：

1. **幂等性控制**: arq 不提供幂等性，需要自建
2. **任务执行历史**: 需要记录到 PG，arq 只保留 Redis 中的短期日志
3. **Webhook 通知**: 任务失败后的通知逻辑
4. **任务管理 API**: 创建/更新/删除定时任务的 REST API

## 实施建议

### Phase 1: 替换 Worker
1. 安装 arq: `pip install arq`
2. 实现 arq Worker（替换自建 Worker）
3. 测试任务执行

### Phase 2: 替换 Scheduler
1. 实现 arq Scheduler（使用 cron）
2. 迁移现有 Cron 任务
3. 测试调度准确性

### Phase 3: 清理
1. 删除自建的时间轮、分布式锁等代码
2. 简化部署配置
3. 性能测试对比

## 权衡与决策

| 方案 | 优点 | 缺点 | 选择 |
|------|------|------|------|
| **自建** | 完全可控、深度定制 | 复杂、维护成本高 | ❌ 放弃 |
| **arq** | 简单可靠、功能完善 | 依赖第三方库、灵活性受限 | ✅ 采用 |

**结论**: 使用 arq 框架替换自建调度器和 Worker，大幅简化架构。
