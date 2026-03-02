"""
/api/files — 文件下载端点

供 present_files 工具生成的下载 URL 使用。
路径严格限定在 SANDBOX_HOST_VOLUME/users/{user_id}/outputs/{session_id}/ 内，
防止路径穿越读取其他用户文件或系统文件。

端点：
- GET /api/files/download?path=users/{user_id}/outputs/{session_id}/filename
"""

import os
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import get_settings
from app.security.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/files", tags=["文件下载"])
log = structlog.get_logger()
settings = get_settings()


@router.get("/download")
async def download_file(
    path: str = Query(description="相对路径，格式：users/{user_id}/outputs/{session_id}/filename"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    下载 outputs 目录中的文件。

    path 参数为相对于 SANDBOX_HOST_VOLUME 的路径，
    调用方（present_files 工具）生成的 URL 格式为：
      /api/files/download?path=users/{user_id}/outputs/{session_id}/filename
    """
    # 只允许下载当前登录用户自己的 outputs 文件
    allowed_prefix = f"users/{user.usernumb}/outputs/"
    if not path.startswith(allowed_prefix):
        raise HTTPException(
            status_code=403,
            detail=f"只允许下载自己的 outputs 目录文件（期望前缀：{allowed_prefix}）",
        )

    # 拼接宿主机绝对路径并防止路径穿越
    host_root = Path(settings.SANDBOX_HOST_VOLUME)
    full_path = (host_root / path).resolve()

    # resolve() 后必须仍在 host_root 内
    try:
        full_path.relative_to(host_root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="路径穿越攻击，拒绝访问")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在：{path}")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="路径不是文件")

    log.info(
        "文件下载",
        path=path,
        usernumb=user.usernumb,
        size=full_path.stat().st_size,
    )

    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type="application/octet-stream",
    )
