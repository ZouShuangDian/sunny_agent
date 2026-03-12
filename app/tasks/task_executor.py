"""
异步任务执行函数。

被 create_task 工具入队后由 Worker 消费。
复用 run_agent_pipeline() 共享执行链路，结构对齐 cron_executor.py。
"""

import asyncio
import uuid as _uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import update

from app.db.engine import async_session
from app.db.models.async_task import AsyncTask
from app.execution.pipeline import run_agent_pipeline
from app.notify import NotificationType, notify_user

log = structlog.get_logger()


async def execute_async_task(
    ctx: dict,
    *,
    task_id: str,
    session_id: str,
    usernumb: str,
    user_id: str,
    input_text: str,
    task_type: str = "deep_research",
) -> None:
    """arq 任务函数：执行异步 Agent 任务"""

    trace_id = f"task_{task_id[:8]}_{_uuid.uuid4().hex[:8]}"
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id, task_id=task_id, usernumb=usernumb, task_type=task_type,
    )

    # 任务描述前 30 字符，用于通知标题
    task_brief = input_text[:30] + ("..." if len(input_text) > 30 else "")

    # Step 1: 状态 → running（原子 UPDATE WHERE status='pending'，防止取消竞态）
    async with async_session() as db:
        result = await db.execute(
            update(AsyncTask)
            .where(AsyncTask.id == task_id, AsyncTask.status == "pending")
            .values(status="running", started_at=datetime.now(timezone.utc))
            .returning(AsyncTask.id)
        )
        if not result.scalar_one_or_none():
            log.info("任务状态已变更，跳过执行", task_id=task_id)
            return
        await db.commit()

    try:
        # Step 2: 复用共享管线执行
        reply, actual_sid = await run_agent_pipeline(
            usernumb=usernumb,
            user_id=user_id,
            input_text=input_text,
            session_id=session_id,
            trace_id=trace_id,
            source="async_task",
        )

        # Step 3: 状态 → completed
        await asyncio.shield(_update_task_status(
            task_id,
            status="completed",
            result_summary=reply[:500] if reply else None,
        ))

        # Step 4: 通知用户
        await asyncio.shield(notify_user(
            usernumb,
            notify_type=NotificationType.TASK_COMPLETED,
            title=f"后台任务「{task_brief}」已完成",
            content=reply[:200] if reply else "任务执行完成",
            session_id=actual_sid,
            task_id=task_id,
        ))

        log.info("异步任务执行成功", task_id=task_id, reply_len=len(reply))

    except asyncio.CancelledError:
        log.warning("异步任务执行超时（arq job_timeout）")
        await asyncio.shield(_update_task_status(
            task_id, status="timeout", error="执行时间超过限制",
        ))
        await asyncio.shield(notify_user(
            usernumb,
            notify_type=NotificationType.TASK_FAILED,
            title=f"后台任务「{task_brief}」执行超时",
            content="任务执行时间超过限制，已被终止",
            session_id=session_id,
            task_id=task_id,
        ))
        raise

    except Exception as e:
        log.exception("异步任务执行失败")
        await asyncio.shield(_update_task_status(
            task_id, status="failed", error=str(e)[:500],
        ))
        await asyncio.shield(notify_user(
            usernumb,
            notify_type=NotificationType.TASK_FAILED,
            title=f"后台任务「{task_brief}」执行失败",
            content=str(e)[:200],
            session_id=session_id,
            task_id=task_id,
        ))

    finally:
        structlog.contextvars.unbind_contextvars("trace_id", "task_id", "usernumb")


async def _update_task_status(
    task_id: str,
    status: str,
    result_summary: str | None = None,
    error: str | None = None,
) -> None:
    """回写任务状态"""
    values: dict = {"status": status}
    if result_summary is not None:
        values["result_summary"] = result_summary
    if error is not None:
        values["error_message"] = error
    if status in ("completed", "failed", "timeout"):
        values["completed_at"] = datetime.now(timezone.utc)

    async with async_session() as db:
        await db.execute(
            update(AsyncTask).where(AsyncTask.id == task_id).values(**values)
        )
        await db.commit()
