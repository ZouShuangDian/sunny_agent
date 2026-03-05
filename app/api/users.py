"""
用户管理 API
"""

import csv
import io
from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.db.engine import get_db
from app.db.filters import CompanyFilter
from app.db.models.user import Role, User
from app.security.auth import AuthenticatedUser, get_current_user
from app.security.auth import hash_password

router = APIRouter(prefix="/api/users", tags=["用户管理"])
log = structlog.get_logger()


# Pydantic 模型
class UserCreateSchema(BaseModel):
    """创建用户请求模型"""
    usernumb: str = Field(..., min_length=1, max_length=32, description="工号")
    username: str = Field(..., min_length=1, max_length=64, description="姓名")
    email: str | None = Field(None, max_length=128, description="邮箱")
    role_id: UUID = Field(..., description="角色 ID")
    department: str | None = Field(None, max_length=64, description="部门")
    company: str | None = Field(None, max_length=100, description="公司")
    phone: str | None = Field(None, max_length=20, description="手机号")
    source: str | None = Field("local", description="用户来源")


class UserUpdateSchema(BaseModel):
    """更新用户请求模型"""
    username: str | None = Field(None, max_length=64, description="姓名")
    email: str | None = Field(None, max_length=128, description="邮箱")
    role_id: UUID | None = Field(None, description="角色 ID")
    department: str | None = Field(None, max_length=64, description="部门")
    company: str | None = Field(None, max_length=100, description="公司")
    phone: str | None = Field(None, max_length=20, description="手机号")
    is_active: bool | None = Field(None, description="是否激活")


class UserUpdateMeSchema(BaseModel):
    """更新当前用户请求模型"""
    username: str | None = Field(None, max_length=64, description="姓名")
    email: str | None = Field(None, max_length=128, description="邮箱")
    department: str | None = Field(None, max_length=64, description="部门")
    phone: str | None = Field(None, max_length=20, description="手机号")
    avatar_url: str | None = Field(None, max_length=512, description="头像 URL")


class RoleSchema(BaseModel):
    """角色响应模型"""
    id: UUID
    name: str
    description: str | None = None
    
    class Config:
        from_attributes = True


class UserResponse(BaseModel):
    """用户响应模型"""
    id: UUID
    usernumb: str
    username: str
    email: str | None = None
    role: RoleSchema
    department: str | None = None
    company: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    source: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    """用户列表响应模型"""
    items: list[UserResponse]
    total: int
    page: int
    page_size: int


class BulkImportResponse(BaseModel):
    """批量导入响应模型"""
    success: int
    failed: int
    errors: list[dict] = []


def require_admin(user: AuthenticatedUser) -> None:
    """检查用户是否为管理员"""
    if "*" not in user.permissions:
        raise HTTPException(status_code=403, detail="权限不足：需要管理员权限")


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User:
    """根据 ID 获取用户，不存在则抛出 404"""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreateSchema,
    session: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """创建新用户（仅管理员）"""
    require_admin(current_user)
    
    # 检查 usernumb 是否已存在
    result = await session.execute(select(User).where(User.usernumb == user_data.usernumb))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="工号已存在")
    
    # 获取角色
    role_result = await session.execute(select(Role).where(Role.id == user_data.role_id))
    role = role_result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=400, detail="角色不存在")
    
    # 创建用户，默认密码 123456
    user = User(
        usernumb=user_data.usernumb,
        username=user_data.username,
        email=user_data.email,
        hashed_pwd=hash_password("123456"),
        role_id=user_data.role_id,
        department=user_data.department,
        company=user_data.company,
        phone=user_data.phone,
        source=user_data.source or "local",
    )
    
    session.add(user)
    await session.commit()
    await session.refresh(user)
    
    log.info("创建用户", usernumb=user.usernumb, creator=current_user.usernumb)
    
    return ok(data=user_to_response(user), message="用户创建成功", status_code=201)


@router.get("")
async def list_users(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    company: str | None = Query(None, description="按公司筛选"),
    department: str | None = Query(None, description="按部门筛选"),
    source: str | None = Query(None, description="按来源筛选"),
    session: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """查询用户列表（支持分页和筛选）"""
    require_admin(current_user)
    
    query = select(User).where(User.is_active == True)
    
    # 应用筛选条件
    if company:
        query = query.where(User.company == company)
    if department:
        query = query.where(User.department == department)
    if source:
        query = query.where(User.source == source)
    
    # 应用公司隔离过滤器（普通用户只能看自己公司的用户）
    query = CompanyFilter.apply(query, User, current_user)
    
    # 分页
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    
    result = await session.execute(query)
    users = result.scalars().all()
    
    # 获取总数
    count_query = select(User)
    if company:
        count_query = count_query.where(User.company == company)
    if department:
        count_query = count_query.where(User.department == department)
    if source:
        count_query = count_query.where(User.source == source)
    count_query = CompanyFilter.apply(count_query, User, current_user)
    count_result = await session.execute(select(func.count()).select_from(count_query.subquery()))
    total = count_result.scalar()
    
    return ok(data=UserListResponse(
        items=[user_to_response(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    ))


@router.get("/{user_id}")
async def get_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """获取用户详情"""
    user = await get_user_by_id(session, user_id)
    
    # 普通用户只能查看自己公司的用户
    if "admin" not in current_user.permissions:
        if user.company != current_user.company:
            raise HTTPException(status_code=403, detail="无权查看其他公司用户")
    
    return ok(data=user_to_response(user))


@router.put("/{user_id}")
async def update_user(
    user_id: UUID,
    user_data: UserUpdateSchema,
    session: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """更新用户信息（仅管理员）"""
    require_admin(current_user)
    
    user = await get_user_by_id(session, user_id)
    
    # 普通用户不能修改其他公司的用户
    if "admin" not in current_user.permissions:
        if user.company != current_user.company:
            raise HTTPException(status_code=403, detail="无权修改其他公司用户")
    
    # 更新字段
    update_data = user_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)
    
    # 如果更新了 role_id，验证角色是否存在
    if user_data.role_id is not None:
        role_result = await session.execute(select(Role).where(Role.id == user_data.role_id))
        role = role_result.scalar_one_or_none()
        if not role:
            raise HTTPException(status_code=400, detail="角色不存在")
        user.role_id = user_data.role_id
    
    await session.commit()
    await session.refresh(user)
    
    log.info("更新用户", usernumb=user.usernumb, updater=current_user.usernumb)
    
    return ok(data=user_to_response(user), message="用户更新成功")


@router.delete("/{user_id}")
async def delete_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """软删除用户（仅管理员）"""
    require_admin(current_user)
    
    user = await get_user_by_id(session, user_id)
    
    # 不能删除自己
    if str(user.id) == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账户")
    
    # 软删除：设置 is_active = false
    user.is_active = False
    await session.commit()
    
    log.info("删除用户", usernumb=user.usernumb, deleter=current_user.usernumb)
    
    return ok(message="用户已删除")


@router.post("/bulk-import")
async def bulk_import_users(
    file: UploadFile = File(..., description="CSV 或 Excel 文件"),
    session: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """批量导入用户（仅管理员）"""
    require_admin(current_user)
    
    # 检查文件类型
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    
    content = await file.read()
    results = {"success": 0, "failed": 0, "errors": []}
    
    try:
        if file.filename.endswith(".csv"):
            results = await import_from_csv(content, session, results)
        elif file.filename.endswith((".xlsx", ".xls")):
            raise HTTPException(status_code=400, detail="Excel 导入暂不支持，请使用 CSV 格式")
        else:
            raise HTTPException(status_code=400, detail="不支持的文件格式，请使用 CSV 或 Excel")
    except Exception as e:
        log.error("批量导入失败", error=str(e))
        raise HTTPException(status_code=500, detail=f"导入失败：{str(e)}")
    
    log.info("批量导入用户", success=results["success"], failed=results["failed"])
    
    return ok(data=BulkImportResponse(**results), message="批量导入完成")


async def import_from_csv(content: bytes, session: AsyncSession, results: dict) -> dict:
    """从 CSV 导入用户"""
    # 检测编码
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("gbk")
    
    reader = csv.DictReader(io.StringIO(text))
    
    # 验证必需列
    required_columns = {"usernumb", "username"}
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV 文件格式错误")
    
    missing_columns = required_columns - set(reader.fieldnames)
    if missing_columns:
        raise HTTPException(status_code=400, detail=f"CSV 缺少必需列：{missing_columns}")
    
    for row_num, row in enumerate(reader, start=2):
        try:
            # 检查 usernumb 是否已存在
            result = await session.execute(select(User).where(User.usernumb == row["usernumb"]))
            if result.scalar_one_or_none():
                results["failed"] += 1
                results["errors"].append({
                    "row": row_num,
                    "usernumb": row["usernumb"],
                    "reason": "工号已存在",
                })
                continue
            
            # 创建用户
            user = User(
                usernumb=row["usernumb"],
                username=row["username"],
                email=row.get("email"),
                department=row.get("department"),
                company=row.get("company"),
                phone=row.get("phone"),
                source="local",
            )
            session.add(user)
            results["success"] += 1
            
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({
                "row": row_num,
                "usernumb": row.get("usernumb", "unknown"),
                "reason": str(e),
            })
    
    await session.commit()
    return results


@router.get("/me")
async def get_current_user_info(
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """获取当前用户信息"""
    user = await get_user_by_id(session, UUID(current_user.id))
    return ok(data=user_to_response(user))


@router.put("/me")
async def update_current_user_info(
    user_data: UserUpdateMeSchema,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """更新当前用户信息"""
    user = await get_user_by_id(session, UUID(current_user.id))
    
    # 只允许更新特定字段
    update_data = user_data.model_dump(exclude_unset=True)
    allowed_fields = {"username", "email", "department", "phone", "avatar_url"}
    
    for field, value in update_data.items():
        if field in allowed_fields:
            setattr(user, field, value)
    
    await session.commit()
    await session.refresh(user)
    
    log.info("更新当前用户信息", usernumb=user.usernumb)
    
    return ok(data=user_to_response(user), message="个人信息更新成功")


def user_to_response(user: User) -> UserResponse:
    """将 User 模型转换为响应模型"""
    return UserResponse(
        id=user.id,
        usernumb=user.usernumb,
        username=user.username,
        email=user.email,
        role=RoleSchema(
            id=user.role.id,
            name=user.role.name,
            description=user.role.description,
        ),
        department=user.department,
        company=user.company,
        phone=user.phone,
        avatar_url=user.avatar_url,
        source=user.source,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
