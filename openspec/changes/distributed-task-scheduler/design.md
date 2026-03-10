# 分布式定时任务调度系统设计（基于 arq）

## Context

### 背景
Sunny Agent 是一个基于 FastAPI 的企业级 AI 智能体框架，目前缺少定时任务调度能力。经过技术调研，决定使用 **arq** 框架构建任务调度系统，替代复杂的自建方案。

### 技术选型决策
**选择 arq 的理由**:
- 自建方案复杂度高（时间轮、分布式锁、僵死检测等 ~3000 行代码）
- arq 提供开箱即用的 Cron 调度、延迟队列、自动重试
- arq 基于 Redis，与现有基础设施兼容
- arq 经过生产验证，可靠性高
- 代码量减少 80%，维护成本大幅降低

### 现有基础设施
- **Web 服务**: FastAPI + Uvicorn
- **数据库**: PostgreSQL（SQLAlchemy 2.0 + asyncpg）
- **缓存**: Redis（单实例）
- **部署**: Docker Compose

## Goals / Non-Goals

**Goals:**
- 支持 Cron 风格定时任务调度（秒级精度）
- 支持延迟任务（指定未来时间执行）
- 实现幂等性控制（防止重复执行）
- 实现双写保障（PG + Redis）
- 支持水平扩展（arq Worker 多实例）
- 提供完整监控和告警

**Non-Goals:**
- 不支持跨天任务依赖（DAG）- 超出 arq 能力
- 不支持任务优先级队列（arq 原生不支持）
- 不替代专业调度系统（如 Airflow）

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    基于 arq 的简化架构                                        │
└─────────────────────────────────────────────────────────────────────────────┘

  Frontend ──► Main API ──► PostgreSQL (任务定义 + 执行历史)
                              │
                              │ 1. 创建定时任务
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         arq Scheduler                                        │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  使用 arq cron 调度器：                                                │  │
│  │  • 每秒检查数据库中到期的任务                                            │  │
│  │  • 使用 enqueue_job() 将任务入队                                        │  │
│  │  • arq 自动处理延迟执行（使用 Redis Sorted Set）                         │  │
│  │  • 无需自建时间轮、无需分布式锁                                          │  │
│  │                                                                       │  │
│  └─────────────────────────────────┬─────────────────────────────────────┘  │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                                     │ enqueue_job()
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Redis (arq)                                     │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  arq:queue:default           - 标准队列 (List)                        │  │
│  │  arq:queue:high_priority     - 高优先级队列                            │  │
│  │  arq:in_progress             - 执行中任务                              │  │
│  │  arq:retry                   - 重试队列                                │  │
│  │  arq:health:*                - Worker 健康状态                         │  │
│  │                                                                       │  │
│  └─────────────────────────────────┬─────────────────────────────────────┘  │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                                     │ BRPOP / 自动消费
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
│  │  │ 4. 重试    │  │    │  │ 4. 重试    │  │    │  │ 4. 重试    │  │      │
│  │  └────────────┘  │    │  └────────────┘  │    │  └────────────┘  │      │
│  └──────────────────┘    └──────────────────┘    └──────────────────┘      │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Design

### 1. 数据库模型

```python
# scheduled_tasks 表
class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Shanghai")
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    
    retry_limit: Mapped[int] = mapped_column(Integer, default=3)
    retry_delays: Mapped[list] = mapped_column(JSONB, default=[0, 60, 300])
    webhook_url: Mapped[Optional[str]] = mapped_column(String(500))
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now())

# task_executions 表  
class TaskExecution(Base):
    __tablename__ = "task_executions"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid7)
    scheduled_task_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("scheduled_tasks.id"))
    
    status: Mapped[str] = mapped_column(String(20), default="queued")
    scheduled_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    worker_id: Mapped[Optional[str]] = mapped_column(String(100))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    
    result: Mapped[Optional[dict]] = mapped_column(JSONB)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    
    # 幂等性
    idempotency_status: Mapped[Optional[str]] = mapped_column(String(20))
    idempotency_result: Mapped[Optional[dict]] = mapped_column(JSONB)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now())
```

### 2. arq Scheduler 实现

```python
# services/scheduler/main.py
import asyncio
from datetime import datetime
from arq import create_pool, cron
from arq.connections import RedisSettings
from app.config import get_settings
from app.db.engine import async_session
from app.db.models import ScheduledTask
from sqlalchemy import select, update
from croniter import croniter
import structlog

logger = structlog.get_logger()
settings = get_settings()

redis_settings = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    database=0,
)

async def schedule_tasks(ctx):
    """
    arq Cron Job：每秒检查并调度到期任务
    """
    logger.debug("Checking for scheduled tasks...")
    
    async with async_session() as db:
        now = datetime.utcnow()
        
        # 查询到期的任务
        stmt = select(ScheduledTask).where(
            ScheduledTask.next_run_at <= now,
            ScheduledTask.is_active == True
        ).limit(100)
        
        result = await db.execute(stmt)
        tasks = result.scalars().all()
        
        for task in tasks:
            try:
                # 创建执行记录
                from app.scheduler.service import TaskExecutionService
                execution_service = TaskExecutionService(db)
                execution = await execution_service.create_execution(task.id)
                
                # 计算下次执行时间
                next_run = croniter(task.cron_expression, now).get_next(datetime)
                task.next_run_at = next_run
                await db.commit()
                
                # 入队 arq 任务
                redis = ctx['redis']
                await redis.enqueue_job(
                    'execute_chat_task',
                    execution_id=str(execution.id),
                    user_id=str(task.created_by),
                    parameters=task.parameters,
                    _queue_name='default',
                    # arq 自动处理延迟执行
                )
                
                logger.info(
                    "Task scheduled",
                    task_id=str(task.id),
                    execution_id=str(execution.id),
                    next_run=next_run.isoformat()
                )
                
            except Exception as e:
                logger.error("Failed to schedule task", task_id=str(task.id), error=str(e))
                await db.rollback()


async def startup(ctx):
    """Scheduler 启动"""
    logger.info("Scheduler starting...")
    ctx['redis'] = await create_pool(redis_settings)

async def shutdown(ctx):
    """Scheduler 关闭"""
    logger.info("Scheduler shutting down...")
    await ctx['redis'].close()

# arq Scheduler 配置
class Scheduler:
    functions = [schedule_tasks]
    
    # 每秒执行一次（支持高频任务）
    cron_jobs = [
        cron(schedule_tasks, second='*/1', run_at_startup=True)
    ]
    
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown

# 启动入口
async def main():
    scheduler = Scheduler()
    await scheduler.run()

if __name__ == '__main__':
    asyncio.run(main())
```

### 3. arq Worker 实现

```python
# services/worker/main.py
import asyncio
from arq import create_pool, Worker
from arq.connections import RedisSettings
from app.config import get_settings
import structlog

logger = structlog.get_logger()
settings = get_settings()

redis_settings = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    database=0,
)

async def startup(ctx):
    """Worker 启动"""
    from app.db.engine import async_session
    ctx['db'] = async_session
    ctx['redis'] = await create_pool(redis_settings)
    logger.info(f"Worker {ctx.get('worker_id', 'unknown')} started")

async def shutdown(ctx):
    """Worker 关闭"""
    logger.info(f"Worker {ctx.get('worker_id', 'unknown')} shutting down...")
    await ctx['redis'].close()

async def execute_chat_task(ctx, execution_id: str, user_id: str, parameters: dict):
    """
    arq 任务函数：执行 Chat API 调用
    
    Args:
        ctx: arq 上下文
        execution_id: 执行 ID（幂等性键）
        user_id: 用户 ID
        parameters: Chat API 参数
    """
    from app.scheduler.idempotency import IdempotencyController
    from app.scheduler.service import TaskExecutionService
    from app.chat.service import ChatService
    from arq import Retry
    
    db = ctx['db']
    redis = ctx['redis']
    job_try = ctx.get('job_try', 1)  # arq 提供的重试次数
    
    # 1. 幂等性检查
    idempotency = IdempotencyController(redis)
    should_execute, cached = await idempotency.check_or_create(
        execution_id,
        attempt=job_try
    )
    
    if not should_execute:
        if cached:
            logger.info("Idempotency hit: task already completed", execution_id=execution_id)
            return cached
        else:
            # 任务正在处理中，稍后重试
            raise Retry(defer=60)
    
    try:
        async with db() as session:
            # 2. 更新执行状态为 running
            execution_service = TaskExecutionService(session)
            await execution_service.mark_running(
                execution_id, 
                ctx.get('worker_id', 'unknown')
            )
            
            # 3. 调用 Chat API
            chat_service = ChatService(session)
            result = await chat_service.chat_completion(
                user_id=user_id,
                **parameters
            )
            
            # 4. 标记完成
            await idempotency.mark_completed(execution_id, result)
            await execution_service.mark_completed(execution_id, result)
            
            logger.info("Task executed successfully", execution_id=execution_id)
            return result
            
    except Exception as e:
        logger.error("Task execution failed", execution_id=execution_id, error=str(e))
        
        # 5. 失败处理：arq 自动重试
        await idempotency.mark_failed_for_retry(execution_id)
        
        # 指数退避重试
        delays = [60, 300, 900]  # 1min, 5min, 15min
        retry_delay = delays[min(job_try - 1, len(delays) - 1)]
        
        raise Retry(defer=retry_delay) from e

# arq Worker 配置
class TaskWorker(Worker):
    functions = [execute_chat_task]
    
    # 并发控制：同时执行的最大任务数
    max_jobs = 10
    
    # 任务超时：5 分钟
    job_timeout = 300
    
    # 重试配置
    retry_jobs = True
    max_tries = 3
    
    # 队列配置
    queue_name = 'default'
    
    # 健康检查
    health_check_interval = 30
    
    # 生命周期回调
    on_startup = startup
    on_shutdown = shutdown
    
    redis_settings = redis_settings

# 启动入口
async def main():
    worker = TaskWorker()
    await worker.run()

if __name__ == '__main__':
    asyncio.run(main())
```

### 4. 幂等性控制（仍需自建）

```python
# app/scheduler/idempotency.py
import json
from datetime import datetime, timedelta
from typing import Optional, Any
import structlog

logger = structlog.get_logger()

class IdempotencyController:
    """幂等性控制器：防止同一 execution 重复执行"""
    
    def __init__(self, redis, ttl_seconds: int = 86400):
        self.redis = redis
        self.ttl = ttl_seconds
        self.key_prefix = "idempotency"
    
    def _make_key(self, execution_id: str) -> str:
        return f"{self.key_prefix}:{execution_id}"
    
    async def check_or_create(self, execution_id: str, attempt: int = 1):
        """
        检查幂等性状态
        
        Returns:
            (should_execute, cached_result)
        """
        key = self._make_key(execution_id)
        
        # 1. 检查 Redis
        data = await self.redis.get(key)
        if data:
            record = json.loads(data)
            
            if record["status"] == "completed":
                logger.info("Idempotency hit: completed", execution_id=execution_id)
                return False, record.get("result")
            
            elif record["status"] == "processing":
                # 检查是否僵死（超过 5 分钟）
                started = datetime.fromisoformat(record["started_at"])
                if datetime.utcnow() - started > timedelta(minutes=5):
                    logger.warning("Dead task detected, allowing retry", execution_id=execution_id)
                    # 更新记录
                    record["status"] = "processing"
                    record["started_at"] = datetime.utcnow().isoformat()
                    record["attempt"] = attempt
                    await self.redis.setex(key, self.ttl, json.dumps(record))
                    return True, None
                else:
                    logger.info("Task in progress", execution_id=execution_id)
                    return False, None
        
        # 2. 创建新记录
        record = {
            "status": "processing",
            "started_at": datetime.utcnow().isoformat(),
            "attempt": attempt,
            "execution_id": execution_id
        }
        await self.redis.setex(key, self.ttl, json.dumps(record))
        return True, None
    
    async def mark_completed(self, execution_id: str, result: Any):
        """标记任务已完成"""
        key = self._make_key(execution_id)
        record = {
            "status": "completed",
            "result": result,
            "completed_at": datetime.utcnow().isoformat(),
            "execution_id": execution_id
        }
        await self.redis.setex(key, self.ttl, json.dumps(record))
        logger.info("Task marked as completed", execution_id=execution_id)
    
    async def mark_failed_for_retry(self, execution_id: str):
        """标记失败，允许重试"""
        key = self._make_key(execution_id)
        await self.redis.delete(key)
        logger.info("Task failed, allowing retry", execution_id=execution_id)
```

## Migration Plan

### 简化后的部署流程

```yaml
# docker-compose.yml
version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
    depends_on:
      - postgres
      - redis

  scheduler:
    build:
      context: .
      dockerfile: services/scheduler/Dockerfile
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
    depends_on:
      - postgres
      - redis
    # arq Scheduler 单实例即可

  worker:
    build:
      context: .
      dockerfile: services/worker/Dockerfile
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
    depends_on:
      - postgres
      - redis
    deploy:
      replicas: 3  # 可水平扩展

  postgres:
    image: postgres:15-alpine
    environment:
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
      - POSTGRES_DB=db
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
```

### 实施步骤

1. **Phase 1**: 安装 arq，实现 Worker
2. **Phase 2**: 实现 Scheduler（arq cron）
3. **Phase 3**: 迁移现有代码，删除自建的时间轮、分布式锁等
4. **Phase 4**: 测试验证，性能对比

## 权衡与优势

| 方案 | 代码量 | 复杂度 | 可靠性 | 维护成本 |
|------|--------|--------|--------|----------|
| **自建** | ~3000 行 | 高 | 中 | 高 |
| **arq** | ~500 行 | 低 | 高 | 低 |

**核心优势**:
- 秒级调度精度（arq 内置支持）
- 自动重试和延迟队列
- 无需分布式锁和 Leader 选举
- 简化的部署（单 Redis 实例）
- 经过生产验证的稳定性
