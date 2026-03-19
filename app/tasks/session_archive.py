from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from app.config import get_settings
from app.cron.utils import get_timezone_info
from app.db.engine import async_session
from app.feishu.session_archive_service import FeishuSessionArchiveService
from app.llm.client import LLMClient
from app.memory.chat_persistence import ChatPersistence

log = structlog.get_logger()
settings = get_settings()


def _compute_cutoff_at(now_utc: datetime, cutoff_hour: int, cutoff_minute: int) -> datetime:
    local_now = now_utc.astimezone(get_timezone_info("Asia/Shanghai"))
    cutoff_local = local_now.replace(
        hour=cutoff_hour,
        minute=cutoff_minute,
        second=0,
        microsecond=0,
    )
    if local_now < cutoff_local:
        cutoff_local -= timedelta(days=1)
    return cutoff_local.astimezone(timezone.utc)


async def archive_feishu_sessions(ctx: dict) -> dict:
    if not settings.FEISHU_SESSION_ARCHIVE_ENABLED:
        return {"status": "disabled"}

    cutoff_at = _compute_cutoff_at(
        datetime.now(timezone.utc),
        settings.FEISHU_SESSION_ARCHIVE_CUTOFF_HOUR,
        settings.FEISHU_SESSION_ARCHIVE_CUTOFF_MINUTE,
    )

    llm = LLMClient()
    persistence = ChatPersistence(async_session)
    service = FeishuSessionArchiveService(llm=llm, persistence=persistence)

    async with async_session() as db:
        stats = await service.archive_due_sessions(
            db,
            cutoff_at=cutoff_at,
            limit=settings.FEISHU_SESSION_ARCHIVE_BATCH_SIZE,
        )

    result = {
        "status": "completed",
        "cutoff_at": cutoff_at.isoformat(),
        "scanned": stats.scanned,
        "archived": stats.archived,
        "skipped": stats.skipped,
        "inherited": stats.inherited,
    }
    log.info("Feishu session archive task completed", **result)
    return result
