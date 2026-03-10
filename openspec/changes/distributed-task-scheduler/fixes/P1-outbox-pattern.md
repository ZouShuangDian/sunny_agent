# P1 修复：核心一致性 - Outbox 模式实现

## 问题描述
当前设计在 `commit` 后才调用 `enqueue_job`，如果 enqueue 失败会导致：
- execution 记录已存在（status='queued'）
- 但任务永远不会被执行（orphan execution）

## 解决方案：Outbox 模式

```python
# 新增表：scheduler_outbox
class SchedulerOutbox(Base):
    __tablename__ = "scheduler_outbox"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid7)
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("task_executions.id"))
    task_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("scheduled_tasks.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("users.id"))
    parameters: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default='pending')  # pending/sent/failed
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)

# 索引
# idx_outbox_status_created: (status, created_at) - 查询待处理消息
# idx_outbox_execution: (execution_id) - 唯一约束防重
```

## 修复后的调度流程

```python
async def schedule_tasks(ctx):
    """修复后的调度函数 - 使用 Outbox 模式"""
    
    async with async_session() as db:
        now = datetime.utcnow()
        
        # 步骤 1: 查询并原子性认领任务（带行级锁）
        # 使用 FOR UPDATE SKIP LOCKED 防止并发调度
        stmt = select(ScheduledTask).where(
            ScheduledTask.next_run_at <= now,
            ScheduledTask.is_active == True
        ).order_by(
            ScheduledTask.next_run_at
        ).limit(100).with_for_update(skip_locked=True)
        
        result = await db.execute(stmt)
        tasks = result.scalars().all()
        
        for task in tasks:
            try:
                # 步骤 2: 在同一个事务中创建 execution 和 outbox
                execution = TaskExecution(
                    scheduled_task_id=task.id,
                    status='pending',  # 初始状态为 pending，不是 queued
                    scheduled_time=task.next_run_at
                )
                db.add(execution)
                await db.flush()  # 获取 execution.id
                
                # 创建 outbox 记录
                outbox = SchedulerOutbox(
                    execution_id=execution.id,
                    task_id=task.id,
                    user_id=task.created_by,
                    parameters=task.parameters,
                    status='pending'
                )
                db.add(outbox)
                
                # 更新下次执行时间
                next_run = croniter(task.cron_expression, now).get_next(datetime)
                task.next_run_at = next_run
                
                # 步骤 3: 提交事务（原子性保证 execution + outbox 同时成功）
                await db.commit()
                
                logger.info(
                    "Task outbox created",
                    task_id=str(task.id),
                    execution_id=str(execution.id),
                    outbox_id=str(outbox.id)
                )
                
            except Exception as e:
                logger.error("Failed to create outbox", task_id=str(task.id), error=str(e))
                await db.rollback()


async def process_outbox(ctx):
    """Outbox 处理器 - 后台持续发送消息到 arq"""
    
    redis = ctx['redis']
    
    while True:
        try:
            async with async_session() as db:
                # 查询待处理的 outbox 消息（批量 100）
                stmt = select(SchedulerOutbox).where(
                    SchedulerOutbox.status == 'pending',
                    SchedulerOutbox.retry_count < 5
                ).order_by(
                    SchedulerOutbox.created_at
                ).limit(100).with_for_update(skip_locked=True)
                
                result = await db.execute(stmt)
                outbox_items = result.scalars().all()
                
                for item in outbox_items:
                    try:
                        # 发送到 arq
                        await redis.enqueue_job(
                            'execute_chat_task',
                            execution_id=str(item.execution_id),
                            user_id=str(item.user_id),
                            parameters=item.parameters,
                            _queue_name='default'
                        )
                        
                        # 更新 execution 状态为 queued
                        await db.execute(
                            update(TaskExecution)
                            .where(TaskExecution.id == item.execution_id)
                            .values(status='queued')
                        )
                        
                        # 标记 outbox 为已发送
                        item.status = 'sent'
                        item.sent_at = datetime.utcnow()
                        await db.commit()
                        
                        logger.info(
                            "Outbox message sent",
                            outbox_id=str(item.id),
                            execution_id=str(item.execution_id)
                        )
                        
                    except Exception as e:
                        # 发送失败，增加重试计数
                        item.retry_count += 1
                        item.error_message = str(e)
                        
                        if item.retry_count >= 5:
                            item.status = 'failed'
                            logger.error(
                                "Outbox message failed permanently",
                                outbox_id=str(item.id),
                                error=str(e)
                            )
                        
                        await db.commit()
                        logger.warning(
                            "Outbox send failed, will retry",
                            outbox_id=str(item.id),
                            retry_count=item.retry_count
                        )
                
                if not outbox_items:
                    # 没有待处理消息，等待 1 秒
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.error("Outbox processor error", error=str(e))
            await asyncio.sleep(5)


# Outbox 清理任务（每天执行一次）
async def cleanup_outbox(ctx):
    """清理已发送超过 7 天的 outbox 记录"""
    
    async with async_session() as db:
        cutoff = datetime.utcnow() - timedelta(days=7)
        
        result = await db.execute(
            delete(SchedulerOutbox)
            .where(
                SchedulerOutbox.status.in_(['sent', 'failed']),
                SchedulerOutbox.created_at < cutoff
            )
        )
        
        await db.commit()
        
        if result.rowcount > 0:
            logger.info(f"Cleaned up {result.rowcount} old outbox records")
```

## 故障恢复机制

### 场景 1: Scheduler 崩溃（Outbox 未发送）
```
Scheduler 崩溃
    │
    ▼
Outbox 状态 = 'pending'
    │
    ▼
新 Scheduler 启动
    │
    ▼
process_outbox 继续发送
    │
    ▼
任务最终执行 ✅
```

### 场景 2: Outbox 发送失败（重试机制）
```
发送失败
    │
    ▼
retry_count += 1
    │
    ▼
< 5 次?
    ├─ 是: 下次循环重试
    └─ 否: 标记为 failed，人工介入
```

### 场景 3: 重复发送防护
```python
# 使用 execution_id 唯一约束防止重复
# idx_outbox_execution: UNIQUE (execution_id)

# 如果重复插入会抛出 IntegrityError，捕获并忽略
try:
    db.add(outbox)
    await db.commit()
except IntegrityError:
    logger.warning("Outbox already exists, skipping", execution_id=execution_id)
    await db.rollback()
```

## 时序图

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│Scheduler │     │PostgreSQL│     │ Outbox   │     │  Redis   │
└────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │                │
     │ 1. BEGIN TX    │                │                │
     │────────────────>                │                │
     │                │                │                │
     │ 2. INSERT execution (pending)   │                │
     │─────────────────────────────────>                │
     │                │                │                │
     │ 3. INSERT outbox (pending)      │                │
     │─────────────────────────────────>                │
     │                │                │                │
     │ 4. UPDATE task.next_run_at      │                │
     │────────────────>                │                │
     │                │                │                │
     │ 5. COMMIT      │                │                │
     │────────────────>                │                │
     │                │                │                │
     │ 6. SELECT outbox (pending)      │                │
     │─────────────────────────────────>                │
     │                │                │                │
     │ 7. enqueue_job │                │                │
     │──────────────────────────────────────────────────>
     │                │                │                │
     │ 8. UPDATE outbox (sent)         │                │
     │─────────────────────────────────>                │
     │                │                │                │
     │ 9. UPDATE execution (queued)    │                │
     │─────────────────────────────────>                │
     │                │                │                │
     └────────────────┴────────────────┴────────────────┘
```

## 优势

1. **原子性**: execution + outbox 在同一事务中
2. **可靠性**: Outbox 处理器独立运行，失败可重试
3. **可观测性**: outbox 表可监控积压、失败率
4. **幂等性**: execution_id 唯一约束防重
