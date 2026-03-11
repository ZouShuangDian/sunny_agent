"""
文件 API - File Preview and Download

端点：
- GET /api/files/download?path=...&expires=...&sig=... - HMAC 签名下载（present_files 工具生成）
- GET /api/files/{id}/preview - 获取文件预览信息（带临时下载 URL）
- GET /api/files/{id}/download - 下载文件（永久下载链接）

权限控制：
- RBAC：基于角色权限检查
- ABAC：公司数据隔离
- 文件上传者、项目所有者、超级管理员可以访问
- 超级管理员绕过所有权限检查
- /download 端点通过 HMAC 签名验证，无需 JWT
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.config import get_settings
from app.db.engine import get_db
from app.db.models.file import File
from app.db.models.project import Project
from app.db.models.user import User
from app.security.auth import AuthenticatedUser, get_current_user, get_current_user_optional, verify_download_token, is_super_admin
from app.security.download_sign import verify_download_sig
from app.services.file_service import FileService

router = APIRouter(prefix="/api/files", tags=["文件操作"])
log = structlog.get_logger()
settings = get_settings()


# ============ Pydantic 响应模型 ============

class FilePreviewResponse(BaseModel):
    """文件预览响应模型"""
    id: UUID
    file_name: str
    file_size: int
    mime_type: str
    file_extension: str
    description: str | None
    uploaded_by: UUID
    uploaded_at: str | None
    download_url: str
    expires_at: str
    
    class Config:
        from_attributes = True


# ============ 权限检查工具函数 ============

async def get_file_or_404(
    session: AsyncSession,
    file_id: UUID,
) -> File:
    """
    根据 ID 获取文件，不存在则抛出 404
    
    Args:
        session: 数据库会话
        file_id: 文件 ID
        
    Returns:
        File: 文件对象
        
    Raises:
        HTTPException: 404 文件不存在
    """
    stmt = select(File).where(File.id == file_id)
    result = await session.execute(stmt)
    file_record = result.scalar_one_or_none()
    
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    return file_record


async def check_file_access(
    file_record: File,
    user: AuthenticatedUser,
    session: AsyncSession,
) -> None:
    """
    检查用户是否有权访问文件
    
    权限规则（新）：
    - 文件上传者：可以访问自己上传的文件
    - 其他用户（包括超级管理员、项目所有者）：无权限
    
    Raises:
        HTTPException: 403 无权限
    """
    # 只有文件上传者可以访问自己的文件
    if str(file_record.uploaded_by) != user.id:
        raise HTTPException(status_code=403, detail="无权访问此文件")


# ============ API 端点 ============

@router.get("/{file_id}/preview")
async def preview_file(
    file_id: UUID,
    expires_in: int = Query(300, ge=60, le=3600, description="临时 URL 过期时间（秒，默认 5 分钟）"),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    获取文件预览信息（包含临时下载 URL）
    
    临时下载 URL 格式：/api/files/{id}/download?token={临时令牌}
    令牌使用 JWT 生成，有效期可配置（默认 5 分钟）
    
    权限规则：
    - 超级管理员：可以预览任何文件
    - 文件上传者：可以预览自己上传的文件
    - 项目所有者：可以预览项目内的文件
    - 同公司用户：可以预览同公司文件
    """
    try:
        # 获取文件
        file_record = await get_file_or_404(session, file_id)
        
        # 检查访问权限
        await check_file_access(file_record, user, session)
        
        # 生成临时下载令牌
        from app.security.auth import create_access_token
        
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_in)
        
        # 创建临时下载令牌（使用与expires_at一致的时间）
        download_token = create_access_token(
            sub=user.id,
            usernumb=user.usernumb,
            role=user.role,
            permissions=["file_download"],  # 限定权限为文件下载
            expires_in_seconds=expires_in,  # 使用相同的过期时间
        )
        
        # 构建临时下载 URL
        download_url = f"/api/files/{file_id}/download?token={download_token}"
        
        log.info(
            "获取文件预览",
            file_id=str(file_id),
            file_name=file_record.file_name,
            user_id=user.id,
            expires_in_seconds=expires_in,
        )
        
        return ok(data=FilePreviewResponse(
            id=file_record.id,
            file_name=file_record.file_name,
            file_size=file_record.file_size,
            mime_type=file_record.mime_type,
            file_extension=file_record.file_extension,
            description=file_record.description,
            uploaded_by=file_record.uploaded_by,
            uploaded_at=file_record.uploaded_at.isoformat() if file_record.uploaded_at else None,
            download_url=download_url,
            expires_at=expires_at.isoformat(),
        ))
        
    except HTTPException:
        raise
    except Exception as e:
        log.error("获取文件预览失败", error=str(e), file_id=str(file_id), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"获取文件预览失败: {e}")


@router.get("/{file_id}/download")
async def download_file_by_id(
    file_id: UUID,
    token: str | None = Query(None, description="临时下载令牌（可选）"),
    session: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser | None = Depends(get_current_user_optional),
):
    """
    通过文件 ID 下载文件（永久下载链接）
    
    支持两种方式：
    1. 使用临时下载令牌（从 preview 接口获取，优先）
    2. 使用当前登录态（JWT Token 认证）
    
    权限规则：
    - 超级管理员：可以下载任何文件
    - 文件上传者：可以下载自己上传的文件
    - 项目所有者：可以下载项目内的文件
    - 同公司用户：可以下载同公司文件
    """
    try:
        # 优先使用临时下载令牌
        if token:
            user = await verify_download_token(token)
        elif current_user:
            user = current_user
        else:
            raise HTTPException(
                status_code=401, 
                detail="需要提供有效的下载令牌或登录凭证"
            )
        
        # 获取文件
        file_record = await get_file_or_404(session, file_id)
        
        # 检查访问权限
        await check_file_access(file_record, user, session)
        
        # 获取文件绝对路径
        file_service = FileService(session)
        file_path = file_service.get_file_absolute_path(file_record)
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="文件不存在或已被删除")
        
        if not file_path.is_file():
            raise HTTPException(status_code=400, detail="路径不是文件")
        
        log.info(
            "下载文件",
            file_id=str(file_id),
            file_name=file_record.file_name,
            user_id=user.id,
            size=file_path.stat().st_size,
            via_token=bool(token),
        )
        
        return FileResponse(
            path=str(file_path),
            filename=file_record.file_name,
            media_type=file_record.mime_type or "application/octet-stream",
        )
        
    except HTTPException:
        raise
    except Exception as e:
        log.error("下载文件失败", error=str(e), file_id=str(file_id))
        raise HTTPException(status_code=500, detail=f"下载文件失败: {e}")


@router.get("/download")
async def download_file(
    path: str = Query(description="相对路径，格式：users/{user_id}/outputs/{session_id}/filename"),
    expires: int = Query(description="过期时间戳（Unix epoch 秒）"),
    sig: str = Query(description="HMAC-SHA256 签名"),
):
    """
    下载 outputs 目录中的文件（HMAC 签名校验，无需 JWT）。

    URL 由 present_files 工具在已鉴权的聊天请求中生成，
    包含 HMAC 签名 + 过期时间，浏览器直接点击即可下载。
    """
    # 1. 验证签名
    error = verify_download_sig(path, expires, sig)
    if error:
        raise HTTPException(status_code=403, detail=error)

    # 2. 路径格式校验（只允许 outputs 目录）
    if not path.startswith("users/") or "/outputs/" not in path:
        raise HTTPException(status_code=403, detail="只允许下载 outputs 目录中的文件")

    # 3. 拼接宿主机绝对路径并防止路径穿越
    host_root = Path(settings.SANDBOX_HOST_VOLUME)
    full_path = (host_root / path).resolve()

    try:
        full_path.relative_to(host_root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="路径越界，拒绝访问")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在：{path}")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="路径不是文件")

    log.info("文件下载", path=path, size=full_path.stat().st_size)

    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type="application/octet-stream",
    )
