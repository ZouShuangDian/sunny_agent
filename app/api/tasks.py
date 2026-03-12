"""
异步任务管理 API（查询 + 取消）

GET    /api/tasks            查询当前用户的任务列表
GET    /api/tasks/{task_id}  查询任务详情
DELETE /api/tasks/{task_id}  取消任务（仅 pending 状态）

注意：无 POST 创建端点——任务创建由 Agent 通过 create_task tool 完成。
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.db.engine import get_db
from app.db.models.async_task import AsyncTask
from app.security.auth import AuthenticatedUser, get_current_user

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
