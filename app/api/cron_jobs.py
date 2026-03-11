"""
定时任务 CRUD API

POST   /api/cron-jobs          创建
GET    /api/cron-jobs          列表（分页）
GET    /api/cron-jobs/{id}     详情
PATCH  /api/cron-jobs/{id}     修改
DELETE /api/cron-jobs/{id}     删除
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.cron.service import CronJobLimitExceeded, CronService
from app.db.engine import get_db
from app.security.auth import AuthenticatedUser, get_current_user

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


class CronJobUpdate(BaseModel):
    """修改定时任务请求（所有字段可选）"""
    name: str | None = Field(None, max_length=200)
    description: str | None = None
    cron_expr: str | None = Field(None, max_length=100)
    timezone: str | None = Field(None, max_length=50)
    input_text: str | None = None
    session_id: str | None = None
    enabled: bool | None = None


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
