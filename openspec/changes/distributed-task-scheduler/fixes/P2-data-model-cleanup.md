# P2 修复：数据模型清理与更新

## 问题
1. 保留 scheduler_token/scheduler_id（旧 fencing 方案），但 arq 方案声明不需要
2. 时区处理不完整（使用 utcnow() 而非任务时区）

## 修复后模型

```python
# app/scheduler/models.py

from datetime import datetime
from typing import Optional
import uuid
from sqlalchemy import String, Text, Integer, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from uuid6 import uuid7
from app.db.models.base import Base


class ScheduledTask(Base):
    """定时任务定义表"""
    __tablename__ = "scheduled_tasks"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    
    # Cron 表达式（支持秒级：如 "*/10 * * * * *" 每 10 秒）
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # 时区（IANA 格式，如 "Asia/Shanghai"）
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Shanghai")
    
    # 任务参数（传给 Chat API）
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    
    # 重试策略
    retry_limit: Mapped[int] = mapped_column(Integer, default=3)
    retry_delays: Mapped[list] = mapped_column(JSONB, default=[0, 60, 300])
    
    # Webhook 配置
    webhook_url: Mapped[Optional[str]] = mapped_column(String(500))
    webhook_secret: Mapped[Optional[str]] = mapped_column(String(255))  # 签名密钥
    
    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # 下次执行时间（带时区）
    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        index=True
    )
    
    # 上次成功调度时间（用于监控）
    last_scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    # 审计字段
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now())
    
    # 🔴 已移除：scheduler_token, scheduler_id（arq 不需要）
    # 🔴 已移除：schedule_strategy（统一由调度器处理）
    # 🔴 已移除：not_loaded（使用 outbox 模式替代）


class TaskExecution(Base):
    """任务执行历史表"""
    __tablename__ = "task_executions"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    scheduled_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("scheduled_tasks.id"),
        nullable=False
    )
    
    # 执行状态
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/queued/running/completed/failed
    
    # 时间（全部带时区）
    scheduled_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    # 执行 Worker
    worker_id: Mapped[Optional[str]] = mapped_column(String(100))
    
    # 重试信息
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    
    # 结果
    result: Mapped[Optional[dict]] = mapped_column(JSONB)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    
    # 幂等性状态
    idempotency_status: Mapped[Optional[str]] = mapped_column(String(20))  # processing/completed
    idempotency_result: Mapped[Optional[dict]] = mapped_column(JSONB)
    
    # Webhook 状态
    webhook_attempts: Mapped[int] = mapped_column(Integer, default=0)
    webhook_last_error: Mapped[Optional[str]] = mapped_column(Text)
    
    # 审计
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now())


class SchedulerOutbox(Base):
    """调度器 Outbox 表（事务外盒模式）"""
    __tablename__ = "scheduler_outbox"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("task_executions.id"),
        unique=True,  # 防止重复
        nullable=False
    )
    
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("scheduled_tasks.id"),
        nullable=False
    )
    
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("users.id"),
        nullable=False
    )
    
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    
    # 状态
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/sent/failed
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # 时间
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    
    # 索引
    __table_args__ = (
        Index('idx_outbox_status_created', 'status', 'created_at'),
        Index('idx_outbox_execution', 'execution_id', unique=True),
    )
```

## 修复后时区处理

```python
# services/scheduler/main.py

import pytz
from datetime import datetime
from croniter import croniter

async def schedule_tasks(ctx):
    """修复后：使用时区感知的调度"""
    
    async with async_session() as db:
        # 使用数据库当前时间（带时区）
        result = await db.execute(select(func.now()))
        db_now = result.scalar()  # 带时区的 datetime
        
        # 查询到期的任务
        stmt = select(ScheduledTask).where(
            ScheduledTask.next_run_at <= db_now,  # 带时区比较
            ScheduledTask.is_active == True
        ).limit(100).with_for_update(skip_locked=True)
        
        result = await db.execute(stmt)
        tasks = result.scalars().all()
        
        for task in tasks:
            try:
                # 获取任务时区
                task_tz = pytz.timezone(task.timezone)
                
                # 将当前时间转换到任务时区
                local_now = db_now.astimezone(task_tz)
                
                # 使用任务时区计算下次执行时间
                next_run_local = croniter(task.cron_expression, local_now).get_next(datetime)
                
                # 转换回 UTC 存储
                next_run_utc = next_run_local.astimezone(pytz.UTC)
                
                # 更新任务
                task.next_run_at = next_run_utc
                
                # ... 创建 execution 和 outbox ...
                
            except pytz.exceptions.UnknownTimeZoneError:
                logger.error("Unknown timezone", task_id=str(task.id), timezone=task.timezone)
                continue
```

## 数据库迁移

```python
# alembic 迁移脚本

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'add_scheduler_tables'
down_revision = None

def upgrade():
    # scheduled_tasks
    op.create_table(
        'scheduled_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('cron_expression', sa.String(100), nullable=False),
        sa.Column('timezone', sa.String(50), default='Asia/Shanghai'),
        sa.Column('parameters', postgresql.JSONB(), default={}),
        sa.Column('retry_limit', sa.Integer(), default=3),
        sa.Column('retry_delays', postgresql.JSONB(), default=[0, 60, 300]),
        sa.Column('webhook_url', sa.String(500), nullable=True),
        sa.Column('webhook_secret', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column('last_scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    
    # task_executions
    op.create_table(
        'task_executions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('scheduled_task_id', postgresql.UUID(as_uuid=True), 
                  sa.ForeignKey('scheduled_tasks.id'), nullable=False),
        sa.Column('status', sa.String(20), default='pending'),
        sa.Column('scheduled_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('worker_id', sa.String(100), nullable=True),
        sa.Column('retry_count', sa.Integer(), default=0),
        sa.Column('max_retries', sa.Integer(), default=3),
        sa.Column('result', postgresql.JSONB(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('idempotency_status', sa.String(20), nullable=True),
        sa.Column('idempotency_result', postgresql.JSONB(), nullable=True),
        sa.Column('webhook_attempts', sa.Integer(), default=0),
        sa.Column('webhook_last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    
    op.create_index('idx_executions_task_id', 'task_executions', ['scheduled_task_id'])
    op.create_index('idx_executions_status', 'task_executions', ['status'])
    
    # scheduler_outbox
    op.create_table(
        'scheduler_outbox',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('execution_id', postgresql.UUID(as_uuid=True), 
                  sa.ForeignKey('task_executions.id'), unique=True, nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), 
                  sa.ForeignKey('scheduled_tasks.id'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), 
                  sa.ForeignKey('users.id'), nullable=False),
        sa.Column('parameters', postgresql.JSONB(), default={}),
        sa.Column('status', sa.String(20), default='pending'),
        sa.Column('retry_count', sa.Integer(), default=0),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
    )
    
    op.create_index('idx_outbox_status_created', 'scheduler_outbox', ['status', 'created_at'])
    op.create_index('idx_outbox_execution', 'scheduler_outbox', ['execution_id'], unique=True)

def downgrade():
    op.drop_table('scheduler_outbox')
    op.drop_table('task_executions')
    op.drop_table('scheduled_tasks')
```

## 变更总结

| 字段/表 | 变更 | 原因 |
|---------|------|------|
| `scheduled_tasks.scheduler_token` | 删除 | arq 不需要 fencing token |
| `scheduled_tasks.scheduler_id` | 删除 | arq 不需要分布式锁 |
| `scheduled_tasks.schedule_strategy` | 删除 | 统一调度，无需分类 |
| `scheduled_tasks.not_loaded` | 删除 | 使用 outbox 替代 |
| `scheduled_tasks.last_scheduled_at` | 新增 | 监控调度活动 |
| `scheduled_tasks.webhook_secret` | 新增 | 强制 Webhook 签名 |
| `scheduler_outbox` | 新增表 | 事务外盒模式 |
| 所有时间字段 | 添加 timezone=True | 正确处理时区 |
