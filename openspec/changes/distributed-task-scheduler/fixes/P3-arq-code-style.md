# P3 修复：代码样例与 arq 实际用法一致

## 问题
文档中的代码样例与 arq 实际用法可能不一致，实施风险高。

## 修复后：符合 arq 标准用法的代码

### 1. Worker 配置（标准 arq 风格）

```python
# services/worker/main.py
"""
标准 arq Worker 配置
参考：https://arq-docs.helpmanual.io/
"""

import asyncio
from arq import create_pool, Worker, Retry
from arq.connections import RedisSettings
from app.config import get_settings
from app.scheduler.idempotency import IdempotencyController
import structlog

logger = structlog.get_logger()
settings = get_settings()

# Redis 配置
redis_settings = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    database=0,
)

async def execute_chat_task(ctx, execution_id: str, user_id: str, parameters: dict):
    """
    arq 任务函数
    
    注意：
    - ctx 是 arq 自动注入的上下文
    - 函数名会在 enqueue_job 时引用
    - 抛出 Retry 异常会触发重试
    """
    redis = ctx['redis']
    db = ctx.get('db')  # 在 startup 中初始化
    job_try = ctx.get('job_try', 1)
    
    # 幂等性控制
    idempotency = IdempotencyController(redis)
    acquired, cached = await idempotency.acquire_execution_lock(
        execution_id, attempt=job_try
    )
    
    if not acquired:
        if cached:
            return cached
        raise Retry(defer=60)
    
    try:
        # 执行任务...
        result = await do_chat_call(db, user_id, parameters)
        
        await idempotency.mark_completed(execution_id, result)
        return result
        
    except Exception as e:
        await idempotency.mark_failed_for_retry(execution_id)
        
        # arq 重试：抛出 Retry 异常
        delays = [60, 300, 900]
        raise Retry(defer=delays[min(job_try - 1, 2)]) from e


async def startup(ctx):
    """Worker 启动时执行"""
    from app.db.engine import async_session
    ctx['db'] = async_session
    logger.info(f"Worker starting...")

async def shutdown(ctx):
    """Worker 关闭时执行"""
    logger.info(f"Worker shutting down...")


# 标准 arq Worker 类（非自定义类）
class WorkerSettings:
    """arq 标准配置类"""
    
    # 任务函数列表
    functions = [execute_chat_task]
    
    # Redis 配置
    redis_settings = redis_settings
    
    # 生命周期回调
    on_startup = startup
    on_shutdown = shutdown
    
    # Worker 配置
    max_jobs = 10              # 并发数
    job_timeout = 300          # 超时 5 分钟
    max_tries = 3              # 最大重试次数
    health_check_interval = 30 # 健康检查间隔
    handle_ctrl_c = True       # 处理 SIGTERM
    
    # 队列名
    queue_name = 'default'


# 启动入口
if __name__ == '__main__':
    # 使用 arq 标准启动方式
    asyncio.run(Worker(**WorkerSettings.__dict__).run())
```

### 2. Scheduler 配置（标准 arq cron）

```python
# services/scheduler/main.py
"""
标准 arq Scheduler 配置
使用 arq cron 功能
"""

import asyncio
from datetime import datetime
from arq import create_pool, cron
from arq.connections import RedisSettings
from croniter import croniter
from sqlalchemy import select, update
from app.config import get_settings
from app.db.engine import async_session
from app.db.models import ScheduledTask
import structlog

logger = structlog.get_logger()
settings = get_settings()

redis_settings = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    database=0,
)

async def schedule_tasks(ctx):
    """
    调度任务函数
    由 arq cron 每秒触发
    """
    redis = ctx['redis']
    
    async with async_session() as db:
        now = datetime.utcnow()
        
        # 原子性查询并锁定
        stmt = (
            select(ScheduledTask)
            .where(
                ScheduledTask.next_run_at <= now,
                ScheduledTask.is_active == True
            )
            .order_by(ScheduledTask.next_run_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        
        result = await db.execute(stmt)
        tasks = result.scalars().all()
        
        for task in tasks:
            try:
                # 创建 execution
                from app.scheduler.service import TaskExecutionService
                execution_service = TaskExecutionService(db)
                execution = await execution_service.create_execution(task.id)
                
                # 更新下次执行时间
                next_run = croniter(task.cron_expression, now).get_next(datetime)
                task.next_run_at = next_run
                await db.flush()
                
                # 创建 outbox
                from app.scheduler.models import SchedulerOutbox
                outbox = SchedulerOutbox(
                    execution_id=execution.id,
                    task_id=task.id,
                    user_id=task.created_by,
                    parameters=task.parameters
                )
                db.add(outbox)
                await db.commit()
                
                logger.info("Task scheduled", 
                          task_id=str(task.id),
                          execution_id=str(execution.id))
                
            except Exception as e:
                logger.error("Failed to schedule", 
                           task_id=str(task.id), 
                           error=str(e))
                await db.rollback()


async def process_outbox(ctx):
    """
    Outbox 处理器
    持续后台运行，发送消息到队列
    """
    redis = ctx['redis']
    
    while True:
        try:
            async with async_session() as db:
                from app.scheduler.models import SchedulerOutbox
                
                # 查询待处理消息
                stmt = (
                    select(SchedulerOutbox)
                    .where(
                        SchedulerOutbox.status == 'pending',
                        SchedulerOutbox.retry_count < 5
                    )
                    .limit(100)
                )
                
                result = await db.execute(stmt)
                items = result.scalars().all()
                
                for item in items:
                    try:
                        # 入队 arq 任务
                        await redis.enqueue_job(
                            'execute_chat_task',  # 函数名字符串
                            execution_id=str(item.execution_id),
                            user_id=str(item.user_id),
                            parameters=item.parameters
                        )
                        
                        item.status = 'sent'
                        item.sent_at = datetime.utcnow()
                        await db.commit()
                        
                    except Exception as e:
                        item.retry_count += 1
                        item.error_message = str(e)
                        await db.commit()
                
                if not items:
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.error("Outbox processor error", error=str(e))
            await asyncio.sleep(5)


async def startup(ctx):
    """启动回调"""
    ctx['redis'] = await create_pool(redis_settings)
    ctx['outbox_task'] = asyncio.create_task(process_outbox(ctx))
    logger.info("Scheduler starting...")

async def shutdown(ctx):
    """关闭回调"""
    ctx['outbox_task'].cancel()
    try:
        await ctx['outbox_task']
    except asyncio.CancelledError:
        pass
    await ctx['redis'].close()
    logger.info("Scheduler shutting down...")


# arq 配置类
class SchedulerSettings:
    """arq Scheduler 配置"""
    
    functions = [schedule_tasks]
    
    # cron 调度：每秒执行一次
    cron_jobs = [
        cron(
            schedule_tasks,
            second='*/1',        # 每秒
            run_at_startup=True  # 启动时立即执行
        )
    ]
    
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown


if __name__ == '__main__':
    asyncio.run(cron(**SchedulerSettings.__dict__).run())
```

### 3. 任务入队（标准 arq API）

```python
# 标准 arq 入队方式
from arq import create_pool

redis = await create_pool(RedisSettings())

# 立即执行
job = await redis.enqueue_job(
    'execute_chat_task',      # 函数名字符串
    execution_id='uuid',
    user_id='user-uuid',
    parameters={'model': 'gpt-4'}
)

# 延迟执行（_defer_by 参数）
job = await redis.enqueue_job(
    'execute_chat_task',
    execution_id='uuid',
    user_id='user-uuid', 
    parameters={'model': 'gpt-4'},
    _defer_by=60  # 60 秒后执行
)

# 指定队列
job = await redis.enqueue_job(
    'execute_chat_task',
    execution_id='uuid',
    _queue_name='high_priority'  # 高优先级队列
)

# 获取任务信息
print(job.job_id)      # 任务 ID
print(job.status)      # 任务状态
```

### 4. 与文档不一致点修正

| 文档原写法 | 标准 arq 写法 | 说明 |
|-----------|--------------|------|
| `class TaskWorker(Worker):` | `class WorkerSettings:` | arq 使用配置类而非继承 |
| `async def main():` | `if __name__ == '__main__':` | 标准入口 |
| `worker.run()` | `Worker(**settings).run()` | 解包配置 |
| `ctx['worker_id']` | `ctx.get('job_id')` | arq 使用 job_id |
| 自定义 cron 类 | 使用 `arq.cron` | 标准装饰器 |

### 5. 依赖安装

```bash
# requirements.txt
arq>=0.25.0          # 异步任务队列
croniter>=1.0.0     # Cron 表达式
pytz>=2023.3        # 时区处理
redis>=4.5.0        # Redis 客户端
httpx>=0.24.0       # HTTP 客户端（Webhook）
```

### 6. 运行命令

```bash
# 启动 Worker
python services/worker/main.py

# 或使用 arq CLI
arq services.worker.main.WorkerSettings

# 启动 Scheduler  
python services/scheduler/main.py

# 或使用 arq CLI
arq services.scheduler.main.SchedulerSettings
```

## 验证清单

- [ ] Worker 使用 `WorkerSettings` 配置类而非自定义类
- [ ] Scheduler 使用 `cron` 装饰器配置调度
- [ ] 任务函数使用 `ctx` 获取上下文
- [ ] 重试使用 `Retry` 异常
- [ ] 入队使用 `enqueue_job` 方法
- [ ] 使用 `create_pool` 创建 Redis 连接
