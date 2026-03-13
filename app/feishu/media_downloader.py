"""
媒体文件下载模块
处理图片、文件、音频等媒体的下载和存储

路径格式统一：{SANDBOX_HOST_VOLUME}/uploads/users/{user_id}/feishu_media/{filename}
"""

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.config import get_settings
from app.feishu.client import FeishuClient, get_feishu_client
from app.db.models.feishu import FeishuMediaFiles, MediaType

settings = get_settings()
logger = structlog.get_logger()

# 媒体文件存储目录（统一路径格式）
# 格式: {SANDBOX_HOST_VOLUME}/uploads/users/{user_id}/feishu_media/
BASE_UPLOAD_DIR = Path(settings.SANDBOX_HOST_VOLUME) / "uploads"
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
    
    def __init__(self, feishu_client: FeishuClient = None):
        self.feishu_client = feishu_client or get_feishu_client()
    
    def _get_storage_path(self, user_id: str | UUID, file_name: str) -> Path:
        """
        生成存储路径（统一格式）
        
        格式: uploads/users/{user_id}/feishu_media/{safe_filename}
        
        Args:
            user_id: 系统用户ID (UUID)
            file_name: 原始文件名
            
        Returns:
            Path: 完整存储路径
        """
        if isinstance(user_id, UUID):
            user_id = str(user_id)
        
        # 统一路径格式: uploads/users/{user_id}/feishu_media/
        user_dir = BASE_UPLOAD_DIR / "users" / user_id / "feishu_media"
        user_dir.mkdir(parents=True, exist_ok=True)
        
        # 安全文件名（防止路径遍历）
        safe_filename = Path(file_name).name
        # 移除危险字符
        safe_filename = "".join(c for c in safe_filename if c.isalnum() or c in "._- ")
        
        file_path = user_dir / safe_filename
        
        return file_path
    
    async def _check_existing_file(
        self,
        db: AsyncSession,
        file_key: str,
        message_id: str,
        user_id: str | UUID,
        file_name: str,
    ) -> Optional[FeishuMediaFiles]:
        """
        检查是否已存在相同的文件或同名文件
        
        Returns:
            如果找到已存在的记录，返回该记录；否则返回 None
        """
        # 1. 检查相同 file_key 和 message_id（同一消息中的同一文件）
        result = await db.execute(
            select(FeishuMediaFiles).where(
                FeishuMediaFiles.file_key == file_key,
                FeishuMediaFiles.message_id == message_id,
                FeishuMediaFiles.download_status == "completed"
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing
        
        # 2. 检查同名文件（覆盖逻辑）
        # 计算目标路径
        target_path = self._get_storage_path(user_id, file_name)
        
        result = await db.execute(
            select(FeishuMediaFiles).where(
                FeishuMediaFiles.local_path == str(target_path),
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
    ) -> FeishuMediaFiles:
        """
        下载媒体文件
        
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
            
        Returns:
            FeishuMediaFiles 记录
        """
        # 1. 检查是否已存在（相同 file_key + message_id）
        existing = await self._check_existing_file(
            db, file_key, message_id, user_id, file_name
        )
        
        if existing and existing.file_key == file_key and existing.message_id == message_id:
            # 同一消息中的同一文件，直接返回
            logger.info("Duplicate media file detected (same message)", 
                       file_key=file_key, 
                       existing_id=str(existing.id))
            return existing
        
        # 2. 生成存储路径（统一格式）
        local_path = self._get_storage_path(user_id, file_name)
        
        # 3. 检查是否是覆盖场景（同名文件）
        is_overwrite = existing is not None and existing.local_path == str(local_path)
        if is_overwrite:
            logger.info("Overwriting existing file",
                       file_name=file_name,
                       existing_id=str(existing.id))
        
        # 4. 创建或更新下载记录
        if is_overwrite:
            # 复用现有记录，更新信息
            media_file = existing
            media_file.file_key = file_key
            media_file.message_id = message_id
            media_file.file_type = file_type
            media_file.open_id = open_id
            media_file.chat_id = chat_id
            media_file.mime_type = mime_type
            media_file.download_status = "downloading"
            media_file.is_duplicate = False
            media_file.duplicate_of = None
        else:
            # 创建新记录
            media_file = FeishuMediaFiles(
                id=uuid7(),
                file_key=file_key,
                file_name=file_name,
                file_type=file_type,
                message_id=message_id,
                open_id=open_id,
                chat_id=chat_id,
                mime_type=mime_type,
                download_status="downloading",
            )
            db.add(media_file)
        
        await db.flush()
        
        try:
            # 5. 下载文件
            logger.info("Starting media download",
                       file_key=file_key,
                       file_name=file_name,
                       file_type=file_type,
                       is_overwrite=is_overwrite)
            
            file_data = await self.feishu_client.download_media(file_key, message_id)
            
            # 6. 检查文件大小
            file_size = len(file_data)
            if file_size > MAX_FILE_SIZE:
                raise FileTooLargeError(
                    f"文件大小 {file_size} 超过限制 {MAX_FILE_SIZE}"
                )
            
            # 7. 计算SHA256
            sha256_hash = hashlib.sha256(file_data).hexdigest()
            
            # 8. 检查SHA256是否重复（同一文件不同消息）
            result = await db.execute(
                select(FeishuMediaFiles).where(
                    FeishuMediaFiles.sha256_hash == sha256_hash,
                    FeishuMediaFiles.download_status == "completed",
                    FeishuMediaFiles.id != media_file.id  # 排除当前记录
                )
            )
            sha_duplicate = result.scalar_one_or_none()
            
            if sha_duplicate and not is_overwrite:
                # 标记为重复文件（不重复保存）
                media_file.is_duplicate = True
                media_file.duplicate_of = sha_duplicate.id
                media_file.local_path = sha_duplicate.local_path
                media_file.file_size = sha_duplicate.file_size
                media_file.sha256_hash = sha256_hash
                media_file.download_status = "completed"
                
                logger.info("SHA256 duplicate detected",
                           file_key=file_key,
                           duplicate_of=str(sha_duplicate.id))
            else:
                # 9. 保存文件（覆盖或新建）
                local_path.write_bytes(file_data)
                
                media_file.local_path = str(local_path)
                media_file.file_size = file_size
                media_file.sha256_hash = sha256_hash
                media_file.download_status = "completed"
                media_file.is_duplicate = False
                media_file.duplicate_of = None
                
                logger.info("Media download completed",
                           file_key=file_key,
                           file_size=file_size,
                           local_path=str(local_path),
                           is_overwrite=is_overwrite)
            
            await db.commit()
            await db.refresh(media_file)
            return media_file
            
        except FileTooLargeError as e:
            media_file.download_status = "failed"
            media_file.download_error = str(e)
            await db.commit()
            logger.error("File too large",
                        file_key=file_key,
                        error=str(e))
            raise
            
        except Exception as e:
            media_file.download_status = "failed"
            media_file.download_error = str(e)
            media_file.download_retry_count += 1
            await db.commit()
            
            logger.error("Media download failed",
                        file_key=file_key,
                        error=str(e),
                        retry_count=media_file.download_retry_count)
            raise MediaDownloadError(f"下载失败: {e}")
    
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
        max_retries: int = 3,
    ) -> Optional[FeishuMediaFiles]:
        """
        带重试的媒体下载
        
        Returns:
            FeishuMediaFiles 或 None（如果全部重试失败）
        """
        import asyncio
        
        for attempt in range(max_retries):
            try:
                return await self.download_media(
                    db=db,
                    file_key=file_key,
                    message_id=message_id,
                    file_name=file_name,
                    file_type=file_type,
                    user_id=user_id,
                    open_id=open_id,
                    chat_id=chat_id,
                    mime_type=mime_type,
                )
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Download attempt {attempt + 1} failed, retrying in {wait_time}s",
                                 file_key=file_key,
                                 error=str(e))
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"All {max_retries} download attempts failed",
                               file_key=file_key,
                               error=str(e))
                    return None
        
        return None


# 全局下载器实例
_media_downloader: MediaDownloader | None = None


def get_media_downloader() -> MediaDownloader:
    """获取MediaDownloader单例"""
    global _media_downloader
    if _media_downloader is None:
        _media_downloader = MediaDownloader()
    return _media_downloader
