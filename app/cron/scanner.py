"""
Cron Scanner：每分钟扫描 DB 中到期的定时任务，原子推进 next_run_at 后批量入队执行。

设计要点（v1.3 架构评审确认）：
- FOR UPDATE SKIP LOCKED（of=CronJob）：多实例安全，且不锁 User 行
- 真实 next_run_at 在事务内计算：崩溃后不会卡在占位值
- run_count 使用 SQL 表达式原子 +1
- JOIN users 过滤无效用户
- Scanner 入队用 arq 内置池（ctx["redis"]），pipeline 用项目 redis_client
"""

from datetime import datetime, timedelta, timezone

import structlog
from arq.connections import ArqRedis
from sqlalchemy import delete, or_, select, update

from app.config import get_settings
from app.cron.utils import calc_next_run
from app.db.engine import async_session
from app.db.models.cron_job import CronJob
from app.db.models.notification import Notification
from app.db.models.user import User

_settings = get_settings()

log = structlog.get_logger()


async def scan_and_enqueue(ctx: dict) -> None:
    """每分钟执行：查询到期的 cron_jobs，原子推进 next_run_at，批量入队"""
    now = datetime.now(timezone.utc)
    jobs_to_enqueue: list[dict] = []

    async with async_session() as session:
        async with session.begin():
            # JOIN users 过滤已删除/禁用用户
            # 同时查出 user.id（UUID），供 run_agent_pipeline 使用
            result = await session.execute(
                select(CronJob, User.id.label("user_id"))
                .join(User, User.usernumb == CronJob.usernumb)
                .where(
                    CronJob.enabled == True,  # noqa: E712
                    CronJob.next_run_at <= now,
                    User.is_active == True,  # noqa: E712
                )
                # of=CronJob：只锁 CronJob 行，避免多 Scanner 互锁 User 行
                .with_for_update(skip_locked=True, of=CronJob)
            )
            rows = result.all()

            for job, user_id in rows:
                # 到期检查：expires_at 已过则自动禁用，不入队执行
                if job.expires_at and job.expires_at <= now:
                    await session.execute(
                        update(CronJob)
                        .where(CronJob.id == job.id)
                        .values(enabled=False)
                    )
                    log.info("定时任务已到期，自动禁用", cron_job_id=str(job.id), name=job.name)
                    continue

                real_next = calc_next_run(job.cron_expr, job.timezone, after=now)

                # SQL 表达式原子 +1（而非 Python 级 +=1，避免并发丢计数）
                await session.execute(
                    update(CronJob)
                    .where(CronJob.id == job.id)
                    .values(
                        next_run_at=real_next,
                        last_run_at=now,
                        run_count=CronJob.run_count + 1,
                        last_status="running",
                        last_error=None,
                    )
                )

                jobs_to_enqueue.append({
                    "cron_job_id": str(job.id),
                    "usernumb": job.usernumb,
                    "user_id": str(user_id),
                    "input_text": job.input_text,
                    "session_id": job.session_id,
                    "name": job.name,
                })

            # 事务提交：所有 next_run_at 已原子推进

    # 事务提交后批量入队
    if jobs_to_enqueue:
        log.info("Cron Scanner 发现到期任务", count=len(jobs_to_enqueue))

        # arq 启动后自动将 ArqRedis 池存入 ctx["redis"]
        pool: ArqRedis = ctx["redis"]
        for item in jobs_to_enqueue:
            try:
                await pool.enqueue_job("execute_cron_job", **item)
                log.info("定时任务已入队", cron_job_id=item["cron_job_id"])
            except Exception:
                log.exception("定时任务入队失败", cron_job_id=item["cron_job_id"])

    # 独立事务清理过期通知（不影响主逻辑，异常只记日志）
    try:
        await _cleanup_expired_notifications(now)
    except Exception:
        log.warning("通知清理异常", exc_info=True)


async def _cleanup_expired_notifications(now: datetime) -> None:
    """清理过期已读通知（独立事务，幂等，多实例执行无副作用）"""
    cutoff = now - timedelta(days=_settings.NOTIFICATION_RETENTION_DAYS)
    async with async_session() as session:
        result = await session.execute(
            delete(Notification)
            .where(
                Notification.is_read == True,  # noqa: E712
                Notification.created_at < cutoff,
            )
        )
        await session.commit()
        if result.rowcount:
            log.info("已清理过期通知", count=result.rowcount)
