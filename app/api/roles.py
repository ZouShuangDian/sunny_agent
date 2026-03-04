"""
角色管理 API
"""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models.user import Role
from app.security.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/roles", tags=["角色管理"])
log = structlog.get_logger()


class RoleCreateSchema(BaseModel):
    """创建角色请求模型"""
    name: str = Field(..., min_length=1, max_length=32, description="角色名")
    permissions: list[str] = Field(default_factory=list, description="权限列表")
    description: str | None = Field(None, description="角色描述")


class RoleUpdateSchema(BaseModel):
    """更新角色请求模型"""
    name: str | None = Field(None, min_length=1, max_length=32, description="角色名")
    permissions: list[str] | None = Field(None, description="权限列表")
    description: str | None = Field(None, description="角色描述")


class RolePermissionsSchema(BaseModel):
    """更新角色权限请求模型"""
    permissions: list[str] = Field(..., description="权限列表")


class RoleResponse(BaseModel):
    """角色响应模型"""
    id: UUID
    name: str
    permissions: list[str]
    description: str | None = None
    created_at: str
    
    class Config:
        from_attributes = True


class RoleListResponse(BaseModel):
    """角色列表响应模型"""
    items: list[RoleResponse]
    total: int


def require_admin(user: AuthenticatedUser) -> None:
    """检查用户是否为管理员"""
    if "admin" not in user.permissions:
        raise HTTPException(status_code=403, detail="权限不足：需要管理员权限")


async def get_role_by_id(session, role_id: UUID) -> Role:
    """根据 ID 获取角色，不存在则抛出 404"""
    result = await session.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    return role


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_role(
    role_data: RoleCreateSchema,
    session=Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> RoleResponse:
    """创建角色（仅管理员）"""
    require_admin(current_user)
    
    # 检查角色名是否已存在
    result = await session.execute(select(Role).where(Role.name == role_data.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="角色名已存在")
    
    # 创建角色
    role = Role(
        name=role_data.name,
        permissions=role_data.permissions,
        description=role_data.description,
    )
    
    session.add(role)
    await session.commit()
    await session.refresh(role)
    
    log.info("创建角色", role_name=role.name, creator=current_user.usernumb)
    
    return role_to_response(role)


@router.get("")
async def list_roles(
    session=Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> RoleListResponse:
    """获取所有角色"""
    result = await session.execute(select(Role).order_by(Role.name))
    roles = result.scalars().all()
    
    return RoleListResponse(
        items=[role_to_response(r) for r in roles],
        total=len(roles),
    )


@router.get("/{role_id}")
async def get_role(
    role_id: UUID,
    session=Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> RoleResponse:
    """获取角色详情"""
    role = await get_role_by_id(session, role_id)
    return role_to_response(role)


@router.put("/{role_id}")
async def update_role(
    role_id: UUID,
    role_data: RoleUpdateSchema,
    session=Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> RoleResponse:
    """更新角色（仅管理员）"""
    require_admin(current_user)
    
    role = await get_role_by_id(session, role_id)
    
    # 更新字段
    update_data = role_data.model_dump(exclude_unset=True)
    
    # 如果更新了 name，检查是否与其他角色名冲突
    if role_data.name is not None and role_data.name != role.name:
        result = await session.execute(
            select(Role).where(Role.name == role_data.name, Role.id != role_id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="角色名已存在")
    
    for field, value in update_data.items():
        setattr(role, field, value)
    
    await session.commit()
    await session.refresh(role)
    
    log.info("更新角色", role_name=role.name, updater=current_user.usernumb)
    
    return role_to_response(role)


@router.delete("/{role_id}")
async def delete_role(
    role_id: UUID,
    session=Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """删除角色（仅管理员）"""
    require_admin(current_user)
    
    role = await get_role_by_id(session, role_id)
    
    # 检查是否有用户使用该角色
    from app.db.models.user import User
    result = await session.execute(select(User).where(User.role_id == role_id))
    users_with_role = result.scalars().first()
    if users_with_role:
        raise HTTPException(
            status_code=400,
            detail="无法删除角色：仍有用户使用该角色",
        )
    
    await session.delete(role)
    await session.commit()
    
    log.info("删除角色", role_name=role.name, deleter=current_user.usernumb)
    
    return {"status": "success", "message": "角色已删除"}


@router.put("/{role_id}/permissions")
async def update_role_permissions(
    role_id: UUID,
    permissions_data: RolePermissionsSchema,
    session=Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> RoleResponse:
    """更新角色权限（仅管理员）"""
    require_admin(current_user)
    
    role = await get_role_by_id(session, role_id)
    
    role.permissions = permissions_data.permissions
    
    await session.commit()
    await session.refresh(role)
    
    log.info("更新角色权限", role_name=role.name, updater=current_user.usernumb)
    
    return role_to_response(role)


def role_to_response(role: Role) -> RoleResponse:
    """将 Role 模型转换为响应模型"""
    return RoleResponse(
        id=role.id,
        name=role.name,
        permissions=role.permissions,
        description=role.description,
        created_at=role.created_at.isoformat(),
    )
