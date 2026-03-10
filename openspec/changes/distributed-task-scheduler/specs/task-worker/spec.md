# Capability: 任务执行 Worker（基于 arq）

## 需求

实现基于 arq Worker 的任务执行服务，利用 arq 内置的队列消费、重试、并发控制等功能，无需自建 Worker 逻辑。

### 功能性需求

#### REQ-1: arq Worker 集成
- 使用 arq Worker 类作为任务执行引擎
- 使用 Redis 作为消息代理
- 自动消费队列中的任务（BRPOP）
- 支持多队列（default/high_priority）

#### REQ-2: 任务执行
- 实现 `execute_chat_task` 任务函数
- 调用 Chat API 执行对话任务
- 传递用户上下文（created_by）
- 超时设置：默认 300 秒（5 分钟）

#### REQ-3: 幂等性控制
- 每个 execution 有唯一 execution_id
- 执行前检查幂等性状态（Redis）
- 已完成任务直接返回缓存结果
- 处理中任务抛出 Retry 异常（arq 自动重试）

#### REQ-4: arq 自动重试
- 使用 arq 内置重试机制（`Retry` 异常）
- 指数退避策略：60s, 300s, 900s
- 最大重试次数：3 次（max_tries=3）
- 超过重试次数标记为 FAILED

#### REQ-5: Worker 配置
- 并发控制：max_jobs=10（同时执行的最大任务数）
- 任务超时：job_timeout=300（5 分钟）
- 队列名称：queue_name='default'
- 健康检查：health_check_interval=30

#### REQ-6: 生命周期管理
- 启动回调：初始化数据库连接池
- 关闭回调：清理资源、关闭连接
- 优雅关闭：handle_ctrl_c=True（处理 SIGTERM）

### 非功能性需求

#### REQ-7: 水平扩展
- 无状态设计，支持多实例部署
- arq 自动处理任务分配（无需原子性认领）
- 连接池大小：10（可配置）

#### REQ-8: 错误处理
- 区分可重试错误和永久错误
- 网络错误自动重试（arq 处理）
- 业务错误不重试（直接失败）
- 记录详细错误日志

### 核心代码

#### Worker 配置
```python
from arq import Worker
from arq.connections import RedisSettings

redis_settings = RedisSettings(
    host='localhost',
    port=6379
)

class TaskWorker(Worker):
    functions = [execute_chat_task]
    
    # 并发控制
    max_jobs = 10
    
    # 任务超时
    job_timeout = 300
    
    # 重试配置
    retry_jobs = True
    max_tries = 3
    
    # 队列配置
    queue_name = 'default'
    
    # 健康检查
    health_check_interval = 30
    
    # 优雅关闭
    handle_ctrl_c = True
    
    redis_settings = redis_settings
    
    async def on_startup(self, ctx):
        ctx['db'] = await create_async_engine(...)
        logger.info(f"Worker {ctx.get('worker_id')} started")
    
    async def on_shutdown(self, ctx):
        await ctx['db'].dispose()
        logger.info(f"Worker {ctx.get('worker_id')} shutdown")
```

#### 任务函数
```python
from arq import Retry

async def execute_chat_task(ctx, execution_id: str, user_id: str, parameters: dict):
    """
    arq 任务函数
    
    Args:
        ctx: arq 上下文（包含 db, redis, worker_id, job_try 等）
        execution_id: 执行 ID（幂等性键）
        user_id: 用户 ID
        parameters: Chat API 参数
    """
    db = ctx['db']
    redis = ctx['redis']
    job_try = ctx.get('job_try', 1)  # arq 提供的重试次数
    
    # 1. 幂等性检查
    idempotency = IdempotencyController(redis)
    should_execute, cached = await idempotency.check_or_create(
        execution_id, attempt=job_try
    )
    
    if not should_execute:
        if cached:
            return cached
        else:
            # 任务正在处理中，稍后重试
            raise Retry(defer=60)
    
    try:
        # 2. 调用 Chat API
        chat_service = ChatService(db)
        result = await chat_service.chat_completion(
            user_id=user_id, **parameters
        )
        
        # 3. 标记完成
        await idempotency.mark_completed(execution_id, result)
        return result
        
    except Exception as e:
        # 4. arq 自动重试
        await idempotency.mark_failed_for_retry(execution_id)
        
        delays = [60, 300, 900]  # 指数退避
        retry_delay = delays[min(job_try - 1, len(delays) - 1)]
        
        raise Retry(defer=retry_delay) from e
```

### arq 上下文（ctx）

```python
ctx = {
    'redis': RedisPool,          # Redis 连接池
    'db': AsyncEngine,           # 数据库引擎（自定义添加）
    'worker_id': str,            # Worker ID
    'job_id': str,               # 当前任务 ID
    'job_try': int,              # 当前重试次数（从 1 开始）
    # ... 其他 arq 内置字段
}
```

### 监控指标

```python
# arq 内置指标（通过 health check 暴露）
arq:jobs_completed        # 已完成任务数
arq:jobs_failed           # 失败任务数
arq:jobs_retried          # 重试任务数
arq:queue_depth           # 队列深度
arq:in_progress           # 执行中任务数

# 自定义指标
TASK_EXECUTION_DURATION = Histogram('task_execution_duration_seconds', ...)
IDEMPOTENCY_CACHE = Counter('idempotency_cache_operations', ..., ['result'])
```

### 对比：自建 vs arq

| 特性 | 自建 Worker | arq Worker |
|------|-------------|------------|
| 队列消费 | BRPOP/ZRANGEBYSCORE | arq 自动处理 |
| 任务认领 | UPDATE ... RETURNING | 不需要（arq 自动分配） |
| 重试机制 | 自建指数退避 | arq 内置 Retry |
| 并发控制 | 自建信号量 | max_jobs 参数 |
| 超时控制 | 自建 asyncio.wait_for | job_timeout 参数 |
| 优雅关闭 | 自建 SIGTERM 处理 | handle_ctrl_c 参数 |
| 健康检查 | 自建 | health_check_interval |
| 心跳管理 | 自建 Redis SETEX | arq 内置 |
| 代码量 | ~1500 行 | ~200 行 |
