"""
文件服务：处理文件上传、验证、存储和删除

功能：
- 文件验证（类型、大小）
- SHA256 哈希计算（流式 8KB 块）
- 用户级去重
- 文件存储（users/{user_id}/upload/{hash}_{filename}）
- 文件记录创建
- 文件删除
"""

import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.file import File
from app.security.auth import AuthenticatedUser

logger = logging.getLogger(__name__)
settings = get_settings()

# 文件上传限制
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
CHUNK_SIZE = 8192  # 8KB 块大小

# 允许的文件类型（MIME 类型和扩展名）
ALLOWED_FILE_TYPES = {
    # PDF
    "application/pdf": ".pdf",
    # Word
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    # Excel
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    # PowerPoint
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    # Markdown
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    # Text
    "text/plain": ".txt",
    # Images
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

ALLOWED_EXTENSIONS = {ext.lower() for ext in ALLOWED_FILE_TYPES.values()}


class FileValidationError(Exception):
    """文件验证错误"""

    pass


class FileService:
    """文件服务类"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.base_storage_path = Path(settings.SANDBOX_HOST_VOLUME) / "uploads"

    async def validate_file(
        self,
        file: UploadFile,
        max_size: int = MAX_FILE_SIZE,
    ) -> tuple[str, str]:
        """
        验证文件类型和大小

        Args:
            file: 上传的文件
            max_size: 最大文件大小（字节）

        Returns:
            tuple: (mime_type, file_extension)

        Raises:
            FileValidationError: 验证失败
        """
        # 检查文件大小
        if file.size is not None and file.size > max_size:
            raise FileValidationError(
                f"文件大小超过限制: {file.size} > {max_size} bytes"
            )

        # 检查文件类型
        mime_type = file.content_type or "application/octet-stream"

        # 首先通过 MIME 类型检查
        if mime_type in ALLOWED_FILE_TYPES:
            return mime_type, ALLOWED_FILE_TYPES[mime_type]

        # 其次通过文件扩展名检查
        filename = file.filename or "unknown"
        ext = Path(filename).suffix.lower()

        if ext in ALLOWED_EXTENSIONS:
            # 找到对应的 MIME 类型
            for mt, e in ALLOWED_FILE_TYPES.items():
                if e == ext:
                    return mt, ext

        raise FileValidationError(
            f"不支持的文件类型: {mime_type} (文件名: {filename})"
        )

    async def calculate_hash(self, file: UploadFile) -> str:
        """
        计算文件的 SHA256 哈希（流式 8KB 块）

        Args:
            file: 上传的文件

        Returns:
            str: SHA256 哈希值（hex）
        """
        sha256_hash = hashlib.sha256()

        # 重置文件指针到开头
        await file.seek(0)

        # 流式读取文件内容
        while chunk := await file.read(CHUNK_SIZE):
            sha256_hash.update(chunk)

        # 重置文件指针到开头
        await file.seek(0)

        return sha256_hash.hexdigest()

    async def check_duplicate(
        self,
        file_hash: str,
        user_id: str | UUID,
    ) -> File | None:
        """
        检查用户是否已上传相同哈希的文件（用户级去重）

        Args:
            file_hash: 文件 SHA256 哈希
            user_id: 用户 ID

        Returns:
            File | None: 已存在的文件记录或 None
        """
        if isinstance(user_id, str):
            user_id = UUID(user_id)

        stmt = select(File).where(
            File.file_hash == file_hash,
            File.uploaded_by == user_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def _get_storage_path(
        self,
        user_id: str | UUID,
        file_hash: str,
        filename: str,
    ) -> Path:
        """
        生成存储路径

        Args:
            user_id: 用户 ID
            file_hash: 文件哈希
            filename: 原始文件名

        Returns:
            Path: 存储路径
        """
        if isinstance(user_id, UUID):
            user_id = str(user_id)

        # 构建路径: users/{user_id}/upload/{hash}_{filename}
        user_dir = self.base_storage_path / "users" / user_id / "upload"
        safe_filename = Path(filename).name  # 防止路径遍历
        storage_filename = f"{file_hash}_{safe_filename}"

        return user_dir / storage_filename

    async def save_file(
        self,
        file: UploadFile,
        storage_path: Path,
    ) -> int:
        """
        保存文件到存储路径

        Args:
            file: 上传的文件
            storage_path: 存储路径

        Returns:
            int: 实际保存的文件大小

        Raises:
            FileValidationError: 保存失败
        """
        try:
            # 确保目录存在
            storage_path.parent.mkdir(parents=True, exist_ok=True)

            # 重置文件指针
            await file.seek(0)

            # 流式写入文件
            total_size = 0
            with open(storage_path, "wb") as f:
                while chunk := await file.read(CHUNK_SIZE):
                    f.write(chunk)
                    total_size += len(chunk)

                    # 实时检查文件大小
                    if total_size > MAX_FILE_SIZE:
                        # 删除已写入的部分文件
                        f.close()
                        storage_path.unlink(missing_ok=True)
                        raise FileValidationError(
                            f"文件大小超过限制: {total_size} > {MAX_FILE_SIZE} bytes"
                        )

            return total_size

        except Exception as e:
            # 清理失败的文件
            if storage_path.exists():
                storage_path.unlink(missing_ok=True)
            logger.error(f"保存文件失败: {e}")
            raise FileValidationError(f"保存文件失败: {e}")

    async def create_file_record(
        self,
        file: UploadFile,
        user: AuthenticatedUser,
        file_hash: str,
        storage_path: Path,
        file_size: int,
        mime_type: str,
        file_extension: str,
        description: str | None = None,
        tags: list[str] | None = None,
        session_id: str | None = None,
        project_id: str | UUID | None = None,
        file_context: str = "session",
    ) -> File:
        """
        创建文件记录

        Args:
            file: 上传的文件
            user: 认证用户
            file_hash: 文件 SHA256 哈希
            storage_path: 存储路径
            file_size: 文件大小
            mime_type: MIME 类型
            file_extension: 文件扩展名
            description: 文件描述（最多 500 字符）
            tags: 标签数组
            session_id: 关联会话 ID
            project_id: 关联项目 ID
            file_context: 文件上下文

        Returns:
            File: 创建的文件记录
        """
        # 验证描述长度
        if description and len(description) > 500:
            raise FileValidationError("文件描述不能超过 500 字符")

        # 清理标签
        clean_tags = None
        if tags:
            clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]

        # 计算相对路径
        relative_path = str(storage_path.relative_to(self.base_storage_path))

        # 创建文件记录
        file_record = File(
            file_name=file.filename or "unknown",
            file_path=relative_path,
            file_size=file_size,
            mime_type=mime_type,
            file_extension=file_extension,
            storage_filename=storage_path.name,
            file_hash=file_hash,
            description=description,
            tags=clean_tags,
            uploaded_by=UUID(user.id),
            session_id=session_id,
            project_id=UUID(project_id) if project_id else None,
            file_context=file_context,
        )

        self.db.add(file_record)
        await self.db.flush()

        logger.info(
            f"创建文件记录: id={file_record.id}, "
            f"user_id={user.id}, hash={file_hash[:16]}..."
        )

        return file_record

    async def upload_file(
        self,
        file: UploadFile,
        user: AuthenticatedUser,
        description: str | None = None,
        tags: list[str] | None = None,
        session_id: str | None = None,
        project_id: str | UUID | None = None,
        file_context: str = "session",
        skip_duplicate: bool = True,
    ) -> tuple[File, bool]:
        """
        上传文件（完整流程）

        Args:
            file: 上传的文件
            user: 认证用户
            description: 文件描述
            tags: 标签数组
            session_id: 关联会话 ID
            project_id: 关联项目 ID
            file_context: 文件上下文
            skip_duplicate: 是否跳过重复文件（直接返回已有记录）

        Returns:
            File: 文件记录

        Raises:
            FileValidationError: 验证失败
            HTTPException: 其他错误
        """
        try:
            # 1. 验证文件
            mime_type, file_extension = await self.validate_file(file)
            logger.debug(f"文件验证通过: {file.filename}, type={mime_type}")

            # 2. 计算哈希
            file_hash = await self.calculate_hash(file)
            logger.debug(f"文件哈希计算完成: {file_hash[:16]}...")

            # 3. 检查重复（用户级去重）
            if skip_duplicate:
                existing_file = await self.check_duplicate(file_hash, user.id)
                if existing_file:
                    logger.info(
                        f"发现重复文件，直接返回已有记录: "
                        f"file_id={existing_file.id}"
                    )
                    return existing_file, True  # 返回重复标志

            # 4. 生成存储路径
            storage_path = self._get_storage_path(user.id, file_hash, file.filename or "unknown")

            # 5. 保存文件
            file_size = await self.save_file(file, storage_path)
            logger.debug(f"文件保存完成: {storage_path}, size={file_size}")

            # 6. 创建文件记录
            file_record = await self.create_file_record(
                file=file,
                user=user,
                file_hash=file_hash,
                storage_path=storage_path,
                file_size=file_size,
                mime_type=mime_type,
                file_extension=file_extension,
                description=description,
                tags=tags,
                session_id=session_id,
                project_id=project_id,
                file_context=file_context,
            )

            await self.db.commit()
            return file_record, False  # 返回新文件标志

        except FileValidationError as e:
            await self.db.rollback()
            logger.warning(f"文件验证失败: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            await self.db.rollback()
            logger.error(f"文件上传失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"文件上传失败: {e}")

    async def delete_file(
        self,
        file_id: str | UUID,
        user: AuthenticatedUser,
        delete_physical: bool = True,
    ) -> bool:
        """
        删除文件

        Args:
            file_id: 文件 ID
            user: 认证用户（用于权限验证）
            delete_physical: 是否同时删除物理文件

        Returns:
            bool: 是否成功删除

        Raises:
            HTTPException: 文件不存在或权限不足
        """
        try:
            if isinstance(file_id, str):
                file_id = UUID(file_id)

            # 查询文件记录
            stmt = select(File).where(File.id == file_id)
            result = await self.db.execute(stmt)
            file_record = result.scalar_one_or_none()

            if not file_record:
                raise HTTPException(status_code=404, detail="文件不存在")

            # 权限检查：只能删除自己上传的文件（管理员除外）
            if str(file_record.uploaded_by) != user.id and user.role != "admin":
                raise HTTPException(status_code=403, detail="无权删除此文件")

            # 删除物理文件
            if delete_physical:
                storage_path = self.base_storage_path / file_record.file_path
                if storage_path.exists():
                    storage_path.unlink()
                    logger.info(f"删除物理文件: {storage_path}")

                # 如果目录为空，删除目录
                try:
                    parent_dir = storage_path.parent
                    if parent_dir.exists() and not any(parent_dir.iterdir()):
                        parent_dir.rmdir()
                        logger.info(f"删除空目录: {parent_dir}")
                except OSError:
                    pass  # 忽略目录删除失败

            # 删除数据库记录
            await self.db.delete(file_record)
            await self.db.commit()

            logger.info(f"删除文件记录: file_id={file_id}, user_id={user.id}")
            return True

        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"删除文件失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"删除文件失败: {e}")

    async def get_file_by_id(
        self,
        file_id: str | UUID,
        user: AuthenticatedUser | None = None,
    ) -> File:
        """
        获取文件记录

        Args:
            file_id: 文件 ID
            user: 认证用户（可选，用于权限验证）

        Returns:
            File: 文件记录

        Raises:
            HTTPException: 文件不存在或权限不足
        """
        try:
            if isinstance(file_id, str):
                file_id = UUID(file_id)

            stmt = select(File).where(File.id == file_id)
            result = await self.db.execute(stmt)
            file_record = result.scalar_one_or_none()

            if not file_record:
                raise HTTPException(status_code=404, detail="文件不存在")

            # 权限检查
            if user and str(file_record.uploaded_by) != user.id and user.role != "admin":
                raise HTTPException(status_code=403, detail="无权访问此文件")

            return file_record

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取文件失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"获取文件失败: {e}")

    async def get_user_files(
        self,
        user: AuthenticatedUser,
        session_id: str | None = None,
        project_id: str | UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[File]:
        """
        获取用户的文件列表

        Args:
            user: 认证用户
            session_id: 会话 ID 过滤
            project_id: 项目 ID 过滤
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            list[File]: 文件列表
        """
        try:
            user_id = UUID(user.id)

            stmt = select(File).where(File.uploaded_by == user_id)

            if session_id:
                stmt = stmt.where(File.session_id == session_id)

            if project_id:
                stmt = stmt.where(File.project_id == UUID(project_id))

            stmt = stmt.order_by(File.uploaded_at.desc()).limit(limit).offset(offset)

            result = await self.db.execute(stmt)
            return list(result.scalars().all())

        except Exception as e:
            logger.error(f"获取用户文件列表失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"获取文件列表失败: {e}")

    def get_file_absolute_path(self, file_record: File) -> Path:
        """
        获取文件的绝对路径

        Args:
            file_record: 文件记录

        Returns:
            Path: 绝对路径
        """
        return self.base_storage_path / file_record.file_path
