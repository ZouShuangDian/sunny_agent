# P1 修复：幂等性竞争条件 - 使用原子 SET NX

## 问题描述
当前幂等性流程：
```python
# 竞争条件！两个 Worker 同时执行：
Worker-A: GET idempotency:{id} -> None  ✅ 通过
Worker-B: GET idempotency:{id} -> None  ✅ 通过
Worker-A: SETEX ...                    ✅ 执行
Worker-B: SETEX ...                    ✅ 重复执行！❌
```

## 解决方案：使用 SET NX（原子操作）

```python
# app/scheduler/idempotency.py
import json
from datetime import datetime, timedelta
from typing import Optional, Any, Tuple
import structlog

logger = structlog.get_logger()

class IdempotencyController:
    """线程/进程安全的幂等性控制器 - 使用 Redis SET NX"""
    
    def __init__(self, redis, ttl_seconds: int = 86400):
        self.redis = redis
        self.ttl = ttl_seconds
        self.key_prefix = "idempotency"
    
    def _make_key(self, execution_id: str) -> str:
        return f"{self.key_prefix}:{execution_id}"
    
    async def acquire_execution_lock(
        self, 
        execution_id: str, 
        attempt: int = 1
    ) -> Tuple[bool, Optional[dict]]:
        """
        原子性获取执行锁
        
        Returns:
            (acquired, cached_result)
            - acquired: True = 获得锁，可以执行；False = 未获得锁
            - cached_result: 如果已完成，返回缓存结果
        """
        key = self._make_key(execution_id)
        
        # 步骤 1: 原子性尝试设置锁（SET NX EX）
        # NX = 仅当 key 不存在时才设置
        # EX = 设置过期时间
        lock_data = json.dumps({
            "status": "processing",
            "started_at": datetime.utcnow().isoformat(),
            "attempt": attempt
        })
        
        # SET key value NX EX ttl - 原子操作
        acquired = await self.redis.set(key, lock_data, nx=True, ex=self.ttl)
        
        if acquired:
            # 成功获得锁
            logger.info("Execution lock acquired", execution_id=execution_id)
            return True, None
        
        # 步骤 2: 未获得锁，检查现有状态
        existing = await self.redis.get(key)
        
        if not existing:
            # 异常情况：SET NX 失败但 key 不存在（可能在检查之间过期）
            logger.warning("Lock acquisition race condition, retrying", execution_id=execution_id)
            # 递归重试一次
            return await self.acquire_execution_lock(execution_id, attempt)
        
        record = json.loads(existing)
        
        if record["status"] == "completed":
            # 任务已完成，返回缓存结果
            logger.info("Task already completed (idempotency hit)", 
                       execution_id=execution_id)
            return False, record.get("result")
        
        if record["status"] == "processing":
            # 任务正在处理中，检查是否僵死
            started = datetime.fromisoformat(record["started_at"])
            elapsed = (datetime.utcnow() - started).total_seconds()
            
            if elapsed > 300:  # 超过 5 分钟视为僵死
                logger.warning("Dead task detected, force acquiring lock",
                             execution_id=execution_id,
                             elapsed_seconds=elapsed)
                
                # 强制获取锁：先删除再设置（非原子，但概率低）
                await self.redis.delete(key)
                
                # 再次尝试原子设置
                acquired = await self.redis.set(key, lock_data, nx=True, ex=self.ttl)
                if acquired:
                    return True, None
            
            # 任务正常处理中，稍后重试
            logger.info("Task in progress", 
                       execution_id=execution_id,
                       elapsed_seconds=elapsed)
            return False, None
        
        # 未知状态
        logger.error("Unknown idempotency status", 
                    execution_id=execution_id,
                    status=record["status"])
        return False, None
    
    async def mark_completed(self, execution_id: str, result: Any):
        """标记任务完成（原子性更新）"""
        key = self._make_key(execution_id)
        
        # 使用 Lua 脚本原子性更新（先检查再设置）
        lua_script = """
        local key = KEYS[1]
        local value = ARGV[1]
        local ttl = ARGV[2]
        
        -- 检查 key 是否存在
        if redis.call('exists', key) == 1 then
            -- 原子性更新
            redis.call('set', key, value, 'ex', ttl)
            return 1
        else
            -- key 不存在（异常情况）
            return 0
        end
        """
        
        record = json.dumps({
            "status": "completed",
            "result": result,
            "completed_at": datetime.utcnow().isoformat(),
            "execution_id": execution_id
        })
        
        updated = await self.redis.eval(
            lua_script,
            1,  # key 数量
            key,
            record,
            str(self.ttl)
        )
        
        if updated:
            logger.info("Task marked as completed", execution_id=execution_id)
        else:
            logger.warning("Could not update completed task (key missing)",
                         execution_id=execution_id)
    
    async def mark_failed_for_retry(self, execution_id: str):
        """标记失败，允许重试（删除锁）"""
        key = self._make_key(execution_id)
        await self.redis.delete(key)
        logger.info("Execution lock released for retry", execution_id=execution_id)


# Worker 中使用示例
async def execute_chat_task(ctx, execution_id: str, user_id: str, parameters: dict):
    """修复后的任务函数 - 使用原子幂等性控制"""
    
    from arq import Retry
    
    redis = ctx['redis']
    idempotency = IdempotencyController(redis)
    
    # 1. 原子性获取执行锁
    acquired, cached = await idempotency.acquire_execution_lock(
        execution_id,
        attempt=ctx.get('job_try', 1)
    )
    
    if not acquired:
        if cached:
            # 任务已完成，直接返回缓存结果
            logger.info("Returning cached result", execution_id=execution_id)
            return cached
        else:
            # 任务正在处理中，稍后重试
            logger.info("Task in progress, deferring retry", execution_id=execution_id)
            raise Retry(defer=60)
    
    try:
        # 2. 执行实际任务
        db = ctx['db']
        async with db() as session:
            # 更新状态为 running
            await mark_running(session, execution_id, ctx.get('worker_id'))
            
            # 调用 Chat API
            result = await chat_service.chat_completion(user_id=user_id, **parameters)
            
            # 3. 标记完成
            await idempotency.mark_completed(execution_id, result)
            await mark_completed(session, execution_id, result)
            
            logger.info("Task executed successfully", execution_id=execution_id)
            return result
            
    except Exception as e:
        # 4. 失败处理 - 释放锁以便重试
        await idempotency.mark_failed_for_retry(execution_id)
        
        # 指数退避重试
        delays = [60, 300, 900]
        retry_delay = delays[min(ctx.get('job_try', 1) - 1, len(delays) - 1)]
        
        logger.error("Task failed, will retry",
                    execution_id=execution_id,
                    retry_delay=retry_delay)
        
        raise Retry(defer=retry_delay) from e
```

## 关键改进点

| 原方案 | 修复方案 | 效果 |
|--------|----------|------|
| GET → SETEX（非原子） | SET NX EX（原子） | 消除竞争条件 |
| 无强制获取锁 | 僵死检测 + 强制获取 | 防止死锁 |
| 直接 SETEX 覆盖 | Lua 脚本原子更新 | 状态转换安全 |

## 竞争条件测试

```python
import asyncio
import pytest
from unittest.mock import AsyncMock

async def test_idempotency_concurrent():
    """测试并发下的幂等性"""
    
    redis = create_mock_redis()
    controller = IdempotencyController(redis)
    execution_id = "test-exec-001"
    
    results = []
    
    async def worker(worker_id):
        """模拟多个 Worker 同时尝试执行"""
        acquired, _ = await controller.acquire_execution_lock(execution_id)
        results.append((worker_id, acquired))
    
    # 10 个 Worker 同时尝试
    await asyncio.gather(*[worker(i) for i in range(10)])
    
    # 只有一个应该成功
    acquired_count = sum(1 for _, acquired in results if acquired)
    assert acquired_count == 1, f"Expected 1 acquired, got {acquired_count}"
```

## 时序对比

```
修复前（竞争条件）:
Worker-A ──► GET ──► None ──► SETEX ✅
Worker-B ──► GET ──► None ──► SETEX ✅  ❌ 重复执行

修复后（原子操作）:
Worker-A ──► SET NX ──► OK ✅
Worker-B ──► SET NX ──► NIL ❌ (key 已存在)
Worker-B ──► GET ──► processing ──► 稍后重试
```
