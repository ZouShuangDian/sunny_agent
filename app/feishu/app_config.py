"""
飞书应用配置管理
提供从数据库获取应用凭证的方法
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.db.models.feishu import FeishuAccessConfig
from app.cache.redis_client import redis_client

logger = get_logger(__name__)

# 缓存键前缀
CACHE_PREFIX = "feishu:app_secret"
CACHE_TTL = 3600  # 缓存1小时


async def get_app_secret_from_db(app_id: str, db: AsyncSession) -> Optional[str]:
    """
    从数据库获取飞书应用密钥
    
    Args:
        app_id: 飞书应用ID
        db: 数据库会话
        
    Returns:
        应用密钥，如果应用不存在或未激活则返回None
    """
    # 先检查缓存
    cache_key = f"{CACHE_PREFIX}:{app_id}"
    try:
        cached_secret = await redis_client.get(cache_key)
        if cached_secret:
            return cached_secret.decode('utf-8') if isinstance(cached_secret, bytes) else cached_secret
    except Exception as e:
        logger.warning("Failed to get app_secret from cache", app_id=app_id, error=str(e))
    
    # 查询数据库
    stmt = select(FeishuAccessConfig).where(
        FeishuAccessConfig.app_id == app_id,
        FeishuAccessConfig.is_active == True
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    
    if config and config.app_secret:
        # 写入缓存
        try:
            await redis_client.setex(cache_key, CACHE_TTL, config.app_secret)
        except Exception as e:
            logger.warning("Failed to cache app_secret", app_id=app_id, error=str(e))
        return config.app_secret
    
    return None


async def get_all_active_apps(db: AsyncSession) -> list[FeishuAccessConfig]:
    """
    获取所有活跃的飞书应用配置
    
    Args:
        db: 数据库会话
        
    Returns:
        活跃的应用配置列表
    """
    stmt = select(FeishuAccessConfig).where(
        FeishuAccessConfig.is_active == True
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def invalidate_app_secret_cache(app_id: str) -> None:
    """
    使应用密钥缓存失效
    
    Args:
        app_id: 飞书应用ID
    """
    cache_key = f"{CACHE_PREFIX}:{app_id}"
    try:
        await redis_client.delete(cache_key)
    except Exception as e:
        logger.warning("Failed to invalidate app_secret cache", app_id=app_id, error=str(e))