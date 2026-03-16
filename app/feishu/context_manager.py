"""
媒体文件上下文管理器
管理最近50条媒体消息，支持历史上下文引用

Redis Key: feishu:media_context:{app_id}:{open_id}:{chat_id}
Type: List (LPUSH 新消息到头部)
TTL: 600 seconds (10分钟)
Max Items: 50
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import UUID

import structlog

from app.cache.redis_client import redis_client, FeishuRedisKeys

logger = structlog.get_logger()

# 配置常量
MAX_CONTEXT_ITEMS = 50
CONTEXT_TTL_SECONDS = 600  # 10分钟


class MediaContextManager:
    """媒体文件上下文管理器"""
    
    def __init__(self):
        pass
    
    def _get_context_key(self, app_id: str, open_id: str, chat_id: str) -> str:
        """生成 Redis Key"""
        return FeishuRedisKeys.media_context(app_id, open_id, chat_id)
    
    def _is_path_valid(self, local_path: str) -> bool:
        """检查文件路径是否存在"""
        try:
            return Path(local_path).exists()
        except Exception:
            return False
    
    async def add_media_message(
        self,
        app_id: str,
        open_id: str,
        chat_id: str,
        message_id: str,
        file_type: str,
        file_name: str,
        local_path: str,
    ) -> None:
        """
        添加媒体消息到上下文
        
        Args:
            app_id: 应用ID
            open_id: 用户open_id
            chat_id: 聊天ID
            message_id: 消息ID
            file_type: 文件类型 (image/file/audio/media/sticker)
            file_name: 文件名
            local_path: 本地存储路径
        """
        try:
            key = self._get_context_key(app_id, open_id, chat_id)
            
            # 构建消息数据
            message_data = {
                "message_id": message_id,
                "file_type": file_type,
                "file_name": file_name,
                "local_path": local_path,
                "timestamp": datetime.utcnow().isoformat(),
            }
            
            # LPUSH 添加到头部（最新的在前面）
            await redis_client.lpush(key, json.dumps(message_data))
            
            # 裁剪到最多50条
            await redis_client.ltrim(key, 0, MAX_CONTEXT_ITEMS - 1)
            
            # 设置/刷新 TTL
            await redis_client.expire(key, CONTEXT_TTL_SECONDS)
            
            logger.debug("Media message added to context",
                        app_id=app_id,
                        open_id=open_id,
                        chat_id=chat_id,
                        message_id=message_id,
                        file_name=file_name)
            
        except Exception as e:
            logger.error("Failed to add media message to context",
                        app_id=app_id,
                        open_id=open_id,
                        chat_id=chat_id,
                        error=str(e))
            # 不抛出异常，上下文失败不应影响主流程
    
    async def get_recent_media(
        self,
        app_id: str,
        open_id: str,
        chat_id: str,
        limit: int = 50,
    ) -> List[dict]:
        """
        获取最近的媒体消息列表
        
        Args:
            app_id: 应用ID
            open_id: 用户open_id
            chat_id: 聊天ID
            limit: 最多返回条数（默认50）
            
        Returns:
            媒体消息列表，按时间倒序（最新的在前）
            每个元素包含：file_type, file_name, local_path, path_exists
        """
        try:
            key = self._get_context_key(app_id, open_id, chat_id)
            
            # 读取列表
            raw_messages = await redis_client.lrange(key, 0, limit - 1)
            
            if not raw_messages:
                return []
            
            result = []
            for raw in raw_messages:
                try:
                    msg = json.loads(raw)
                    local_path = msg.get("local_path", "")
                    
                    # 检查路径是否存在
                    path_exists = self._is_path_valid(local_path)
                    
                    result.append({
                        "message_id": msg.get("message_id"),
                        "file_type": msg.get("file_type"),
                        "file_name": msg.get("file_name"),
                        "local_path": local_path,
                        "path_exists": path_exists,
                        "timestamp": msg.get("timestamp"),
                    })
                except json.JSONDecodeError:
                    logger.warning("Failed to decode context message", raw=raw)
                    continue
            
            logger.debug("Recent media context loaded",
                        app_id=app_id,
                        open_id=open_id,
                        chat_id=chat_id,
                        count=len(result))
            
            return result
            
        except Exception as e:
            logger.error("Failed to get recent media context",
                        app_id=app_id,
                        open_id=open_id,
                        chat_id=chat_id,
                        error=str(e))
            return []
    
    async def clear_context(
        self,
        app_id: str,
        open_id: str,
        chat_id: str,
    ) -> None:
        """
        清除上下文（可选，用于清理场景）
        
        Args:
            app_id: 应用ID
            open_id: 用户open_id
            chat_id: 聊天ID
        """
        try:
            key = self._get_context_key(app_id, open_id, chat_id)
            await redis_client.delete(key)
            
            logger.info("Media context cleared",
                       app_id=app_id,
                       open_id=open_id,
                       chat_id=chat_id)
            
        except Exception as e:
            logger.error("Failed to clear media context",
                        app_id=app_id,
                        open_id=open_id,
                        chat_id=chat_id,
                        error=str(e))


# 全局实例
_media_context_manager: MediaContextManager | None = None


def get_media_context_manager() -> MediaContextManager:
    """获取 MediaContextManager 单例"""
    global _media_context_manager
    if _media_context_manager is None:
        _media_context_manager = MediaContextManager()
    return _media_context_manager
