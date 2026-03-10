"""
项目 API - Project Management

端点：
- POST /api/projects - 创建项目
- GET /api/projects - 项目列表（分页，按 updated_at DESC 排序）
- GET /api/projects/{id} - 获取项目详情（包含 file_count 和 session_count）
- PUT /api/projects/{id} - 更新项目名称
- DELETE /api/projects/{id} - 删除项目（硬删除项目，文件和会话 SET NULL）

权限控制：
- RBAC：基于角色权限检查
- ABAC：公司数据隔离
- 超级管理员绕过所有权限检查
"""

import logging
from uuid import UUID

import structlog
from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import fail, ok
from app.db.engine import get_db
from app.db.filters import CompanyFilter
from app.db.models.chat import ChatSession
from app.db.models.file import File
from app.db.models.project import Project
from app.security.auth import AuthenticatedUser, get_current_user, is_super_admin

router = APIRouter(prefix="/api/projects", tags=["项目管理"])
log = structlog.get_logger()


# ============ Pydantic 请求/响应模型 ============

class ProjectCreateSchema(BaseModel):
    """创建项目请求模型"""
    name: str = Field(..., min_length=1, max_length=100, description="项目名称")


class ProjectUpdateSchema(BaseModel):
    """更新项目请求模型"""
    name: str = Field(..., min_length=1, max_length=100, description="项目名称")


class ProjectResponse(BaseModel):
    """项目响应模型"""
    id: UUID
    name: str
    owner_id: UUID
    company: str | None
    file_count: int
    session_count: int
    created_at: str
    updated_at: str
    
    class Config:
        from_attributes = True


class ProjectDetailResponse(ProjectResponse):
    """项目详情响应模型（包含关联数据）"""
    pass


class ProjectListResponse(BaseModel):
    """项目列表响应模型"""
    items: list[ProjectResponse]
    total: int
    page: int
    page_size: int


# ============ 权限检查工具函数 ============

def check_project_access(project: Project, user: AuthenticatedUser) -> None:
    """
    检查用户是否有权访问项目
    
    权限规则：
    - 超级管理员：拥有所有权限
    - 项目所有者：完全权限
    - 同公司用户：只读权限
    - 其他用户：无权限
    
    Raises:
        HTTPException: 403 无权限
    """
    # 超级管理员绕过所有检查
    if is_super_admin(user):
        return
    
    # 项目所有者拥有完全权限
    if str(project.owner_id) == user.id:
        return
    
    # 检查公司隔离（ABAC）
    if project.company and project.company != user.company:
        raise HTTPException(status_code=403, detail="无权访问其他公司的项目")
    
    # 同公司用户可以查看但不能修改
    return


def check_project_modify_permission(project: Project, user: AuthenticatedUser) -> None:
    """
    检查用户是否有权修改/删除项目
    
    权限规则：
    - 超级管理员：拥有所有权限
    - 项目所有者：完全权限
    - 其他用户：无权限
    
    Raises:
        HTTPException: 403 无权限
    """
    # 超级管理员绕过所有检查
    if is_super_admin(user):
        return
    
    # 只有项目所有者可以修改/删除
    if str(project.owner_id) != user.id:
        raise HTTPException(status_code=403, detail="无权修改或删除此项目")


async def get_project_or_404(
    session: AsyncSession,
    project_id: UUID,
    user: AuthenticatedUser,
) -> Project:
    """
    根据 ID 获取项目，不存在则抛出 404
    
    Args:
        session: 数据库会话
        project_id: 项目 ID
        user: 当前用户
        
    Returns:
        Project: 项目对象
        
    Raises:
        HTTPException: 404 项目不存在
    """
    stmt = select(Project).where(Project.id == project_id)
    result = await session.execute(stmt)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    return project


def project_to_response(project: Project) -> dict:
    """将 Project 模型转换为响应字典"""
    return {
        "id": str(project.id),
        "name": project.name,
        "owner_id": str(project.owner_id),
        "company": project.company,
        "file_count": project.file_count,
        "session_count": project.session_count,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


# ============ API 端点 ============

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreateSchema,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    创建新项目
    
    权限：任何已认证用户都可以创建项目
    """
    try:
        # 创建项目
        project = Project(
            name=data.name,
            owner_id=UUID(user.id),
            company=user.company,  # 自动设置用户所属公司
            file_count=0,
            session_count=0,
        )
        
        session.add(project)
        await session.commit()
        await session.refresh(project)
        
        log.info(
            "创建项目",
            project_id=str(project.id),
            project_name=project.name,
            user_id=user.id,
            company=user.company,
        )
        
        return ok(
            data=project_to_response(project),
            message="项目创建成功",
            status_code=201,
        )
        
    except IntegrityError as e:
        await session.rollback()
        # 检查是否是唯一约束冲突（同名项目）
        error_msg = str(e.orig) if hasattr(e, 'orig') else str(e)
        if "uq_projects_owner_name" in error_msg or "UniqueViolationError" in error_msg:
            log.warning("创建项目失败：同名项目已存在", project_name=data.name, user_id=user.id)
            raise HTTPException(status_code=409, detail=f"项目 '{data.name}' 已存在，请使用其他名称")
        else:
            log.error("创建项目失败：数据库约束错误", error=str(e), user_id=user.id)
            raise HTTPException(status_code=500, detail=f"创建项目失败: {e}")
    except Exception as e:
        await session.rollback()
        log.error("创建项目失败", error=str(e), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"创建项目失败: {e}")


@router.get("")
async def list_projects(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, description="每页数量"),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    获取项目列表（分页，按 updated_at DESC 排序）
    
    权限规则：
    - 超级管理员：查看所有项目
    - 普通用户：只能查看自己创建或同公司的项目（ABAC 公司隔离）
    """
    try:
        # 基础查询
        query = select(Project)
        
        # 应用公司隔离过滤器（超级管理员除外）
        if not is_super_admin(user):
            # 用户只能看到自己创建的项目或同公司的项目
            query = query.where(
                (Project.owner_id == UUID(user.id))
            )
        
        # 统计总数
        count_query = query
        count_result = await session.execute(
            select(func.count()).select_from(count_query.subquery())
        )
        total = count_result.scalar()
        
        # 分页和排序（按 updated_at DESC）
        offset = (page - 1) * page_size
        query = (
            query.order_by(Project.updated_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        
        result = await session.execute(query)
        projects = result.scalars().all()
        
        return ok(data=ProjectListResponse(
            items=[project_to_response(p) for p in projects],
            total=total,
            page=page,
            page_size=page_size,
        ))
        
    except Exception as e:
        log.error("获取项目列表失败", error=str(e), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"获取项目列表失败: {e}")


@router.get("/{project_id}")
async def get_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    获取项目详情（包含 file_count 和 session_count）
    
    权限规则：
    - 超级管理员：查看任何项目
    - 项目所有者：查看自己的项目
    - 同公司用户：查看同公司项目
    - 其他用户：无权限
    """
    try:
        # 获取项目
        project = await get_project_or_404(session, project_id, user)
        
        # 检查访问权限
        check_project_access(project, user)
        
        return ok(data=project_to_response(project))
        
    except HTTPException:
        raise
    except Exception as e:
        log.error("获取项目详情失败", error=str(e), project_id=str(project_id), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"获取项目详情失败: {e}")


@router.put("/{project_id}")
async def update_project(
    project_id: UUID,
    data: ProjectUpdateSchema,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    更新项目名称
    
    权限规则：
    - 超级管理员：更新任何项目
    - 项目所有者：更新自己的项目
    - 其他用户：无权限
    """
    try:
        # 获取项目
        project = await get_project_or_404(session, project_id, user)
        
        # 检查修改权限
        check_project_modify_permission(project, user)
        
        # 更新项目名称
        old_name = project.name
        project.name = data.name
        
        await session.commit()
        await session.refresh(project)
        
        log.info(
            "更新项目",
            project_id=str(project.id),
            old_name=old_name,
            new_name=project.name,
            user_id=user.id,
        )
        
        return ok(data=project_to_response(project), message="项目更新成功")
        
    except IntegrityError as e:
        await session.rollback()
        # 检查是否是唯一约束冲突（同名项目）
        error_msg = str(e.orig) if hasattr(e, 'orig') else str(e)
        if "uq_projects_owner_name" in error_msg or "UniqueViolationError" in error_msg:
            log.warning("更新项目失败：同名项目已存在", project_name=data.name, user_id=user.id)
            raise HTTPException(status_code=409, detail=f"项目 '{data.name}' 已存在，请使用其他名称")
        else:
            log.error("更新项目失败：数据库约束错误", error=str(e), project_id=str(project_id), user_id=user.id)
            raise HTTPException(status_code=500, detail=f"更新项目失败: {e}")
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        log.error("更新项目失败", error=str(e), project_id=str(project_id), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"更新项目失败: {e}")


@router.get("/{project_id}/sessions")
async def get_project_sessions(
    project_id: UUID,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, description="每页数量"),
    status: str = Query("all", description="过滤状态：active / archived / all"),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    获取项目下的会话列表

    权限规则：
    - 超级管理员：查看任何项目的会话
    - 项目所有者：查看自己项目的会话
    - 同公司用户：查看同公司项目的会话
    - 其他用户：无权限
    """
    try:
        # 获取项目
        project = await get_project_or_404(session, project_id, user)

        # 检查访问权限
        check_project_access(project, user)

        # 构建查询条件
        conditions = [ChatSession.project_id == project_id]
        if status != "all":
            conditions.append(ChatSession.status == status)

        # 查询总数
        count_query = select(func.count()).select_from(
            select(ChatSession.id).where(*conditions).subquery()
        )
        total = (await session.execute(count_query)).scalar() or 0

        # 查询列表（按最后活跃时间倒序）
        query = (
            select(ChatSession)
            .where(*conditions)
            .order_by(ChatSession.last_active_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await session.execute(query)
        sessions = result.scalars().all()

        # 构建响应
        items = [
            {
                "id": str(s.id),
                "session_id": s.session_id,
                "title": s.title,
                "user_id": str(s.user_id),
                "project_id": str(s.project_id) if s.project_id else None,
                "turn_count": s.turn_count,
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "last_active_at": s.last_active_at.isoformat() if s.last_active_at else None,
            }
            for s in sessions
        ]

        return ok(data={
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        })

    except HTTPException:
        raise
    except Exception as e:
        log.error("获取项目会话列表失败", error=str(e), project_id=str(project_id), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"获取项目会话列表失败: {e}")


@router.post("/{project_id}/sessions/{session_id}")
async def add_session_to_project(
    project_id: UUID,
    session_id: str,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    将对话添加到项目
    
    权限规则：
    - 超级管理员：可操作任何项目
    - 项目所有者：可向自己的项目添加对话
    - 其他用户：无权限
    """
    try:
        # 获取项目
        project = await get_project_or_404(session, project_id, user)
        
        # 检查项目修改权限
        check_project_modify_permission(project, user)
        
        # 校验会话存在且属于当前用户
        session_result = await session.execute(
            select(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user.id,
            )
        )
        session_row = session_result.scalar_one_or_none()
        if not session_row:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        # 更新会话的 project_id
        await session.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(project_id=project_id)
        )
        await session.commit()
        
        log.info(
            "添加会话到项目",
            session_id=session_id,
            project_id=str(project_id),
            user_id=user.id,
        )
        
        return ok(message="会话已添加到项目", data={
            "session_id": session_id,
            "project_id": str(project_id),
        })
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        log.error("添加会话到项目失败", error=str(e), project_id=str(project_id), session_id=session_id, user_id=user.id)
        raise HTTPException(status_code=500, detail=f"添加会话到项目失败: {e}")


@router.delete("/{project_id}/sessions/{session_id}")
async def remove_session_from_project(
    project_id: UUID,
    session_id: str,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    从项目中移除对话
    
    将对话的 project_id 设置为 NULL，使其回到历史对话列表
    
    权限规则：
    - 超级管理员：可操作任何项目
    - 项目所有者：可从自己的项目移除对话
    - 其他用户：无权限
    """
    try:
        # 获取项目
        project = await get_project_or_404(session, project_id, user)
        
        # 检查项目修改权限
        check_project_modify_permission(project, user)
        
        # 校验会话存在且属于当前用户
        session_result = await session.execute(
            select(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user.id,
            )
        )
        session_row = session_result.scalar_one_or_none()
        if not session_row:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        # 如果会话不属于该项目，返回错误
        if session_row.project_id != project_id:
            raise HTTPException(status_code=400, detail="该会话不属于此项目")
        
        # 移除 project_id 关联
        await session.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(project_id=None)
        )
        await session.commit()
        
        log.info(
            "从项目移除会话",
            session_id=session_id,
            project_id=str(project_id),
            user_id=user.id,
        )
        
        return ok(message="会话已从项目移除", data={
            "session_id": session_id,
            "project_id": None,
        })
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        log.error("从项目移除会话失败", error=str(e), project_id=str(project_id), session_id=session_id, user_id=user.id)
        raise HTTPException(status_code=500, detail=f"从项目移除会话失败: {e}")


@router.delete("/{project_id}")
async def delete_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    删除项目
    
    删除逻辑：
    - 硬删除项目记录
    - 关联的文件 project_id 设为 NULL（SET NULL）
    - 关联的会话 project_id 设为 NULL（SET NULL）
    
    权限规则：
    - 超级管理员：删除任何项目
    - 项目所有者：删除自己的项目
    - 其他用户：无权限
    """
    try:
        # 获取项目
        project = await get_project_or_404(session, project_id, user)
        
        # 检查修改权限
        check_project_modify_permission(project, user)
        
        project_name = project.name
        
        # 1. 将关联文件的 project_id 设为 NULL
        await session.execute(
            select(File).where(File.project_id == project_id)
        )
        await session.execute(
            File.__table__.update()
            .where(File.project_id == project_id)
            .values(project_id=None)
        )
        
        # 2. 将关联会话的 project_id 设为 NULL
        await session.execute(
            ChatSession.__table__.update()
            .where(ChatSession.project_id == project_id)
            .values(project_id=None)
        )
        
        # 3. 硬删除项目
        await session.delete(project)
        await session.commit()
        
        log.info(
            "删除项目",
            project_id=str(project_id),
            project_name=project_name,
            user_id=user.id,
        )
        
        return ok(message="项目删除成功")
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        log.error("删除项目失败", error=str(e), project_id=str(project_id), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"删除项目失败: {e}")
