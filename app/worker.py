"""
arq Worker 入口

启动命令：arq app.worker.WorkerSettings
与 api 进程共享同一套代码和 Docker 镜像，只是入口不同。
"""

import structlog
from arq import cron as arq_cron
from arq.connections import RedisSettings
from sqlalchemy import text

from app.config import get_settings
from app.cron.scanner import scan_and_enqueue
from app.tasks.cron_executor import execute_cron_job
from app.tasks.task_executor import execute_async_task

settings = get_settings()
log = structlog.get_logger()


async def startup(ctx: dict) -> None:
    """Worker 启动：初始化日志 + 预检连接"""
    from app.observability.logging_config import setup_logging

    setup_logging(env=settings.ENV)

    # 不要覆盖 ctx["redis"]！
    # arq 启动时自动将 ArqRedis 连接池存入 ctx["redis"]，用于 Scanner 入队。
    # pipeline.py 内部直接 import redis_client / async_session，不需要通过 ctx 中转。

    # 预检 PG + Redis 连接，快速失败
    from app.cache.redis_client import redis_client
    from app.db.engine import async_session

    async with async_session() as session:
        await session.execute(text("SELECT 1"))
    await redis_client.ping()

    log.info("Worker 启动完成", queue=settings.ARQ_QUEUE_NAME, max_jobs=settings.ARQ_MAX_JOBS)


async def shutdown(ctx: dict) -> None:
    """Worker 关闭：清理 DB 连接池"""
    from app.cache.redis_client import redis_client
    from app.db.engine import engine

    await engine.dispose()
    await redis_client.aclose()
    log.info("Worker 已关闭")


class WorkerSettings:
    """arq Worker 配置"""

    functions = [execute_cron_job, execute_async_task]
    cron_jobs = [
        arq_cron(
            scan_and_enqueue,
            minute=set(range(0, 60, settings.CRON_SCAN_INTERVAL)),  # 按配置间隔执行
            unique=True,             # 多实例只有一个执行（Redis 锁）
        ),
    ]

    on_startup = startup
    on_shutdown = shutdown

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = settings.ARQ_QUEUE_NAME
    max_jobs = settings.ARQ_MAX_JOBS
    job_timeout = settings.ARQ_JOB_TIMEOUT
    max_tries = settings.ARQ_MAX_TRIES
