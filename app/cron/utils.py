"""
Cron 工具函数：表达式解析、下次触发时间计算
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from croniter import croniter

log = structlog.get_logger()

_FIXED_TZ_FALLBACKS = {
    "UTC": timezone.utc,
    "Etc/UTC": timezone.utc,
    "Asia/Shanghai": timezone(timedelta(hours=8)),
}


def get_timezone_info(timezone_name: str):
    """Resolve an IANA timezone with fixed-offset fallbacks for Windows without tzdata."""
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        fallback = _FIXED_TZ_FALLBACKS.get(timezone_name)
        if fallback is None:
            raise
        log.warning("Timezone data not found, using fixed offset fallback", timezone=timezone_name)
        return fallback


def calc_next_run(
    cron_expr: str,
    timezone: str = "Asia/Shanghai",
    after: datetime | None = None,
) -> datetime:
    """根据 cron 表达式计算下一次触发时间（返回 UTC）

    Args:
        cron_expr: 标准 5 字段 Cron 表达式（分 时 日 月 周）
        timezone: 用户时区
        after: 从该时间之后计算（默认当前时间）

    Returns:
        下一次触发时间（UTC timezone-aware）
    """
    tz = get_timezone_info(timezone)
    base = after.astimezone(tz) if after else datetime.now(tz)
    cron = croniter(cron_expr, base)
    next_local = cron.get_next(datetime)
    return next_local.astimezone(get_timezone_info("UTC"))


def validate_cron_expr(cron_expr: str) -> bool:
    """校验 cron 表达式是否合法"""
    return croniter.is_valid(cron_expr)


def check_min_interval(cron_expr: str, min_minutes: int) -> bool:
    """检查 cron 表达式的最小触发间隔是否 >= min_minutes

    通过 croniter 计算连续两次触发时间的间距来判断。
    """
    base = datetime.now(get_timezone_info("UTC"))
    cron = croniter(cron_expr, base)
    first = cron.get_next(datetime)
    second = cron.get_next(datetime)
    interval_minutes = (second - first).total_seconds() / 60
    return interval_minutes >= min_minutes


def validate_timezone(timezone: str) -> bool:
    """校验时区是否合法"""
    try:
        get_timezone_info(timezone)
        return True
    except (KeyError, ValueError, ZoneInfoNotFoundError):
        return False
