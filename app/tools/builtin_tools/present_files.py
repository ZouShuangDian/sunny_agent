"""
PresentFilesTool — 将 outputs 目录的文件暴露为下载链接

职责：
1. 校验文件路径在当前 session 的 outputs 目录内
2. 将容器内路径转换为 API 下载 URL
3. 创建 File 记录到数据库（包含 AI 生成标记）
4. 返回文件名 + 下载链接列表，LLM 在回复中告知用户

下载实际由 /api/files/download 端点处理，端点负责流式返回文件内容。
"""

import asyncio
import mimetypes
import os
from datetime import datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import async_session
from app.db.models.chat import ChatSession
from app.db.models.file import File
from app.db.models.user import User
from app.execution.session_context import get_session_id
from app.execution.user_context import get_user_id
from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()
settings = get_settings()


class PresentFilesParams(BaseModel):
    paths: list[str] = Field(
        description=(
            "要呈现给用户的文件路径列表，路径必须在 "
            "/mnt/users/{user_id}/outputs/{session_id}/ 目录下"
        )
    )


class PresentFilesTool(BaseTool):
    """将 outputs 目录中的文件转换为下载链接，供 LLM 在回复中告知用户"""

    @property
    def name(self) -> str:
        return "present_files"

    @property
    def description(self) -> str:
        return (
            "将 outputs 目录中的文件暴露为下载链接。\n"
            "用法：先用 write_file 写入文件，再调用 present_files 获取下载 URL。\n"
            "只能呈现当前 session 的 outputs 目录下的文件。\n"
            "返回文件名和下载链接列表，在最终回复中告知用户点击下载。"
        )

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def risk_level(self) -> str:
        return "read"

    @property
    def timeout_ms(self) -> int:
        return 5_000

    @property
    def params_model(self) -> type[BaseModel]:
        return PresentFilesParams

    async def execute(self, args: dict) -> ToolResult:
        params = PresentFilesParams(**args)
        session_id = get_session_id()
        user_id = get_user_id()

        if not session_id:
            return ToolResult.fail("present_files 需要有效的 session_id，当前上下文未设置")

        if not user_id:
            return ToolResult.fail("present_files 需要有效的 user_id，当前上下文未设置")

        allowed_prefix = f"/mnt/users/{user_id}/outputs/{session_id}/"
        files = []
        created_files = []

        for path in params.paths:
            normalized = os.path.normpath(path)
            if not normalized.startswith(allowed_prefix.rstrip("/")):
                return ToolResult.fail(
                    f"present_files 只允许呈现 {allowed_prefix} 下的文件，拒绝路径：{path}"
                )

            filename = os.path.basename(normalized)
            # 将容器内路径转换为宿主机相对路径（去掉 /mnt/ 前缀）
            # 实际下载由 /api/files/download?path=users/{uid}/outputs/{sid}/filename 处理
            relative = normalized.removeprefix("/mnt/")
            download_url = f"/api/files/download?path={relative}"

            files.append({
                "name": filename,
                "path": path,
                "download_url": download_url,
            })

            # 创建 File 记录
            try:
                file_record = await self._create_file_record(
                    filename=filename,
                    file_path=relative,
                    full_path=normalized,
                    session_id=session_id,
                    user_id=user_id,
                )
                created_files.append(file_record)
            except Exception as e:
                log.warning("创建文件记录失败", filename=filename, error=str(e))
                # 不中断流程，继续处理其他文件

        log.info(
            "present_files 生成下载链接",
            count=len(files),
            session_id=session_id[:8],
            created_files=len(created_files),
        )

        return ToolResult.success(
            message=f"已生成 {len(files)} 个文件的下载链接，请在回复中告知用户点击下载",
            files=files,
        )

    async def _create_file_record(
        self,
        filename: str,
        file_path: str,
        full_path: str,
        session_id: str,
        user_id: str,
    ) -> File:
        """
        创建 File 记录
        
        Args:
            filename: 文件名
            file_path: 相对路径
            full_path: 完整路径（用于获取文件信息）
            session_id: 会话 ID
            user_id: 用户工号 (usernumb)
            
        Returns:
            File: 创建的 File 记录
        """
        async with async_session() as db:
            # 通过 usernumb 查询用户 UUID
            stmt = select(User.id).where(User.usernumb == user_id)
            result = await db.execute(stmt)
            user_uuid = result.scalar_one_or_none()
            
            if not user_uuid:
                raise ValueError(f"未找到用户: {user_id}")
            
            # 检查会话是否属于某个项目
            stmt = select(ChatSession).where(ChatSession.session_id == session_id)
            result = await db.execute(stmt)
            chat_session = result.scalar_one_or_none()
            
            project_id = chat_session.project_id if chat_session else None
            file_context = "session_in_project" if project_id else "session"
            
            # 获取文件信息
            host_root = Path(settings.SANDBOX_HOST_VOLUME)
            absolute_path = host_root / file_path
            
            file_size = 0
            file_hash = None
            mime_type = "application/octet-stream"
            
            try:
                if absolute_path.exists():
                    file_size = absolute_path.stat().st_size
                    mime_type, _ = mimetypes.guess_type(str(absolute_path))
                    if not mime_type:
                        mime_type = "application/octet-stream"
                    
                    # 计算文件哈希（SHA256）
                    import hashlib
                    hasher = hashlib.sha256()
                    with open(absolute_path, "rb") as f:
                        for chunk in iter(lambda: f.read(8192), b""):
                            hasher.update(chunk)
                    file_hash = hasher.hexdigest()
            except Exception as e:
                log.warning("获取文件信息失败", path=str(absolute_path), error=str(e))
            
            # 提取文件扩展名
            file_extension = os.path.splitext(filename)[1].lower()
            
            # 创建 File 记录
            from uuid6 import uuid7
            file_record = File(
                id=uuid7(),
                file_name=filename,
                file_path=file_path,
                file_size=file_size,
                mime_type=mime_type,
                file_extension=file_extension,
                storage_filename=filename,  # 使用原始文件名作为存储名（已在 outputs 目录中）
                file_hash=file_hash,
                description="AI 生成的文件",
                tags=["ai_generated"],
                uploaded_by=user_uuid,
                session_id=session_id,
                project_id=project_id,
                file_context=file_context,
            )
            
            db.add(file_record)
            await db.commit()
            await db.refresh(file_record)
            
            log.info(
                "创建文件记录",
                file_id=str(file_record.id),
                file_name=filename,
                session_id=session_id,
                project_id=str(project_id) if project_id else None,
                file_context=file_context,
            )
            
            return file_record

    def _create_file_record_background(
        self,
        filename: str,
        file_path: str,
        full_path: str,
        session_id: str,
        user_id: str,
    ) -> None:
        """后台创建 File 记录（异步执行）"""
        asyncio.create_task(
            self._safe_create_file_record(filename, file_path, full_path, session_id, user_id)
        )

    async def _safe_create_file_record(
        self,
        filename: str,
        file_path: str,
        full_path: str,
        session_id: str,
        user_id: str,
    ) -> None:
        """安全创建 File 记录（失败静默）"""
        try:
            await self._create_file_record(filename, file_path, full_path, session_id, user_id)
        except Exception as e:
            log.warning("后台创建文件记录失败", filename=filename, error=str(e))
