"""
用户通知：DB 持久化 + Redis Pub/Sub 实时推送

双写策略：
1. 先写 DB（保证不丢）
2. 再 PUBLISH（加速在线用户感知）
PUBLISH 失败不影响 DB 记录，用户下次打开页面仍能看到。
"""

import json

import structlog
from uuid6 import uuid7

from app.cache.redis_client import RedisKeys, redis_client
from app.db.engine import async_session
from app.db.models.notification import Notification

log = structlog.get_logger()


class NotificationType:
    """通知类型常量，避免硬编码字符串散落各处"""

    CRON_COMPLETED = "cron_completed"
    CRON_FAILED = "cron_failed"
    SYSTEM_ANNOUNCE = "system_announce"  # 预留


async def notify_user(
    usernumb: str,
    *,
    notify_type: str,
    title: str,
    content: str | None = None,
    session_id: str | None = None,
    cron_job_id: str | None = None,
) -> None:
    """发送用户通知（DB + Pub/Sub 双写）

    Args:
        usernumb: 接收者工号
        notify_type: 通知类型（NotificationType 常量）
        title: 通知标题
        content: 通知详情（可选）
        session_id: 关联会话 ID（可选）
        cron_job_id: 关联定时任务 ID（可选）
    """
    notification_id = uuid7()

    # 1. 写 DB（持久化）
    try:
        async with async_session() as db:
            db.add(Notification(
                id=notification_id,
                usernumb=usernumb,
                type=notify_type,
                title=title,
                content=content,
                session_id=session_id,
                cron_job_id=cron_job_id,
            ))
            await db.commit()
    except Exception:
        log.exception("通知写入 DB 失败", usernumb=usernumb, notify_type=notify_type)
        return  # DB 失败则不推送（数据不一致比丢通知更糟）

    # 2. Redis PUBLISH（实时推送给在线用户）
    # 字段与 _serialize_notification() 保持一致，前端 SSE 收到的数据形状统一
    payload = {
        "id": str(notification_id),
        "type": notify_type,
        "title": title,
        "content": content,
        "session_id": session_id,
        "cron_job_id": cron_job_id,
        "is_read": False,
        "created_at": None,  # Pub/Sub 实时推送无需精确时间，前端可用本地时间
    }
    try:
        channel = RedisKeys.notify_channel(usernumb)
        await redis_client.publish(channel, json.dumps(payload, ensure_ascii=False))
    except Exception:
        # Pub/Sub 失败不影响已写入的 DB 记录
        log.warning("通知 PUBLISH 失败", usernumb=usernumb, exc_info=True)
