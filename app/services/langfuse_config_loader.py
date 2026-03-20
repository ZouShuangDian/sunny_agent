"""
Langfuse 配置加载器：DB 为唯一信息源，管理后台 UI 为唯一配置入口
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.langfuse_config import LangfuseConfig
from app.utils.crypto import generate_encryption_key


async def load_langfuse_config(db: AsyncSession) -> LangfuseConfig:
    """
    加载 Langfuse 配置：
    1. DB 记录（initialized=True）→ 直接返回
    2. 无记录 → 创建空/禁用配置，等待管理员通过 UI 配置
    """
    result = await db.execute(select(LangfuseConfig))
    config = result.scalar_one_or_none()

    if config is not None and config.initialized:
        return config

    # 首次启动：确保 ENCRYPTION_KEY 存在（DB 加密需要）
    settings = get_settings()
    if not settings.ENCRYPTION_KEY:
        settings.ENCRYPTION_KEY = generate_encryption_key()

    # 创建空配置，默认关闭，管理员通过 UI 开启并填入 key
    config = LangfuseConfig(
        id=1,
        enabled=False,
        langfuse_host=None,
        langfuse_public_key=None,
        langfuse_secret_key=None,
        sample_rate=1.0,
        flush_interval=5,
        initialized=True,
    )

    db.add(config)
    await db.commit()

    return config
