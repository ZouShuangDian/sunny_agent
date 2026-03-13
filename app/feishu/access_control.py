"""
访问控制模块
处理DM/群组访问策略、白名单验证等
"""

import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import structlog

logger = structlog.get_logger()
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feishu import (
    DMPolicy,
    FeishuAccessConfig,
    FeishuGroupConfig,
    GroupPolicy,
)


class AccessController:
    """访问控制器"""
    
    def __init__(self):
        self._config_cache: Dict[str, dict] = {}
        self._cache_ttl = 300  # 5分钟缓存
        self._cache_timestamp: Dict[str, datetime] = {}
    
    async def _load_config(
        self, 
        db: AsyncSession, 
        app_id: str
    ) -> Optional[FeishuAccessConfig]:
        """加载访问配置"""
        result = await db.execute(
            select(FeishuAccessConfig).where(
                FeishuAccessConfig.app_id == app_id,
                FeishuAccessConfig.is_active == True
            )
        )
        return result.scalar_one_or_none()
    
    async def _get_cached_config(
        self, 
        db: AsyncSession, 
        app_id: str
    ) -> Optional[dict]:
        """获取缓存的配置"""
        now = datetime.utcnow()
        
        # 检查缓存是否有效
        if app_id in self._config_cache:
            cache_time = self._cache_timestamp.get(app_id)
            if cache_time and (now - cache_time).seconds < self._cache_ttl:
                return self._config_cache[app_id]
        
        # 重新加载配置
        config = await self._load_config(db, app_id)
        if not config:
            return None
        
        config_dict = {
            "dm_policy": config.dm_policy,
            "group_policy": config.group_policy,
            "dm_allowlist": config.dm_allowlist,
            "group_allowlist": config.group_allowlist,
            "require_mention": config.require_mention,
            "block_streaming_config": config.block_streaming_config,
            "debounce_config": config.debounce_config,
            "human_like_delay": config.human_like_delay,
        }
        
        self._config_cache[app_id] = config_dict
        self._cache_timestamp[app_id] = now
        
        return config_dict
    
    async def check_dm_access(
        self,
        db: AsyncSession,
        app_id: str,
        employee_no: str,
    ) -> tuple[bool, str]:
        """
        检查私信访问权限
        
        Returns:
            (是否允许, 拒绝原因)
        """
        config = await self._get_cached_config(db, app_id)
        
        if not config:
            return False, "未找到访问配置"
        
        policy = config["dm_policy"]
        
        if policy == DMPolicy.DISABLED.value:
            return False, "私信功能已禁用"
        
        if policy == DMPolicy.OPEN.value:
            return True, ""
        
        if policy == DMPolicy.ALLOWLIST.value:
            allowlist = config.get("dm_allowlist", [])
            if employee_no in allowlist:
                return True, ""
            return False, "您不在白名单中，请联系管理员添加"
        
        return False, "未知的访问策略"
    
    async def check_group_access(
        self,
        db: AsyncSession,
        app_id: str,
        chat_id: str,
        employee_no: Optional[str] = None,
        has_mention: bool = False,
    ) -> tuple[bool, str]:
        """
        检查群组访问权限
        
        Returns:
            (是否允许, 拒绝原因)
        """
        config = await self._get_cached_config(db, app_id)
        
        if not config:
            return False, "未找到访问配置"
        
        policy = config["group_policy"]
        
        if policy == GroupPolicy.DISABLED.value:
            return False, "群聊功能已禁用"
        
        # 检查群组白名单
        if policy == GroupPolicy.ALLOWLIST.value:
            allowlist = config.get("group_allowlist", [])
            if chat_id not in allowlist:
                return False, "该群组不在白名单中"
        
        # 检查是否需要@提及
        require_mention = config.get("require_mention", True)
        if require_mention and not has_mention:
            return False, "请在群聊中@我"
        
        return True, ""
    
    async def get_group_config(
        self,
        db: AsyncSession,
        chat_id: str,
        app_id: str,
    ) -> Optional[FeishuGroupConfig]:
        """获取群组特定配置"""
        result = await db.execute(
            select(FeishuGroupConfig).where(
                FeishuGroupConfig.chat_id == chat_id,
                FeishuGroupConfig.is_active == True
            )
        )
        return result.scalar_one_or_none()
    
    async def get_effective_config(
        self,
        db: AsyncSession,
        app_id: str,
        chat_id: Optional[str] = None,
    ) -> dict:
        """
        获取生效的配置（合并全局配置和群组配置）
        
        Returns:
            合并后的配置字典
        """
        base_config = await self._get_cached_config(db, app_id)
        if not base_config:
            return {}
        
        effective_config = base_config.copy()
        
        # 如果有群组ID，尝试合并群组配置
        if chat_id:
            group_config = await self.get_group_config(db, chat_id, app_id)
            if group_config:
                # 合并覆盖配置
                if group_config.override_block_streaming:
                    effective_config["block_streaming_config"].update(
                        group_config.override_block_streaming
                    )
                if group_config.override_debounce:
                    effective_config["debounce_config"].update(
                        group_config.override_debounce
                    )
                if group_config.override_human_like_delay:
                    effective_config["human_like_delay"].update(
                        group_config.override_human_like_delay
                    )
        
        return effective_config
    
    def invalidate_cache(self, app_id: str):
        """使配置缓存失效"""
        if app_id in self._config_cache:
            del self._config_cache[app_id]
        if app_id in self._cache_timestamp:
            del self._cache_timestamp[app_id]
        logger.info(f"已清除访问配置缓存: {app_id}")
    
    def get_rejection_message(self, reason: str) -> str:
        """获取拒绝消息的友好提示"""
        messages = {
            "私信功能已禁用": "❌ 私信功能当前不可用，请联系管理员开启",
            "您不在白名单中，请联系管理员添加": "❌ 您当前没有使用权限，请联系管理员添加到白名单",
            "群聊功能已禁用": "❌ 群聊功能当前不可用",
            "该群组不在白名单中": "❌ 该群组未授权使用此功能",
            "请在群聊中@我": "👋 请在消息中@我，我才能回复您哦",
            "未找到访问配置": "⚙️ 系统配置错误，请联系管理员",
        }
        return messages.get(reason, f"❌ {reason}")


# 全局访问控制器实例
_access_controller: AccessController | None = None


def get_access_controller() -> AccessController:
    """获取AccessController单例"""
    global _access_controller
    if _access_controller is None:
        _access_controller = AccessController()
    return _access_controller
