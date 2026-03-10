# P1 修复：调度并发控制 - 原子性任务认领

## 问题描述
当前调度流程：
```python
# 无锁查询！多个 Scheduler 实例会获取相同任务
stmt = select(ScheduledTask).where(
    ScheduledTask.next_run_at <= now
).limit(100)

# 逐条处理，没有原子性保证
tasks = result.scalars().all()
for task in tasks:
    # 可能多个 Scheduler 同时处理同一个 task！
```

**风险**: 多实例部署、重启或调度器重叠时，会创建重复 execution

## 解决方案：原子性认领 + 行级锁

### 方案 1: FOR UPDATE SKIP LOCKED（推荐）

```python
# services/scheduler/main.py

async def schedule_tasks(ctx):
    """修复后的调度函数 - 使用行级锁防止并发"""
    
    async with async_session() as db:
        now = datetime.utcnow()
        
        # 步骤 1: 原子性查询并锁定任务
        # FOR UPDATE SKIP LOCKED: 跳过已被其他事务锁定的行
        stmt = (
            select(ScheduledTask)
            .where(
                ScheduledTask.next_run_at <= now,
                ScheduledTask.is_active == True
            )
            .order_by(ScheduledTask.next_run_at)
            .limit(100)
            .with_for_update(skip_locked=True)  # 🔑 关键：行级锁
        )
        
        result = await db.execute(stmt)
        tasks = result.scalars().all()
        
        for task in tasks:
            try:
                # 步骤 2: 立即更新 next_run_at（防止重复调度）
                # 在同一个事务中原子性推进调度时间
                next_run = croniter(task.cron_expression, now).get_next(datetime)
                
                # 使用 RETURNING 获取更新后的值（原子性）
                update_stmt = (
                    update(ScheduledTask)
                    .where(ScheduledTask.id == task.id)
                    .values(
                        next_run_at=next_run,
                        last_scheduled_at=now  # 记录调度时间
                    )
                    .returning(ScheduledTask.id, ScheduledTask.next_run_at)
                )
                
                update_result = await db.execute(update_stmt)
                updated = update_result.fetchone()
                
                if not updated:
                    # 更新失败（理论上不会发生，因为已被锁定）
                    logger.warning("Task update failed, skipping", task_id=str(task.id))
                    continue
                
                # 步骤 3: 创建 execution 记录
                execution = TaskExecution(
                    scheduled_task_id=task.id,
                    status='pending',
                    scheduled_time=task.next_run_at
                )
                db.add(execution)
                await db.flush()  # 获取 execution.id
                
                # 步骤 4: 创建 outbox 记录（见 P1-outbox-pattern.md）
                outbox = SchedulerOutbox(
                    execution_id=execution.id,
                    task_id=task.id,
                    user_id=task.created_by,
                    parameters=task.parameters,
                    status='pending'
                )
                db.add(outbox)
                
                # 步骤 5: 提交事务
                await db.commit()
                
                logger.info(
                    "Task atomically claimed and scheduled",
                    task_id=str(task.id),
                    execution_id=str(execution.id),
                    next_run=next_run.isoformat()
                )
                
            except Exception as e:
                logger.error("Failed to schedule task", 
                           task_id=str(task.id), 
                           error=str(e))
                await db.rollback()


# 数据库模型更新 - 添加 last_scheduled_at
class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    
    # ... 原有字段 ...
    
    # 新增：记录上次调度时间（用于调试和监控）
    last_scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        comment="上次成功调度时间"
    )
```

### 方案 2: 单 SQL 原子操作（更严格）

```python
# 使用 CTE + UPDATE RETURNING 一次性处理
async def schedule_tasks_atomic(ctx):
    """原子性调度 - 使用单个 SQL 语句"""
    
    async with async_session() as db:
        now = datetime.utcnow()
        
        # 使用 CTE (Common Table Expression) 原子性处理
        # 1. 选择任务
        # 2. 更新 next_run_at
        # 3. 返回任务信息
        
        sql = """
        WITH tasks_to_schedule AS (
            SELECT id, cron_expression, created_by, parameters
            FROM scheduled_tasks
            WHERE next_run_at <= :now
              AND is_active = TRUE
            ORDER BY next_run_at
            LIMIT 100
            FOR UPDATE SKIP LOCKED
        ),
        updated_tasks AS (
            UPDATE scheduled_tasks
            SET next_run_at = CASE 
                WHEN cron_expression IS NOT NULL THEN
                    -- 使用 croniter 计算下次执行时间
                    :now::timestamp + interval '1 second' * 
                    EXTRACT(EPOCH FROM (cron_next(cron_expression, :now) - :now))
                ELSE
                    :now + interval '1 minute'
                END,
                last_scheduled_at = :now
            FROM tasks_to_schedule
            WHERE scheduled_tasks.id = tasks_to_schedule.id
            RETURNING scheduled_tasks.id, 
                      scheduled_tasks.created_by,
                      tasks_to_schedule.parameters
        )
        SELECT * FROM updated_tasks;
        """
        
        result = await db.execute(text(sql), {"now": now})
        tasks = result.mappings().all()
        
        for task in tasks:
            # 为每个任务创建 execution 和 outbox
            # ... （同上）
            pass
```

### 方案 3: 应用层乐观锁（辅助）

```python
class ScheduledTask(Base):
    # ... 原有字段 ...
    
    # 乐观锁版本号
    version: Mapped[int] = mapped_column(Integer, default=0)

# 调度时检查版本号
update_stmt = (
    update(ScheduledTask)
    .where(
        ScheduledTask.id == task.id,
        ScheduledTask.version == task.version  # 乐观锁条件
    )
    .values(
        next_run_at=next_run,
        version=task.version + 1
    )
)

result = await db.execute(update_stmt)
if result.rowcount == 0:
    # 版本冲突，其他 Scheduler 已更新
    logger.warning("Optimistic lock conflict, skipping", task_id=str(task.id))
    continue
```

## 推荐组合

```python
# 生产环境推荐：FOR UPDATE SKIP LOCKED + 立即更新 next_run_at
# 适用场景：
# - 多 Scheduler 实例部署
# - 高频任务（每秒扫描）
# - 需要强一致性保证

stmt = (
    select(ScheduledTask)
    .where(ScheduledTask.next_run_at <= now, ScheduledTask.is_active == True)
    .order_by(ScheduledTask.next_run_at)
    .limit(100)
    .with_for_update(skip_locked=True)  # PostgreSQL 行级锁
)
```

## 并发测试

```python
import asyncio
import pytest

async def test_concurrent_scheduling():
    """测试并发调度不会重复"""
    
    # 创建测试任务
    task = await create_scheduled_task(cron="* * * * *")
    
    execution_counts = []
    
    async def scheduler_instance(instance_id):
        """模拟多个 Scheduler 实例同时调度"""
        await schedule_tasks(ctx)
        
        # 统计创建的 execution 数
        count = await db.scalar(
            select(func.count(TaskExecution.id))
            .where(TaskExecution.scheduled_task_id == task.id)
        )
        execution_counts.append((instance_id, count))
    
    # 5 个 Scheduler 实例同时运行
    await asyncio.gather(*[scheduler_instance(i) for i in range(5)])
    
    # 应该只创建 1 个 execution
    total_executions = max(count for _, count in execution_counts)
    assert total_executions == 1, f"Expected 1 execution, got {total_executions}"
```

## 监控

```python
# 监控重复调度
SCHEDULED_TASKS_DUPLICATE = Counter(
    'scheduled_tasks_duplicate_total',
    'Number of duplicate scheduling attempts prevented'
)

# 在调度逻辑中
if not updated:
    SCHEDULED_TASKS_DUPLICATE.inc()
    logger.warning("Duplicate scheduling prevented", task_id=str(task.id))
```
