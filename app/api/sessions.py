"""
Session Move API - Session Management

端点：
- POST /api/sessions/{id}/move - 移动会话到项目或从项目中移除
- GET /api/projects/{id}/sessions - 获取项目内的会话列表

权限控制：
- RBAC：基于角色权限检查
- ABAC：公司数据隔离
- 超级管理员绕过所有权限检查

逻辑：
- 移动会话时会同步更新关联的 File 记录的 project_id
- 关联的 File 记录的 file_context 也会相应更新
"""

import uuid
from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import fail, ok
from app.db.engine import get_db
from app.db.models.chat import ChatSession
from app.db.models.file import File
from app.db.models.project import Project
from app.security.auth import AuthenticatedUser, get_current_user, is_super_admin

router = APIRouter(tags=["会话管理"])
log = structlog.get_logger()


# ============ Pydantic 请求/响应模型 ============

class MoveSessionRequest(BaseModel):
    """移动会话请求模型"""
    project_id: UUID | None = Field(
        None,
        description="目标项目 ID，为 null 表示从当前项目中移除"
    )


class SessionResponse(BaseModel):
    """会话响应模型"""
    id: UUID
    session_id: str
    user_id: UUID
    project_id: UUID | None
    title: str | None
    turn_count: int
    status: str
    created_at: str
    last_active_at: str
    
    class Config:
        from_attributes = True


class SessionListResponse(BaseModel):
    """会话列表响应模型"""
    items: list[SessionResponse]
    total: int
    page: int
    page_size: int


class MoveSessionResponse(BaseModel):
    """移动会话响应模型"""
    id: UUID
    session_id: str
    project_id: UUID | None
    previous_project_id: UUID | None
    updated_files_count: int
    message: str


# ============ 权限检查工具函数 ============

async def get_session_or_404(
    session: AsyncSession,
    session_id: str,
) -> ChatSession:
    """
    根据 ID 获取会话，不存在则抛出 404
    
    Args:
        session: 数据库会话
        session_id: 会话 ID
        
    Returns:
        ChatSession: 会话对象
        
    Raises:
        HTTPException: 404 会话不存在
    """
    stmt = select(ChatSession).where(ChatSession.session_id == session_id)
    result = await session.execute(stmt)
    chat_session = result.scalar_one_or_none()
    
    if not chat_session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    return chat_session


async def get_project_or_404(
    session: AsyncSession,
    project_id: UUID,
) -> Project:
    """
    根据 ID 获取项目，不存在则抛出 404
    
    Args:
        session: 数据库会话
        project_id: 项目 ID
        
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


def check_session_ownership(chat_session: ChatSession, user: AuthenticatedUser) -> None:
    """
    检查用户是否拥有该会话
    
    权限规则：
    - 超级管理员：拥有所有权限
    - 会话所有者：完全权限
    - 其他用户：无权限
    
    Raises:
        HTTPException: 403 无权限
    """
    # 超级管理员绕过所有检查
    if is_super_admin(user):
        return
    
    # 会话所有者拥有完全权限
    if str(chat_session.user_id) == user.id:
        return
    
    raise HTTPException(status_code=403, detail="无权访问此会话")


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
    检查用户是否有权修改项目（将会话移入/移出）
    
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
    
    # 只有项目所有者可以修改
    if str(project.owner_id) != user.id:
        raise HTTPException(status_code=403, detail="无权修改此项目")


def session_to_response(chat_session: ChatSession) -> dict:
    """将 ChatSession 模型转换为响应字典"""
    return {
        "id": str(chat_session.id),
        "session_id": chat_session.session_id,
        "user_id": str(chat_session.user_id),
        "project_id": str(chat_session.project_id) if chat_session.project_id else None,
        "title": chat_session.title,
        "turn_count": chat_session.turn_count,
        "status": chat_session.status,
        "created_at": chat_session.created_at.isoformat() if chat_session.created_at else None,
        "last_active_at": chat_session.last_active_at.isoformat() if chat_session.last_active_at else None,
    }


# ============ API 端点 ============

@router.post("/api/sessions/{session_id}/move", response_model=MoveSessionResponse)
async def move_session(
    session_id: str,
    data: MoveSessionRequest,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    移动会话到项目或从项目中移除
    
    功能：
    - project_id 为 null：将会话从当前项目中移除
    - project_id 为有效 UUID：将会话移动到指定项目
    - 自动更新关联的 File 记录的 project_id 和 file_context
    
    权限规则：
    - 超级管理员：可以移动任何会话
    - 会话所有者：可以移动自己的会话
    - 必须对目标项目有写入权限（项目所有者）
    - 必须从源项目有移除权限（项目所有者，如果当前在项目中）
    """
    try:
        # 获取会话
        chat_session = await get_session_or_404(session, session_id)
        
        # 检查会话所有权
        check_session_ownership(chat_session, user)
        
        # 保存之前的项目 ID
        previous_project_id = chat_session.project_id
        
        # 如果目标项目和当前项目相同，直接返回
        if previous_project_id == data.project_id:
            return MoveSessionResponse(
                id=chat_session.id,
                session_id=chat_session.session_id,
                project_id=chat_session.project_id,
                previous_project_id=previous_project_id,
                updated_files_count=0,
                message="会话已在目标项目中",
            )
        
        # 如果目标项目不为空，检查目标项目是否存在且有权限
        if data.project_id is not None:
            target_project = await get_project_or_404(session, data.project_id)
            check_project_modify_permission(target_project, user)
            target_company = target_project.company
        else:
            target_project = None
            target_company = None
        
        # 如果当前在项目中，检查是否有权限从该项目移除
        if previous_project_id is not None:
            source_project = await get_project_or_404(session, previous_project_id)
            check_project_modify_permission(source_project, user)
        
        # 更新会话的 project_id
        chat_session.project_id = data.project_id
        await session.flush()
        
        # 更新关联的 File 记录
        # 1. 更新 project_id
        # 2. 更新 file_context：session_in_project -> session 或 session -> session_in_project
        new_context = "session_in_project" if data.project_id else "session"
        
        # 获取需要更新的文件数量
        count_stmt = select(func.count()).where(
            File.session_id == session_id
        )
        count_result = await session.execute(count_stmt)
        files_count = count_result.scalar()
        
        # 更新所有关联的 File 记录
        if files_count > 0:
            await session.execute(
                update(File)
                .where(File.session_id == session_id)
                .values(
                    project_id=data.project_id,
                    file_context=new_context,
                )
            )
        
        # 更新项目计数器（如果有变化）
        if previous_project_id is not None:
            # 减少源项目计数
            await session.execute(
                update(Project)
                .where(Project.id == previous_project_id)
                .values(session_count=Project.session_count - 1)
            )
        
        if data.project_id is not None:
            # 增加目标项目计数
            await session.execute(
                update(Project)
                .where(Project.id == data.project_id)
                .values(session_count=Project.session_count + 1)
            )
        
        await session.commit()
        await session.refresh(chat_session)
        
        log.info(
            "移动会话",
            session_id=session_id,
            user_id=user.id,
            previous_project_id=str(previous_project_id) if previous_project_id else None,
            new_project_id=str(data.project_id) if data.project_id else None,
            updated_files_count=files_count,
        )
        
        return MoveSessionResponse(
            id=chat_session.id,
            session_id=chat_session.session_id,
            project_id=chat_session.project_id,
            previous_project_id=previous_project_id,
            updated_files_count=files_count,
            message="会话移动成功" if data.project_id else "会话已从项目中移除",
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        log.error("移动会话失败", error=str(e), session_id=session_id, user_id=user.id)
        raise HTTPException(status_code=500, detail=f"移动会话失败: {e}")


@router.get("/api/projects/{project_id}/sessions", response_model=SessionListResponse)
async def list_project_sessions(
    project_id: UUID,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    获取项目内的会话列表
    
    返回项目中的所有会话，按最后活跃时间降序排列。
    
    权限规则：
    - 超级管理员：查看任何项目的会话
    - 项目所有者：查看自己项目的会话
    - 同公司用户：查看同公司项目的会话
    - 其他用户：无权限
    """
    try:
        # 获取项目
        project = await get_project_or_404(session, project_id)
        
        # 检查项目访问权限
        check_project_access(project, user)
        
        # 基础查询
        query = select(ChatSession).where(ChatSession.project_id == project_id)
        
        # 统计总数
        count_query = select(func.count()).select_from(
            select(ChatSession).where(ChatSession.project_id == project_id).subquery()
        )
        count_result = await session.execute(count_query)
        total = count_result.scalar()
        
        # 分页和排序（按 last_active_at DESC）
        offset = (page - 1) * page_size
        query = (
            query.order_by(ChatSession.last_active_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        
        result = await session.execute(query)
        sessions = result.scalars().all()
        
        log.info(
            "获取项目会话列表",
            project_id=str(project_id),
            user_id=user.id,
            count=len(sessions),
            total=total,
        )
        
        return SessionListResponse(
            items=[session_to_response(s) for s in sessions],
            total=total,
            page=page,
            page_size=page_size,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        log.error("获取项目会话列表失败", error=str(e), project_id=str(project_id), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"获取项目会话列表失败: {e}")
