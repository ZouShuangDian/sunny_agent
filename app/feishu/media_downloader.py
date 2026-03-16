"""
媒体文件下载模块
处理图片、文件、音频等媒体的下载和存储

路径格式统一：{SANDBOX_HOST_VOLUME}/mnt/users/{user_id}/feishu_media/{filename}
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
from app.feishu.context_manager import get_media_context_manager
from app.db.models.feishu import FeishuMediaFiles, MediaType

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
        
        格式: uploads/users/{user_id}/feishu_media/{uuid_prefix}_{safe_filename}
        
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
            
            # 6. 下载成功，创建数据库记录
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
    ) -> Optional[FeishuMediaFiles]:
        """
        带重试的媒体下载
        
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


# ==================== 预下载和 Redis 缓存（多实例兼容）====================

from app.cache.redis_client import redis_client, FeishuRedisKeys

MEDIA_CACHE_TTL = 600  # 10分钟缓存


async def pre_download_media_async(
    app_id: str,
    open_id: str,
    chat_id: str,
    message_id: str,
    file_key: str,
    file_name: str,
    file_type: str,
    user_id: str | UUID,
) -> None:
    """
    异步预下载媒体文件并缓存到 Redis（多实例兼容方案）
    
    用于 Debounce 缓冲期间提前下载媒体文件，下载结果存储在 Redis 中，
    供后续任何 Worker 实例使用。
    
    Args:
        app_id: 应用ID
        open_id: 用户open_id
        chat_id: 聊天ID
        message_id: 消息ID
        file_key: 飞书文件key
        file_name: 文件名
        file_type: 文件类型
        user_id: 用户ID
    """
    cache_key = FeishuRedisKeys.media_cache(app_id, message_id)
    
    try:
        # 检查是否已缓存
        cached = await redis_client.hgetall(cache_key)
        if cached and cached.get("status") == "completed":
            logger.debug("Media already cached", 
                        message_id=message_id, 
                        file_name=file_name)
            return
        
        # 设置下载中状态
        await redis_client.hset(cache_key, mapping={
            "status": "downloading",
            "file_name": file_name,
            "file_type": file_type,
        })
        await redis_client.expire(cache_key, MEDIA_CACHE_TTL)
        
        # 执行下载（传入 app_id 以获取正确的 FeishuClient）
        downloader = get_media_downloader(app_id=app_id)
        
        # 预下载不创建数据库记录，只获取本地路径
        # 使用独立的 session
        from app.db.engine import async_session
        async with async_session() as db:
            media_file = await downloader.download_media(
                db=db,
                file_key=file_key,
                message_id=message_id,
                file_name=file_name,
                file_type=file_type,
                user_id=user_id,
                open_id=open_id,
                chat_id=chat_id,
                app_id=app_id,
            )
        
        if media_file:
            # 下载成功，缓存结果
            await redis_client.hset(cache_key, mapping={
                "status": "completed",
                "local_path": media_file.local_path,
                "file_name": media_file.file_name,
                "file_type": media_file.file_type,
                "file_size": str(media_file.file_size),
            })
            await redis_client.expire(cache_key, MEDIA_CACHE_TTL)
            
            logger.info("Media pre-downloaded and cached",
                       message_id=message_id,
                       file_name=file_name,
                       local_path=media_file.local_path)
        else:
            # 下载失败
            await redis_client.hset(cache_key, mapping={
                "status": "failed",
                "error": "Download returned None",
            })
            await redis_client.expire(cache_key, MEDIA_CACHE_TTL)
            
            logger.warning("Media pre-download failed",
                          message_id=message_id,
                          file_name=file_name)
            
    except Exception as e:
        # 异常处理
        try:
            await redis_client.hset(cache_key, mapping={
                "status": "failed",
                "error": str(e),
            })
            await redis_client.expire(cache_key, MEDIA_CACHE_TTL)
        except:
            pass
        
        logger.error("Media pre-download error",
                    message_id=message_id,
                    file_name=file_name,
                    error=str(e))


async def get_cached_media(message_id: str, app_id: str) -> Optional[dict]:
    """
    从 Redis 缓存获取媒体下载结果
    
    Args:
        message_id: 消息ID
        app_id: 应用ID
        
    Returns:
        媒体信息字典或 None
    """
    cache_key = FeishuRedisKeys.media_cache(app_id, message_id)
    
    try:
        cached = await redis_client.hgetall(cache_key)
        
        if not cached:
            return None
        
        if cached.get("status") == "completed":
            return {
                "local_path": cached.get("local_path"),
                "file_name": cached.get("file_name"),
                "file_type": cached.get("file_type"),
                "file_size": int(cached.get("file_size", 0)),
            }
        
        return None
        
    except Exception as e:
        logger.error("Failed to get cached media",
                    message_id=message_id,
                    error=str(e))
        return None


async def clear_media_cache(message_id: str, app_id: str) -> None:
    """清理媒体缓存（处理完成后调用）"""
    cache_key = FeishuRedisKeys.media_cache(app_id, message_id)
    
    try:
        await redis_client.delete(cache_key)
        logger.debug("Media cache cleared",
                    message_id=message_id)
    except Exception as e:
        logger.warning("Failed to clear media cache",
                      message_id=message_id,
                      error=str(e))
