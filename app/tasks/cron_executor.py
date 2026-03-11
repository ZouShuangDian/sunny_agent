"""
arq 任务函数：定时任务执行。

被 Cron Scanner 入队后由 Worker 消费。
调用 run_agent_pipeline() 共享执行链路，保证与 chat.py 完全一致。
"""

import asyncio
import uuid as _uuid

import structlog
from sqlalchemy import update

from app.db.engine import async_session
from app.db.models.cron_job import CronJob
from app.execution.pipeline import run_agent_pipeline

log = structlog.get_logger()


async def execute_cron_job(
    ctx: dict,
    cron_job_id: str,
    usernumb: str,
    user_id: str,
    input_text: str,
    session_id: str | None,
) -> None:
    """Worker 消费的任务函数：通过共享管线执行一次完整的 Agent 对话"""
    # 生成 trace_id 用于日志追踪
    trace_id = f"cron_{cron_job_id[:8]}_{_uuid.uuid4().hex[:8]}"

    # 绑定 structlog contextvars，整条链路日志自动携带
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        cron_job_id=cron_job_id,
        usernumb=usernumb,
    )

    try:
        reply = await run_agent_pipeline(
            usernumb=usernumb,
            user_id=user_id,
            input_text=input_text,
            session_id=session_id,
            trace_id=trace_id,
            source="cron",
        )

        await _update_cron_status(cron_job_id, status="completed", error=None)
        log.info("定时任务执行成功", reply_len=len(reply))

    except asyncio.CancelledError:
        # arq 超时抛 CancelledError（不被 except Exception 捕获）
        log.warning("定时任务执行超时（arq job_timeout）")
        await _update_cron_status(cron_job_id, status="timeout", error="arq job_timeout exceeded")
        raise  # 必须 re-raise，让 arq 知道任务被取消

    except Exception as e:
        log.exception("定时任务执行失败")
        await _update_cron_status(cron_job_id, status="failed", error=str(e)[:500])

    finally:
        structlog.contextvars.unbind_contextvars("trace_id", "cron_job_id", "usernumb")


async def _update_cron_status(cron_job_id: str, status: str, error: str | None) -> None:
    """回写定时任务执行结果"""
    async with async_session() as session:
        values: dict = {"last_status": status, "last_error": error}
        if status == "failed":
            # ORM 表达式实现原子 +1
            values["fail_count"] = CronJob.fail_count + 1
        await session.execute(
            update(CronJob).where(CronJob.id == cron_job_id).values(**values)
        )
        await session.commit()
