"""
异步任务管理 API（查询 + 取消 + 进度流）

GET    /api/tasks                    查询当前用户的任务列表
GET    /api/tasks/{task_id}          查询任务详情
GET    /api/tasks/{task_id}/stream   SSE 推送任务进度（断线续传）
GET    /api/tasks/{task_id}/status   查询任务状态（断线重连兜底）
DELETE /api/tasks/{task_id}          取消任务（仅 pending 状态）

注意：无 POST 创建端点——任务创建由 Agent 通过 create_task tool 完成。
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.api.notifications import _verify_sse_token
from app.cache.redis_client import RedisKeys, redis_client
from app.config import get_settings
from app.db.engine import async_session, get_db
from app.db.models.async_task import AsyncTask
from app.security.auth import AuthenticatedUser, get_current_user
from app.streaming.events import SSEEvent, format_sse

router = APIRouter(prefix="/api/tasks", tags=["异步任务"])


def _serialize_task(t: AsyncTask, *, detail: bool = False) -> dict:
    """AsyncTask ORM 对象 → JSON 可序列化 dict

    Args:
        detail: True 时返回完整 input_text，False 时截断为前 200 字符
    """
    return {
        "task_id": str(t.id),
        "task_type": t.task_type,
        "status": t.status,
        "input_text": t.input_text if detail else t.input_text[:200],
        "result_summary": t.result_summary,
        "error_message": t.error_message,
        "metadata": t.metadata_,
        "session_id": t.session_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


@router.get("")
async def list_tasks(
    status: str | None = Query(None, description="按状态筛选"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询当前用户的任务列表"""
    # 基础过滤条件
    base_where = [AsyncTask.usernumb == user.usernumb]
    if status:
        base_where.append(AsyncTask.status == status)

    # 总数查询
    total = await db.scalar(
        select(func.count()).select_from(AsyncTask).where(*base_where)
    )

    # 分页查询
    stmt = (
        select(AsyncTask)
        .where(*base_where)
        .order_by(AsyncTask.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    return ok(data={
        "items": [_serialize_task(t) for t in tasks],
        "total": total,
        "offset": offset,
        "limit": limit,
    })


@router.get("/{task_id}")
async def get_task(
    task_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询单个任务详情"""
    task = await db.get(AsyncTask, task_id)
    if not task or task.usernumb != user.usernumb:
        raise HTTPException(404, "任务不存在")

    return ok(data=_serialize_task(task, detail=True))


@router.get("/{task_id}/stream")
async def task_stream(
    task_id: uuid.UUID,
    request: Request,
    last_event_id: int = Query(0, description="上次收到的最后一个 event_id，断线续传用"),
):
    """SSE 推送异步任务进度，支持断线续传

    鉴权方式：query param ?token={jwt_token}（EventSource 不支持自定义 Header）
    前端示例：new EventSource('/api/tasks/{id}/stream?token=' + accessToken)

    混合模式：先从 Redis List 补发历史事件，再监听 Pub/Sub 实时推送。
    使用独立 Redis 连接（不占共享连接池），与通知 SSE 架构一致。
    """
    usernumb = await _verify_sse_token(request)

    async def generator():
        event_key = RedisKeys.task_events(str(task_id))
        settings = get_settings()

        # 独立 Redis 连接：SSE 长连接会长期占用槽位，不应使用共享池
        dedicated_conn = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True,
            socket_connect_timeout=5, socket_keepalive=True,
        )
        pubsub = dedicated_conn.pubsub()
        await pubsub.subscribe(RedisKeys.notify_channel(usernumb))

        try:
            # 1. 补发历史事件（Redis List，断线续传）
            # event_id == list_index + 1：lrange(key, N, -1) 跳过前 N 个已收事件
            cursor = last_event_id
            history = await redis_client.lrange(event_key, cursor, -1)
            for raw in history:
                event = json.loads(raw)
                cursor += 1
                yield format_sse(event["type"], event)
                if event["type"] in ("done", "error"):
                    return

            # 2. 实时监听 Pub/Sub 新事件
            async for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                data = json.loads(msg["data"])
                if data.get("type") != "task_progress" or data.get("task_id") != str(task_id):
                    continue
                yield format_sse(data["type"], data)
                if data["type"] in ("done", "error"):
                    return

        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()
            await dedicated_conn.aclose()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/{task_id}/status")
async def task_status(
    task_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """查询任务当前状态（断线重连兜底）

    统一先查 DB 校验任务归属（防止水平越权），再决定从哪里读状态。
    """
    # 归属校验
    async with async_session() as db:
        result = await db.execute(
            select(AsyncTask.status).where(
                AsyncTask.id == task_id,
                AsyncTask.usernumb == user.usernumb,
            )
        )
        db_status = result.scalar_one_or_none()

    if db_status is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    # Redis List 还在 → 读最后一条事件（含 last_event_id，供断线续传）
    event_key = RedisKeys.task_events(str(task_id))
    if await redis_client.exists(event_key):
        last_event = await redis_client.lindex(event_key, -1)
        if last_event:
            event = json.loads(last_event)
            return ok(data={
                "status": event["type"],
                "last_event_id": event["event_id"],
            })

    # Redis List 不在（TTL 过期）→ 直接用 DB 状态
    return ok(data={"status": db_status})


@router.delete("/{task_id}")
async def cancel_task(
    task_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """取消任务（仅 pending 状态）"""
    # 先验证任务存在且属于当前用户
    task = await db.get(AsyncTask, task_id)
    if not task or task.usernumb != user.usernumb:
        raise HTTPException(404, "任务不存在")

    # 原子取消：UPDATE WHERE status='pending'，rowcount=0 表示已被 Worker 抢走
    result = await db.execute(
        update(AsyncTask)
        .where(
            AsyncTask.id == task_id,
            AsyncTask.status == "pending",
        )
        .values(status="cancelled", completed_at=datetime.now(timezone.utc))
    )
    if result.rowcount == 0:
        raise HTTPException(409, f"任务状态已变更为 {task.status}，无法取消")
    await db.commit()

    return ok(message="任务已取消")
