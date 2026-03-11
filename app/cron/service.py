"""
CronService：定时任务 CRUD 业务逻辑
"""

from datetime import datetime, timezone

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.cron.utils import calc_next_run, check_min_interval, validate_cron_expr, validate_timezone
from app.db.models.cron_job import CronJob

log = structlog.get_logger()
settings = get_settings()


class CronJobLimitExceeded(Exception):
    """用户定时任务数超过配额"""


class CronService:
    """定时任务 CRUD"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        usernumb: str,
        name: str,
        cron_expr: str,
        input_text: str,
        description: str | None = None,
        timezone_str: str = "Asia/Shanghai",
        session_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> CronJob:
        """创建定时任务

        校验项：
        1. cron_expr 合法
        2. timezone 合法
        3. 用户任务数 < MAX_CRON_JOBS_PER_USER
        """
        if not validate_cron_expr(cron_expr):
            raise ValueError(f"无效的 Cron 表达式：{cron_expr}")

        if not validate_timezone(timezone_str):
            raise ValueError(f"无效的时区：{timezone_str}")

        # 校验最小触发间隔
        if not check_min_interval(cron_expr, settings.CRON_MIN_INTERVAL_MINUTES):
            raise ValueError(
                f"定时任务触发间隔不得低于 {settings.CRON_MIN_INTERVAL_MINUTES} 分钟"
            )

        # 校验用户任务数上限
        count_result = await self.db.execute(
            select(func.count()).select_from(CronJob).where(CronJob.usernumb == usernumb)
        )
        current_count = count_result.scalar_one()
        if current_count >= settings.MAX_CRON_JOBS_PER_USER:
            raise CronJobLimitExceeded(
                f"每个用户最多创建 {settings.MAX_CRON_JOBS_PER_USER} 个定时任务，"
                f"当前已有 {current_count} 个"
            )

        next_run = calc_next_run(cron_expr, timezone_str)

        job = CronJob(
            usernumb=usernumb,
            name=name,
            description=description,
            cron_expr=cron_expr,
            timezone=timezone_str,
            input_text=input_text,
            session_id=session_id,
            next_run_at=next_run,
            expires_at=expires_at,
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)

        log.info("定时任务已创建", cron_job_id=str(job.id), name=name, usernumb=usernumb)
        return job

    async def list_by_user(
        self, usernumb: str, *, offset: int = 0, limit: int = 20
    ) -> tuple[list[CronJob], int]:
        """查询用户的定时任务列表（分页）"""
        # 总数
        count_result = await self.db.execute(
            select(func.count()).select_from(CronJob).where(CronJob.usernumb == usernumb)
        )
        total = count_result.scalar_one()

        # 分页列表
        result = await self.db.execute(
            select(CronJob)
            .where(CronJob.usernumb == usernumb)
            .order_by(CronJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list(result.scalars().all())

        return items, total

    async def get_by_id(self, job_id: str, usernumb: str) -> CronJob | None:
        """按 ID 查询（限定当前用户）"""
        result = await self.db.execute(
            select(CronJob).where(CronJob.id == job_id, CronJob.usernumb == usernumb)
        )
        return result.scalar_one_or_none()

    async def update(
        self,
        job_id: str,
        usernumb: str,
        **fields,
    ) -> CronJob | None:
        """修改定时任务

        可修改字段：name, description, cron_expr, timezone, input_text, session_id, enabled
        修改 cron_expr / timezone / enabled(True) 时自动重算 next_run_at
        """
        job = await self.get_by_id(job_id, usernumb)
        if not job:
            return None

        # 字段校验
        if "cron_expr" in fields and not validate_cron_expr(fields["cron_expr"]):
            raise ValueError(f"无效的 Cron 表达式：{fields['cron_expr']}")
        if "timezone" in fields and not validate_timezone(fields["timezone"]):
            raise ValueError(f"无效的时区：{fields['timezone']}")
        if "cron_expr" in fields and not check_min_interval(
            fields["cron_expr"], settings.CRON_MIN_INTERVAL_MINUTES
        ):
            raise ValueError(
                f"定时任务触发间隔不得低于 {settings.CRON_MIN_INTERVAL_MINUTES} 分钟"
            )

        # 逐字段更新（不过滤 None，允许清空 session_id / description 等 nullable 字段）
        allowed_fields = {"name", "description", "cron_expr", "timezone", "input_text", "session_id", "enabled", "expires_at"}
        for key, value in fields.items():
            if key in allowed_fields:
                setattr(job, key, value)

        # 需要重算 next_run_at 的场景
        need_recalc = (
            "cron_expr" in fields
            or "timezone" in fields
            or ("enabled" in fields and fields["enabled"] is True)
        )
        if need_recalc and job.enabled:
            job.next_run_at = calc_next_run(job.cron_expr, job.timezone)

        await self.db.commit()
        await self.db.refresh(job)

        log.info("定时任务已更新", cron_job_id=str(job.id), updated_fields=list(fields.keys()))
        return job

    async def delete(self, job_id: str, usernumb: str) -> bool:
        """删除定时任务（硬删除，只能删自己的）"""
        result = await self.db.execute(
            delete(CronJob).where(CronJob.id == job_id, CronJob.usernumb == usernumb)
        )
        await self.db.commit()
        deleted = result.rowcount > 0
        if deleted:
            log.info("定时任务已删除", cron_job_id=job_id, usernumb=usernumb)
        return deleted
