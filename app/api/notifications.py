"""
通知中心 API

GET    /api/notifications              通知列表（分页）
GET    /api/notifications/unread-count  未读计数
PATCH  /api/notifications/{id}/read     标记单条已读
PATCH  /api/notifications/read-all      全部标记已读
GET    /api/notifications/stream        SSE 实时推送（query param 鉴权）
"""

from __future__ import annotations

import json

import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.cache.redis_client import RedisKeys, redis_client
from app.config import get_settings
from app.db.engine import async_session, get_db
from app.db.models.notification import Notification
from app.security.auth import AuthenticatedUser, get_current_user

log = structlog.get_logger()
_settings = get_settings()

router = APIRouter(prefix="/api/notifications", tags=["通知中心"])


# ── 辅助函数 ──


def _serialize_notification(n: Notification) -> dict:
    """ORM → dict，字段与 notify_user() 的 Pub/Sub payload 保持一致"""
    return {
        "id": str(n.id),
        "type": n.type,
        "title": n.title,
        "content": n.content,
        "session_id": n.session_id,
        "cron_job_id": n.cron_job_id,
        "task_id": n.task_id,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


async def _verify_sse_token(request: Request) -> str:
    """SSE 端点鉴权：从 query param 提取并验证 JWT，返回 usernumb

    浏览器 EventSource 不支持自定义 Header，因此 JWT 通过 query param 传递。
    """
    token = request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="缺少 token 参数")

    try:
        payload = jwt.decode(
            token, _settings.JWT_SECRET, algorithms=[_settings.JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效 Token")

    # 检查黑名单
    jti = payload.get("jti")
    if jti and await redis_client.exists(RedisKeys.token_blacklist(jti)):
        raise HTTPException(status_code=401, detail="Token 已注销")

    usernumb = payload.get("usernumb")
    if not usernumb:
        raise HTTPException(status_code=401, detail="Token 缺少 usernumb")

    return usernumb


# ── REST 端点 ──


@router.get("")
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """通知列表（分页），同时返回未读计数"""
    offset = (page - 1) * page_size

    # 查总数
    total_result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.usernumb == user.usernumb)
    )
    total = total_result.scalar() or 0

    # 查未读数
    unread_result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.usernumb == user.usernumb,
            Notification.is_read == False,  # noqa: E712
        )
    )
    unread_count = unread_result.scalar() or 0

    # 查列表
    result = await db.execute(
        select(Notification)
        .where(Notification.usernumb == user.usernumb)
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = result.scalars().all()

    return ok(data={
        "items": [_serialize_notification(n) for n in items],
        "total": total,
        "unread_count": unread_count,
    })


@router.get("/unread-count")
async def get_unread_count(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """未读通知计数（轻量查询，前端可高频调用）"""
    result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.usernumb == user.usernumb,
            Notification.is_read == False,  # noqa: E712
        )
    )
    count = result.scalar() or 0
    return ok(data={"count": count})


@router.patch("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """标记单条通知为已读"""
    result = await db.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.usernumb == user.usernumb,
        )
        .values(is_read=True)
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="通知不存在")

    return ok(message="已标记为已读")


@router.patch("/read-all")
async def mark_all_read(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """一键全部标记已读"""
    result = await db.execute(
        update(Notification)
        .where(
            Notification.usernumb == user.usernumb,
            Notification.is_read == False,  # noqa: E712
        )
        .values(is_read=True)
    )
    await db.commit()

    return ok(message="已全部标记为已读", data={"count": result.rowcount})


# ── SSE 端点 ──


@router.get("/stream")
async def notification_stream(request: Request):
    """SSE 实时推送通知

    鉴权方式：query param ?token={jwt_token}
    前端示例：new EventSource('/api/notifications/stream?token=' + accessToken)
    """
    usernumb = await _verify_sse_token(request)

    return StreamingResponse(
        _event_generator(usernumb),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 禁用缓冲
        },
    )


async def _event_generator(usernumb: str):
    """SSE 事件生成器

    流程：先 subscribe → 查 DB 未读推送（断线补偿）→ 循环监听 Pub/Sub
    注意：先 subscribe 再查 DB 会导致短暂重叠，前端需按 id 去重。

    Pub/Sub 使用独立 Redis 连接（不占用主连接池），避免 SSE 长连接耗尽连接池。
    """
    import redis.asyncio as aioredis

    channel = RedisKeys.notify_channel(usernumb)
    heartbeat_seconds = _settings.SSE_HEARTBEAT_SECONDS

    # 创建独立 Redis 连接用于 Pub/Sub（不经过主连接池，SSE 长连接不会占用池槽位）
    dedicated_conn = aioredis.from_url(
        _settings.REDIS_URL,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )
    pubsub = dedicated_conn.pubsub()

    try:
        # 先 subscribe 再查 DB，消除通知丢失窗口
        await pubsub.subscribe(channel)

        # 查询并推送未读通知（断线补偿）
        async with async_session() as db:
            result = await db.execute(
                select(Notification)
                .where(
                    Notification.usernumb == usernumb,
                    Notification.is_read == False,  # noqa: E712
                )
                .order_by(Notification.created_at.desc())
                .limit(20)
            )
            unread = result.scalars().all()
            for n in reversed(unread):
                data = json.dumps(_serialize_notification(n), ensure_ascii=False)
                yield f"data: {data}\n\n"

        # 监听 Redis Pub/Sub 接收新通知
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=heartbeat_seconds,
            )
            if message and message["type"] == "message":
                # redis decode_responses=True，message['data'] 已经是 str
                raw = message["data"]
                payload = raw if isinstance(raw, str) else raw.decode("utf-8")
                yield f"data: {payload}\n\n"
            else:
                yield ": heartbeat\n\n"

    finally:
        # 保证资源释放：先退订，再关闭 pubsub 和独立连接
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await dedicated_conn.aclose()
