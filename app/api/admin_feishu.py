"""
飞书应用管理 API
提供飞书应用的 CRUD 接口，供管理员动态维护
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.db.engine import get_db
from app.db.models.feishu import FeishuAccessConfig
from app.feishu.app_config import invalidate_app_secret_cache
from app.security.auth import AuthenticatedUser, get_current_user, is_super_admin

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/feishu/apps", tags=["admin-feishu"])


@router.get("", response_model=List[dict])
async def list_feishu_apps(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """列出所有飞书应用配置（仅超级管理员）"""
    # 检查管理员权限
    await is_super_admin(current_user)
    
    stmt = select(FeishuAccessConfig)
    if not include_inactive:
        stmt = stmt.where(FeishuAccessConfig.is_active == True)
    
    result = await db.execute(stmt)
    configs = result.scalars().all()
    
    return [
        {
            "id": str(config.id),
            "app_id": config.app_id,
            "is_active": config.is_active,
            "dm_policy": config.dm_policy,
            "group_policy": config.group_policy,
            "require_mention": config.require_mention,
            "created_at": config.created_at.isoformat() if config.created_at else None,
            "updated_at": config.updated_at.isoformat() if config.updated_at else None,
        }
        for config in configs
    ]


@router.get("/{app_id}")
async def get_feishu_app(
    app_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """获取单个飞书应用配置详情（仅超级管理员）"""
    # 检查管理员权限
    await is_super_admin(current_user)

    stmt = select(FeishuAccessConfig).where(FeishuAccessConfig.app_id == app_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App {app_id} not found"
        )
    
    return {
        "id": str(config.id),
        "app_id": config.app_id,
        "app_secret": "***" if config.app_secret else None,  # 不返回真实密钥
        "is_active": config.is_active,
        "dm_policy": config.dm_policy,
        "group_policy": config.group_policy,
        "dm_allowlist": config.dm_allowlist,
        "group_allowlist": config.group_allowlist,
        "require_mention": config.require_mention,
        "block_streaming_config": config.block_streaming_config,
        "debounce_config": config.debounce_config,
        "human_like_delay": config.human_like_delay,
        "encrypt_key": "***" if config.encrypt_key else None,
        "verification_token": "***" if config.verification_token else None,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_feishu_app(
    app_id: str,
    app_secret: str,
    dm_policy: str = "open",
    group_policy: str = "open",
    require_mention: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """创建新的飞书应用配置（仅超级管理员）"""
    # 检查管理员权限
    await is_super_admin(current_user)

    # 检查是否已存在
    stmt = select(FeishuAccessConfig).where(FeishuAccessConfig.app_id == app_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"App {app_id} already exists"
        )
    
    config = FeishuAccessConfig(
        app_id=app_id,
        app_secret=app_secret,
        dm_policy=dm_policy,
        group_policy=group_policy,
        require_mention=require_mention,
    )
    
    db.add(config)
    await db.commit()
    await db.refresh(config)
    
    logger.info("Created feishu app config", app_id=app_id, created_by=str(current_user.id))
    
    return {
        "id": str(config.id),
        "app_id": config.app_id,
        "is_active": config.is_active,
        "message": "App created successfully"
    }


@router.put("/{app_id}")
async def update_feishu_app(
    app_id: str,
    app_secret: Optional[str] = None,
    is_active: Optional[bool] = None,
    dm_policy: Optional[str] = None,
    group_policy: Optional[str] = None,
    require_mention: Optional[bool] = None,
    dm_allowlist: Optional[List[str]] = None,
    group_allowlist: Optional[List[str]] = None,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """更新飞书应用配置（仅超级管理员）"""
    # 检查管理员权限
    await is_super_admin(current_user)

    stmt = select(FeishuAccessConfig).where(FeishuAccessConfig.app_id == app_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App {app_id} not found"
        )
    
    # 更新字段
    if app_secret is not None:
        config.app_secret = app_secret
        # 清除缓存，强制重新加载
        await invalidate_app_secret_cache(app_id)
    if is_active is not None:
        config.is_active = is_active
        if not is_active:
            # 如果禁用应用，也清除缓存
            await invalidate_app_secret_cache(app_id)
    if dm_policy is not None:
        config.dm_policy = dm_policy
    if group_policy is not None:
        config.group_policy = group_policy
    if require_mention is not None:
        config.require_mention = require_mention
    if dm_allowlist is not None:
        config.dm_allowlist = dm_allowlist
    if group_allowlist is not None:
        config.group_allowlist = group_allowlist
    
    await db.commit()
    await db.refresh(config)
    
    logger.info("Updated feishu app config", app_id=app_id, updated_by=str(current_user.id))
    
    return {
        "id": str(config.id),
        "app_id": config.app_id,
        "is_active": config.is_active,
        "message": "App updated successfully"
    }


@router.delete("/{app_id}")
async def delete_feishu_app(
    app_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """删除（软删除）飞书应用配置（仅超级管理员）"""
    # 检查管理员权限
    await is_super_admin(current_user)

    stmt = select(FeishuAccessConfig).where(FeishuAccessConfig.app_id == app_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App {app_id} not found"
        )
    
    # 软删除：标记为不活跃
    config.is_active = False
    await db.commit()
    
    # 清除缓存
    await invalidate_app_secret_cache(app_id)
    
    logger.info("Deactivated feishu app config", app_id=app_id, deleted_by=str(current_user.id))
    
    return {"message": f"App {app_id} deactivated successfully"}