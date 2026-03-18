"""
媒体文件下载模块
处理图片、文件、音频等媒体的下载和存储

路径格式统一：{SANDBOX_HOST_VOLUME}/mnt/users/{user_id}/feishu_media/{filename}
"""

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.db.models.user import User

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.config import get_settings
from app.feishu.client import FeishuClient, get_feishu_client
from app.feishu.context_manager import get_media_context_manager
from app.db.models.feishu import FeishuMediaFiles, MediaType
from app.db.models.file import File as DBFile


settings = get_settings()
logger = structlog.get_logger()

# 媒体文件存储目录（统一路径格式）
# 格式: {SANDBOX_HOST_VOLUME}/mnt/users/{user_id}/feishu_media/
BASE_UPLOAD_DIR = Path(settings.SANDBOX_HOST_VOLUME) / "mnt"
MAX_FILE_SIZE = 30 * 1024 * 1024  # 30MB
CHUNK_SIZE = 8192  # 8KB


class MediaDownloadError(Exception):
    """媒体下载错误"""
    pass


class FileTooLargeError(MediaDownloadError):
    """文件过大错误"""
    pass


class MediaDownloader:
    """媒体文件下载器"""
    
    def __init__(self, feishu_client: FeishuClient = None, app_id: str = None):
        # 注意：get_feishu_client() 是 async 函数，不能在这里调用
        # 使用 lazy initialization，在需要时才获取
        self._feishu_client = feishu_client
        self._app_id = app_id  # 保存 app_id 用于获取客户端
    
    async def _get_client(self) -> FeishuClient:
        """获取 FeishuClient（支持 lazy initialization）"""
        if self._feishu_client is None:
            # 使用保存的 app_id 获取客户端
            self._feishu_client = await get_feishu_client(app_id=self._app_id)
        return self._feishu_client
    
    def _get_storage_path(self, user_id: str | UUID, file_name: str) -> Path:
        """
        生成存储路径（UUID_文件名格式，确保唯一性）
        
        格式: mnt/users/{user_id}/feishu_media/{uuid_prefix}_{safe_filename}
        
        Args:
            user_id: 系统用户ID (UUID)
            file_name: 原始文件名
            
        Returns:
            Path: 完整存储路径
        """
        if isinstance(user_id, UUID):
            user_id = str(user_id)
        
        # 统一路径格式: mnt/users/{user_id}/feishu_media/
        user_dir = BASE_UPLOAD_DIR / "users" / user_id / "feishu_media"
        user_dir.mkdir(parents=True, exist_ok=True)
        
        # 生成唯一文件名：uuid_文件名
        import uuid
        unique_prefix = str(uuid.uuid4())[:8]  # 8 位 UUID 前缀
        
        # 安全文件名（防止路径遍历）
        safe_filename = Path(file_name).name
        # 移除危险字符
        safe_filename = "".join(c for c in safe_filename if c.isalnum() or c in "._- ")
        
        # 组合唯一文件名
        unique_filename = f"{unique_prefix}_{safe_filename}"
        
        file_path = user_dir / unique_filename
        
        return file_path
    
    async def _check_existing_file(
        self,
        db: AsyncSession,
        file_key: str,
        message_id: str,
    ) -> Optional[FeishuMediaFiles]:
        """
        检查是否已存在相同的文件（仅检查 file_key + message_id）
        
        Returns:
            如果找到已存在的记录，返回该记录；否则返回 None
        """
        result = await db.execute(
            select(FeishuMediaFiles).where(
                FeishuMediaFiles.file_key == file_key,
                FeishuMediaFiles.message_id == message_id,
                FeishuMediaFiles.download_status == "completed"
            )
        )
        return result.scalar_one_or_none()
    
    async def download_media(
        self,
        db: AsyncSession,
        file_key: str,
        message_id: str,
        file_name: str,
        file_type: str,
        user_id: str | UUID,  # 改为 user_id（系统用户UUID）
        open_id: str,         # 保留 open_id 用于记录
        chat_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        app_id: Optional[str] = None,  # 应用ID，用于上下文隔离
        skip_db_record: bool = False,  # 是否跳过数据库记录（预下载使用）
        user: Optional["User"] = None,  # type: ignore  # 用户对象（用于私聊文件落盘）
        chat_type: Optional[str] = None,  # 聊天类型：p2p/group
    ) -> Optional[FeishuMediaFiles]:
        """
        下载媒体文件
        
        流程：先检查重复 -> 下载文件 -> 成功后才创建数据库记录
        下载失败（包括文件过大）返回 None，不创建脏数据
        
        Args:
            db: 数据库会话
            file_key: 飞书文件key
            message_id: 关联消息ID
            file_name: 文件名
            file_type: 文件类型 (image/file/audio/media/sticker)
            user_id: 系统用户ID (UUID) - 用于确定存储路径
            open_id: 发送者open_id - 用于记录
            chat_id: 群组ID
            mime_type: MIME类型
            skip_db_record: 是否跳过创建数据库记录（预下载时使用，避免重复记录）
            
        Returns:
            FeishuMediaFiles 记录（成功），None（失败）
        """
        # 1. 检查是否已存在（相同 file_key + message_id）
        existing = await self._check_existing_file(db, file_key, message_id)
        
        if existing:
            # 同一消息中的同一文件，直接返回已有记录
            logger.info("Duplicate media file detected (same message)", 
                       file_key=file_key, 
                       existing_id=str(existing.id))
            return existing
        
        # 2. 生成存储路径（UUID_文件名格式，确保唯一性）
        local_path = self._get_storage_path(user_id, file_name)
        
        # 保存 app_id 以便 _get_client 使用
        if app_id:
            self._app_id = app_id
        
        try:
            # 3. 下载文件（先下载，成功后再创建记录）
            logger.info("Starting media download",
                       file_key=file_key,
                       file_name=file_name,
                       file_type=file_type)
            
            # 根据文件类型确定 media_type 参数
            media_type = "image" if file_type == "image" else "file"
            
            # 获取 FeishuClient（lazy initialization，使用传入的 app_id）
            client = await self._get_client()
            
            # 下载并获取文件名（从 API 响应头提取）
            file_data, downloaded_file_name = await client.download_media(
                file_key=file_key,
                message_id=message_id,
                media_type=media_type,
            )
            
            # 使用 API 返回的文件名（优先级高于传入的 file_name）
            if downloaded_file_name:
                file_name = downloaded_file_name
                # 重新生成存储路径（因为文件名可能变化）
                local_path = self._get_storage_path(user_id, file_name)
            
            # 4. 检查文件大小
            file_size = len(file_data)
            if file_size > MAX_FILE_SIZE:
                # 文件过大，记录日志但不创建数据库记录
                logger.warning("File too large, skipping",
                              file_key=file_key,
                              file_name=file_name,
                              file_size_mb=round(file_size / (1024*1024), 1))
                return None
            
            # 5. 保存文件到磁盘
            local_path.write_bytes(file_data)
            
            # 6. 下载成功
            if skip_db_record:
                # 预下载模式：不创建数据库记录，返回临时对象
                logger.info("Media download completed (pre-download, no db record)",
                           file_key=file_key,
                           file_size=file_size,
                           local_path=str(local_path))
                
                # 返回临时对象（仅包含必要信息）
                return FeishuMediaFiles(
                    id=uuid7(),
                    file_key=file_key,
                    file_name=file_name,
                    file_type=file_type,
                    message_id=message_id,
                    open_id=open_id,
                    chat_id=chat_id,
                    mime_type=mime_type,
                    local_path=str(local_path),
                    file_size=file_size,
                    download_status="completed",
                )
            else:
                # 主下载模式：创建数据库记录
                # 如果是私聊，创建 File 记录（Project 在 _create_or_update_feishu_session_mapping 中创建）
                file_record_id = None
                if chat_type == "p2p" and user and app_id:
                    try:
                        # 1. 计算文件 hash
                        file_hash = None
                        try:
                            sha256 = hashlib.sha256()
                            sha256.update(file_data)
                            file_hash = sha256.hexdigest()
                        except Exception as hash_err:
                            logger.warning("Failed to calculate file hash",
                                          file_path=str(local_path),
                                          error=str(hash_err))
                        
                        # 3. 创建 File 记录（session_id 暂时为 None，AI 管线后更新）
                        file_record = DBFile(
                            id=uuid7(),
                            file_name=file_name,
                            file_path=str(local_path),
                            file_size=file_size,
                            mime_type=mime_type or "application/octet-stream",
                            file_extension=Path(file_name).suffix.lower(),
                            storage_filename=local_path.name,
                            file_hash=file_hash,
                            session_id=None,  # 稍后由 AI 管线更新
                            project_id=None,
                            file_context="feishu_private",
                            feishu_app_id=app_id,
                            feishu_message_id=message_id,
                            feishu_file_key=file_key,
                            feishu_chat_type="p2p",
                            uploaded_by=user.id,
                        )
                        db.add(file_record)
                        await db.flush()  # 获取 file_record.id
                        file_record_id = file_record.id
                        
                        logger.info("Created File record for Feishu media",
                                   file_id=file_record_id,
                                   message_id=message_id)
                    except Exception as file_err:
                        logger.error("Failed to create File record",
                                    message_id=message_id,
                                    error=str(file_err),
                                    exc_info=True)
                        # 继续创建 FeishuMediaFiles 记录，不中断流程
                
                # 5. 创建 FeishuMediaFiles 记录
                media_file = FeishuMediaFiles(
                    id=uuid7(),
                    file_key=file_key,
                    file_name=file_name,
                    file_type=file_type,
                    message_id=message_id,
                    open_id=open_id,
                    chat_id=chat_id,
                    mime_type=mime_type,
                    local_path=str(local_path),
                    file_size=file_size,
                    download_status="completed",
                    file_id=file_record_id,  # 关联 File 记录（私聊时）
                )
                db.add(media_file)
                await db.commit()
                await db.refresh(media_file)
                
                # 7. 添加到上下文管理器（支持历史引用）
                if app_id and chat_id:
                    try:
                        context_manager = get_media_context_manager()
                        await context_manager.add_media_message(
                            app_id=app_id,
                            open_id=open_id,
                            chat_id=chat_id,
                            message_id=message_id,
                            file_type=file_type,
                            file_name=file_name,
                            local_path=str(local_path),
                        )
                    except Exception as ctx_err:
                        # 上下文添加失败不应影响主流程
                        logger.warning("Failed to add to context manager",
                                      file_key=file_key,
                                      error=str(ctx_err))
                
                logger.info("Media download completed",
                           file_key=file_key,
                           file_size=file_size,
                           local_path=str(local_path))
                
                return media_file
            
        except Exception as e:
            # 下载失败，记录日志但不创建数据库记录
            logger.error("Media download failed, no db record created",
                        file_key=file_key,
                        file_name=file_name,
                        error=str(e))
            return None
    
    async def download_with_retry(
        self,
        db: AsyncSession,
        file_key: str,
        message_id: str,
        file_name: str,
        file_type: str,
        user_id: str | UUID,
        open_id: str,
        chat_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        app_id: Optional[str] = None,
        max_retries: int = 3,
        user: Optional["User"] = None,
        chat_type: Optional[str] = None,
    ) -> Optional[FeishuMediaFiles]:
        """
        带重试的媒体下载
        
        Args:
            user: 用户对象（私聊文件落盘用）
            chat_type: 聊天类型（p2p/group）
        
        Returns:
            FeishuMediaFiles 记录（成功），None（最终失败）
        """
        import asyncio
        
        for attempt in range(max_retries):
            result = await self.download_media(
                db=db,
                file_key=file_key,
                message_id=message_id,
                file_name=file_name,
                file_type=file_type,
                user_id=user_id,
                open_id=open_id,
                chat_id=chat_id,
                mime_type=mime_type,
                app_id=app_id,
                user=user,
                chat_type=chat_type,
            )
            
            # 成功，返回记录
            if result is not None:
                return result
            
            # 失败，检查是否需要重试
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.warning(f"Download attempt {attempt + 1} failed, retrying in {wait_time}s",
                             file_key=file_key,
                             file_name=file_name)
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"All {max_retries} download attempts failed",
                           file_key=file_key,
                           file_name=file_name)
        
        # 所有重试都失败，返回 None
        return None


    async def update_file_session_id(
        self,
        db: AsyncSession,
        message_id: str,
        session_id: str,
    ) -> None:
        """
        更新私聊媒体文件的 session_id
        在 AI 管线返回 session_id 后调用
        
        Args:
            db: 数据库会话
            message_id: 飞书消息 ID
            session_id: AI 管线返回的 session ID
        """
        try:
            # 查询此消息的所有 FeishuMediaFiles 记录
            result = await db.execute(
                select(FeishuMediaFiles).where(
                    FeishuMediaFiles.message_id == message_id,
                )
            )
            media_records = result.scalars().all()
            
            # 更新关联的 File 记录的 session_id
            for media in media_records:
                if media.file_id:
                    file_result = await db.execute(
                        select(DBFile).where(DBFile.id == media.file_id)
                    )
                    file_record = file_result.scalar_one_or_none()
                    
                    if file_record and not file_record.session_id:
                        file_record.session_id = session_id
                        logger.info("Updated File session_id",
                                   file_id=media.file_id,
                                   message_id=message_id,
                                   session_id=session_id)
            
            await db.commit()
            
        except Exception as e:
            logger.error("Failed to update File session_id",
                        message_id=message_id,
                        session_id=session_id,
                        error=str(e))
            await db.rollback()


# 全局下载器实例（按 app_id 缓存）
# 格式：{app_id: MediaDownloader}
_media_downloaders: dict[str, MediaDownloader] = {}


def get_media_downloader(app_id: str = None) -> MediaDownloader:
    """
    获取 MediaDownloader 实例（支持多应用）
    
    Args:
        app_id: 应用 ID，用于获取对应的 FeishuClient
    """
    global _media_downloaders
    
    if app_id:
        if app_id not in _media_downloaders:
            _media_downloaders[app_id] = MediaDownloader(app_id=app_id)
        return _media_downloaders[app_id]
    else:
        # 兼容旧代码，不传 app_id 时使用默认实例
        if None not in _media_downloaders:
            _media_downloaders[None] = MediaDownloader()
        return _media_downloaders[None]