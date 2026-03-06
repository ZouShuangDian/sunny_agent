"""
项目文件 API - Project File Management

端点：
- POST /api/projects/{id}/files - 上传文件（multipart/form-data）
- GET /api/projects/{id}/files - 列出项目文件（包括上传文件和 AI 生成文件）
- DELETE /api/projects/{id}/files/{file_id} - 删除文件

权限控制：
- RBAC：基于角色权限检查
- ABAC：公司数据隔离
- 项目所有者可以删除项目内所有文件
- 超级管理员绕过所有权限检查
"""

import logging
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File as FastAPIFile, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import fail, ok
from app.db.engine import get_db
from app.db.models.chat import ChatSession
from app.db.models.file import File
from app.db.models.project import Project
from app.security.auth import AuthenticatedUser, get_current_user, is_super_admin
from app.services.file_service import FileService

router = APIRouter(prefix="/api/projects", tags=["项目文件"])
log = structlog.get_logger()


# ============ Pydantic 请求/响应模型 ============

class FileUploadResponse(BaseModel):
    """文件上传响应模型"""
    id: UUID
    file_name: str
    file_size: int
    mime_type: str
    file_extension: str
    file_hash: str | None
    description: str | None
    tags: list[str] | None
    uploaded_by: UUID
    uploaded_at: str
    file_context: str
    session_id: str | None
    
    class Config:
        from_attributes = True


class FileListItem(BaseModel):
    """文件列表项模型"""
    id: UUID
    file_name: str
    file_size: int
    mime_type: str
    file_extension: str
    description: str | None
    tags: list[str] | None
    uploaded_by: UUID
    uploaded_at: str
    file_context: str
    session_id: str | None
    is_generated: bool  # 是否为 AI 生成文件
    
    class Config:
        from_attributes = True


class FileListResponse(BaseModel):
    """文件列表响应模型"""
    items: list[FileListItem]
    total: int
    page: int
    page_size: int


class FileDeleteResponse(BaseModel):
    """文件删除响应模型"""
    success: bool
    file_id: UUID

class FileUploadItemResult(BaseModel):
    """single file upload result"""
    file_name: str
    status: str  # created | duplicate | failed
    file: dict | None = None
    error: str | None = None


class BatchFileUploadSummary(BaseModel):
    """batch upload summary"""
    total: int
    success: int
    failed: int
    created: int
    duplicate: int


class BatchFileUploadResponse(BaseModel):
    """batch upload response"""
    items: list[FileUploadItemResult]
    summary: BatchFileUploadSummary



# ============ 权限检查工具函数 ============

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
        HTTPException: 404 项目不存在，403 无权限
    """
    stmt = select(Project).where(Project.id == project_id)
    result = await session.execute(stmt)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查访问权限（超级管理员除外）
    if not is_super_admin(user):
        # 检查是否是项目所有者
        if str(project.owner_id) == user.id:
            return project
    
    return project


async def get_file_or_404(
    session: AsyncSession,
    file_id: UUID,
    project_id: UUID | None = None,
) -> File:
    """
    根据 ID 获取文件，不存在则抛出 404
    
    Args:
        session: 数据库会话
        file_id: 文件 ID
        project_id: 可选的项目 ID 过滤
        
    Returns:
        File: 文件对象
        
    Raises:
        HTTPException: 404 文件不存在
    """
    stmt = select(File).where(File.id == file_id)
    
    if project_id:
        stmt = stmt.where(
            or_(
                File.project_id == project_id,
                File.session_id.in_(
                    select(ChatSession.session_id)
                    .where(ChatSession.project_id == project_id)
                )
            )
        )
    
    result = await session.execute(stmt)
    file_record = result.scalar_one_or_none()
    
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    return file_record


def check_file_delete_permission(
    file_record: File,
    project: Project | None,
    user: AuthenticatedUser,
) -> None:
    """
    检查用户是否有权删除文件
    
    权限规则：
    - 超级管理员：可以删除任何文件
    - 项目所有者：可以删除项目内所有文件
    - 文件上传者：可以删除自己上传的文件
    - 其他用户：无权限
    
    Raises:
        HTTPException: 403 无权限
    """
    # 超级管理员绕过所有检查
    if is_super_admin(user):
        return
    
    # 项目所有者可以删除项目内所有文件
    if project and str(project.owner_id) == user.id:
        return
    
    # 文件上传者可以删除自己的文件
    if str(file_record.uploaded_by) == user.id:
        return
    
    raise HTTPException(status_code=403, detail="无权删除此文件")


def file_to_list_item(file_record: File) -> dict:
    """将 File 模型转换为列表项字典"""
    # 判断是否为 AI 生成文件
    # file_context 为 "session" 且有关联 session_id 但没有 project_id 的直接关联
    # 或者文件是通过 AI 生成的（可以根据其他条件判断）
    is_generated = (
        file_record.file_context in ["session", "session_in_project"]
        and file_record.session_id is not None
    )
    
    return {
        "id": str(file_record.id),
        "file_name": file_record.file_name,
        "file_size": file_record.file_size,
        "mime_type": file_record.mime_type,
        "file_extension": file_record.file_extension,
        "description": file_record.description,
        "tags": file_record.tags,
        "uploaded_by": str(file_record.uploaded_by),
        "uploaded_at": file_record.uploaded_at.isoformat() if file_record.uploaded_at else None,
        "file_context": file_record.file_context,
        "session_id": file_record.session_id,
        "is_generated": is_generated,
    }


def file_to_upload_response(file_record: File) -> dict:
    """将 File 模型转换为上传响应字典"""
    return {
        "id": str(file_record.id),
        "file_name": file_record.file_name,
        "file_size": file_record.file_size,
        "mime_type": file_record.mime_type,
        "file_extension": file_record.file_extension,
        "file_hash": file_record.file_hash,
        "description": file_record.description,
        "tags": file_record.tags,
        "uploaded_by": str(file_record.uploaded_by),
        "uploaded_at": file_record.uploaded_at.isoformat() if file_record.uploaded_at else None,
        "file_context": file_record.file_context,
        "session_id": file_record.session_id,
    }


# ============ API 端点 ============

@router.post("/{project_id}/files", status_code=status.HTTP_201_CREATED)
async def upload_file(
    project_id: UUID,
    file: UploadFile | None = FastAPIFile(None, description="单个文件 (legacy-compatible)"),
    files: list[UploadFile] | None = FastAPIFile(None, description="批量文件 (preferred)"),
    description: str | None = Form(None, description="文件描述"),
    tags: str | None = Form(None, description="标签（逗号分隔）"),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    上传文件到项目
    
    支持 multipart/form-data 上传，文件类型和大小限制由 FileService 控制。
    
    权限规则：
    - 超级管理员：可以上传到任何项目
    - 项目所有者：可以上传到自己的项目
    - 同公司用户：可以上传到同公司项目
    - 其他用户：无权限
    """
    try:
        # 验证项目存在性和访问权限
        project = await get_project_or_404(session, project_id, user)

        # 检查用户是否有权上传（项目所有者或同公司）
        if not is_super_admin(user):
            if str(project.owner_id) != user.id:
                raise HTTPException(status_code=403, detail="no permission to upload to this project")

        # 解析标签
        tag_list = None
        if tags:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        # Support both legacy `file` and new `files` fields
        upload_files: list[UploadFile] = []
        if file is not None:
            upload_files.append(file)
        if files:
            upload_files.extend(files)

        if not upload_files:
            return fail(
                code=40000,
                message="上传文件为空",
                status_code=400,
            )

        file_service = FileService(session)
        items: list[FileUploadItemResult] = []
        created_count = 0
        duplicate_count = 0
        failed_count = 0

        for upload in upload_files:
            file_name = upload.filename or "unknown"
            try:
                file_record, is_duplicate = await file_service.upload_file(
                    file=upload,
                    user=user,
                    description=description,
                    tags=tag_list,
                    project_id=project_id,
                    file_context="project",
                    skip_duplicate=True,
                )
                if is_duplicate:
                    duplicate_count += 1
                    items.append(FileUploadItemResult(
                        file_name=file_name,
                        status="duplicate",
                        file=file_to_upload_response(file_record),
                    ))
                else:
                    created_count += 1
                    items.append(FileUploadItemResult(
                        file_name=file_name,
                        status="created",
                        file=file_to_upload_response(file_record),
                    ))
            except HTTPException as e:
                failed_count += 1
                items.append(FileUploadItemResult(
                    file_name=file_name,
                    status="failed",
                    error=str(e.detail),
                ))
            except Exception as e:
                failed_count += 1
                items.append(FileUploadItemResult(
                    file_name=file_name,
                    status="failed",
                    error=str(e),
                ))

        success_count = created_count + duplicate_count

        # Counter increases only by newly-created files
        if created_count > 0:
            project.file_count += created_count
            await session.commit()

        response_data = BatchFileUploadResponse(
            items=items,
            summary=BatchFileUploadSummary(
                total=len(upload_files),
                success=success_count,
                failed=failed_count,
                created=created_count,
                duplicate=duplicate_count,
            ),
        )

        log.info(
            "批量文件上传至项目",
            project_id=str(project_id),
            user_id=user.id,
            total=len(upload_files),
            success=success_count,
            failed=failed_count,
            created=created_count,
            duplicate=duplicate_count,
        )

        if success_count == 0:
            return fail(
                code=40000,
                message="上传失败",
                status_code=400,
                data=response_data,
            )

        return ok(data=response_data, message="上传成功", status_code=201)

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        log.error("上传失败", error=str(e), project_id=str(project_id), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")

@router.get("/{project_id}/files")
async def list_project_files(
    project_id: UUID,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    file_context: str | None = Query(None, description="文件上下文过滤（project/session/session_in_project）"),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    获取项目文件列表
    
    包括：
    - 直接上传到项目的文件（project_id = 当前项目）
    - 项目内会话生成的 AI 文件（session.project_id = 当前项目）
    
    权限规则：
    - 超级管理员：查看任何项目文件
    - 项目所有者：查看自己的项目文件
    - 同公司用户：查看同公司项目文件
    - 其他用户：无权限
    """
    try:
        from app.db.models.chat import ChatSession
        
        # 验证项目存在性和访问权限
        project = await get_project_or_404(session, project_id, user)
        
        # 获取项目内的所有会话 ID
        session_stmt = select(ChatSession.session_id).where(
            ChatSession.project_id == project_id
        )
        session_result = await session.execute(session_stmt)
        session_ids = [s[0] for s in session_result.fetchall()]
        
        # 构建查询：获取项目直接关联的文件 + 项目内会话关联的文件
        if session_ids:
            query = select(File).where(
                or_(
                    File.project_id == project_id,  # 直接上传到项目的文件
                    File.session_id.in_(session_ids),  # 项目内会话生成的文件
                )
            )
        else:
            query = select(File).where(File.project_id == project_id)
        
        # 应用文件上下文过滤
        if file_context:
            query = query.where(File.file_context == file_context)
        
        # 统计总数
        count_query = query
        count_result = await session.execute(
            select(func.count()).select_from(count_query.subquery())
        )
        total = count_result.scalar()
        
        # 分页和排序（按上传时间倒序）
        offset = (page - 1) * page_size
        query = (
            query.order_by(File.uploaded_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        
        result = await session.execute(query)
        files = result.scalars().all()
        
        return ok(data=FileListResponse(
            items=[FileListItem(**file_to_list_item(f)) for f in files],
            total=total or 0,
            page=page,
            page_size=page_size,
        ))
        
    except HTTPException:
        raise
    except Exception as e:
        log.error("获取项目文件列表失败", error=str(e), project_id=str(project_id), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"获取文件列表失败: {e}")


@router.delete("/{project_id}/files/{file_id}")
async def delete_project_file(
    project_id: UUID,
    file_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    删除项目文件
    
    权限规则：
    - 超级管理员：可以删除任何文件
    - 项目所有者：可以删除项目内所有文件
    - 文件上传者：可以删除自己上传的文件
    - 其他用户：无权限
    """
    try:
        # 验证项目存在性（不需要严格权限检查，因为文件删除权限检查会处理）
        project_result = await session.execute(
            select(Project).where(Project.id == project_id)
        )
        project = project_result.scalar_one_or_none()
        
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        # 获取文件
        file_record = await get_file_or_404(session, file_id)
        
        # 验证文件是否属于该项目
        if file_record.project_id != project_id:
            # 检查是否通过会话关联
            if file_record.session_id:
                from app.db.models.chat import ChatSession
                session_result = await session.execute(
                    select(ChatSession).where(
                        ChatSession.session_id == file_record.session_id
                    )
                )
                chat_session = session_result.scalar_one_or_none()
                if not chat_session or chat_session.project_id != project_id:
                    raise HTTPException(status_code=404, detail="文件不存在于该项目")
            else:
                raise HTTPException(status_code=404, detail="文件不存在于该项目")
        
        # 检查删除权限
        check_file_delete_permission(file_record, project, user)
        
        # 使用 FileService 删除文件
        file_service = FileService(session)
        await file_service.delete_file(file_id, user, delete_physical=False)
        
        # 更新项目文件计数
        if project.file_count > 0:
            project.file_count -= 1
            await session.commit()
        
        log.info(
            "删除项目文件",
            file_id=str(file_id),
            file_name=file_record.file_name,
            project_id=str(project_id),
            user_id=user.id,
        )
        
        return ok(data=FileDeleteResponse(
            success=True,
            file_id=file_id,
        ), message="文件删除成功")
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        log.error("删除文件失败", error=str(e), file_id=str(file_id), project_id=str(project_id), user_id=user.id)
        raise HTTPException(status_code=500, detail=f"删除文件失败: {e}")
