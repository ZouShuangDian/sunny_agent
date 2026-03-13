"""
定时任务 CRUD API

POST   /api/cron-jobs              创建
GET    /api/cron-jobs              列表（分页）
GET    /api/cron-jobs/executions   执行记录列表（分页，支持 status/cron_job_id 过滤）
GET    /api/cron-jobs/{id}         详情
PATCH  /api/cron-jobs/{id}         修改
DELETE /api/cron-jobs/{id}         删除
POST   /api/cron-jobs/{id}/run     立即执行一次
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.cron.service import CronJobLimitExceeded, CronService
from app.db.engine import get_db
from app.db.models.user import User
from app.security.auth import AuthenticatedUser, get_current_user
from app.tasks.arq_pool import get_arq_pool

log = structlog.get_logger()

router = APIRouter(prefix="/api/cron-jobs", tags=["cron-jobs"])


# -- Request/Response Schemas --


class CronJobCreate(BaseModel):
    """创建定时任务请求"""
    name: str = Field(..., max_length=200, description="任务名称")
    description: str | None = Field(None, description="任务描述")
    cron_expr: str = Field(..., max_length=100, description="标准 5 字段 Cron 表达式")
    timezone: str = Field("Asia/Shanghai", max_length=50)
    input_text: str = Field(..., description="投喂给 Agent 的用户消息")
    session_id: str | None = Field(None, max_length=64, description="结果推送到哪个会话")
    expires_at: datetime | None = Field(None, description="到期日期（可选），到期后自动禁用")


class CronJobUpdate(BaseModel):
    """修改定时任务请求（所有字段可选）"""
    name: str | None = Field(None, max_length=200)
    description: str | None = None
    cron_expr: str | None = Field(None, max_length=100)
    timezone: str | None = Field(None, max_length=50)
    input_text: str | None = None
    session_id: str | None = None
    enabled: bool | None = None
    expires_at: datetime | None = None


def _serialize_execution(exe) -> dict[str, Any]:
    """CronJobExecution ORM 对象 -> JSON 可序列化 dict"""
    return {
        "id": str(exe.id),
        "cron_job_id": str(exe.cron_job_id),
        "name": exe.name,
        "input_text": exe.input_text[:200] if exe.input_text else None,
        "session_id": exe.session_id,
        "status": exe.status,
        "error_message": exe.error_message,
        "started_at": exe.started_at.isoformat() if exe.started_at else None,
        "completed_at": exe.completed_at.isoformat() if exe.completed_at else None,
    }


def _serialize_job(job) -> dict[str, Any]:
    """CronJob ORM 对象 -> JSON 可序列化 dict"""
    return {
        "id": str(job.id),
        "usernumb": job.usernumb,
        "name": job.name,
        "description": job.description,
        "cron_expr": job.cron_expr,
        "timezone": job.timezone,
        "input_text": job.input_text,
        "session_id": job.session_id,
        "enabled": job.enabled,
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
        "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
        "last_status": job.last_status,
        "last_error": job.last_error,
        "run_count": job.run_count,
        "fail_count": job.fail_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


# -- Endpoints --


@router.post("")
async def create_cron_job(
    body: CronJobCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建定时任务"""
    service = CronService(db)
    try:
        job = await service.create(
            usernumb=user.usernumb,
            name=body.name,
            cron_expr=body.cron_expr,
            input_text=body.input_text,
            description=body.description,
            timezone_str=body.timezone,
            session_id=body.session_id,
            expires_at=body.expires_at,
        )
    except CronJobLimitExceeded as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ok(data=_serialize_job(job), message="定时任务创建成功", status_code=201)


@router.get("")
async def list_cron_jobs(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询当前用户的定时任务列表（分页）"""
    service = CronService(db)
    items, total = await service.list_by_user(user.usernumb, offset=offset, limit=limit)

    return ok(data={
        "items": [_serialize_job(j) for j in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    })


@router.get("/executions")
async def list_executions(
    status: str | None = Query(None, description="按状态过滤：completed/failed/timeout/running"),
    cron_job_id: str | None = Query(None, description="按定时任务 ID 过滤"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询定时任务执行记录列表（分页）"""
    service = CronService(db)
    items, total = await service.list_executions(
        user.usernumb, status=status, cron_job_id=cron_job_id,
        offset=offset, limit=limit,
    )

    return ok(data={
        "items": [_serialize_execution(e) for e in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    })


@router.get("/{job_id}")
async def get_cron_job(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询定时任务详情"""
    service = CronService(db)
    job = await service.get_by_id(job_id, user.usernumb)
    if not job:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    return ok(data=_serialize_job(job))


@router.patch("/{job_id}")
async def update_cron_job(
    job_id: str,
    body: CronJobUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改定时任务"""
    service = CronService(db)

    # 只传非 None 字段
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="请提供至少一个要修改的字段")

    try:
        job = await service.update(job_id, user.usernumb, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not job:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    return ok(data=_serialize_job(job), message="定时任务更新成功")


@router.delete("/{job_id}")
async def delete_cron_job(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除定时任务"""
    service = CronService(db)
    deleted = await service.delete(job_id, user.usernumb)
    if not deleted:
        raise HTTPException(status_code=404, detail="定时任务不存在或无权删除")

    return ok(message="定时任务已删除")


@router.post("/{job_id}/run")
async def run_cron_job_now(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """立即执行一次定时任务（入队到 Worker，不影响原有调度周期）"""
    service = CronService(db)
    job = await service.get_by_id(job_id, user.usernumb)
    if not job:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    # 查 user.id（UUID），execute_cron_job 需要
    from sqlalchemy import select as sa_select
    result = await db.execute(
        sa_select(User.id).where(User.usernumb == user.usernumb)
    )
    user_row = result.scalar_one_or_none()
    if not user_row:
        raise HTTPException(status_code=500, detail="用户记录异常")

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "execute_cron_job",
        cron_job_id=str(job.id),
        usernumb=job.usernumb,
        user_id=str(user_row),
        input_text=job.input_text,
        session_id=job.session_id,
        name=job.name,
    )

    log.info("定时任务手动触发执行", cron_job_id=str(job.id), usernumb=user.usernumb)
    return ok(message=f"定时任务「{job.name}」已触发执行")
