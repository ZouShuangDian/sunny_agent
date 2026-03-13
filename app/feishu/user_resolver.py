"""
用户解析模块
处理 open_id -> employee_no -> user_id 映射
"""

import asyncio
from typing import Optional
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.cache.redis_client import FeishuRedisKeys, redis_client
from app.db.models.user import User
from app.db.models.feishu import FeishuUserBindings
from app.feishu.client import FeishuClient, get_feishu_client

logger = structlog.get_logger()

# 用户缓存 TTL
USER_CACHE_TTL = 3600  # 1小时


class UserResolver:
    """用户解析器"""
    
    def __init__(self, feishu_client: FeishuClient = None):
        self._feishu_clients: dict[str, FeishuClient] = {}
        self._default_client = feishu_client
    
    async def _get_client(self, app_id: str = "", db=None) -> FeishuClient:
        """获取 FeishuClient（支持多应用）
        
        Args:
            app_id: 飞书应用ID，不传则使用默认配置
            db: 数据库会话，用于查询应用配置（优先使用，避免创建新session）
        """
        if app_id not in self._feishu_clients:
            if self._default_client and not app_id:
                # 如果没有指定 app_id 且有默认客户端，使用默认客户端
                # 确保默认客户端已初始化
                await self._default_client._ensure_initialized()
                self._feishu_clients[app_id] = self._default_client
            else:
                # 从 get_feishu_client 获取（会自动初始化）
                # 传入 db 参数，优先使用外部 session 查询 app_secret
                self._feishu_clients[app_id] = await get_feishu_client(app_id, db)
        return self._feishu_clients[app_id]
    
    async def _get_cached_user(self, open_id: str, app_id: str) -> Optional[dict]:
        """从缓存获取用户信息"""
        cache_key = FeishuRedisKeys.user(app_id, open_id)
        cached = await redis_client.get(cache_key)
        if cached:
            import json
            return json.loads(cached)
        return None
    
    async def _cache_user(self, open_id: str, app_id: str, user_info: dict):
        """缓存用户信息"""
        cache_key = FeishuRedisKeys.user(app_id, open_id)
        import json
        await redis_client.setex(
            cache_key,
            USER_CACHE_TTL,
            json.dumps(user_info)
        )
    
    async def resolve_user(
        self,
        db: AsyncSession,
        open_id: str,
        app_id: str,
    ) -> tuple[Optional[User], Optional[str], str]:
        """
        解析用户身份
        
        Returns:
            (系统用户对象, 员工工号, 错误信息)
        """
        # 1. 检查缓存
        cached = await self._get_cached_user(open_id, app_id)
        if cached and cached.get("user_id"):
            user_id = cached.get("user_id")
            # 从数据库获取完整用户对象
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if user:
                return user, cached.get("employee_no"), ""
        
        # 2. 查询本地绑定表
        result = await db.execute(
            select(FeishuUserBindings).where(
                FeishuUserBindings.open_id == open_id,
                FeishuUserBindings.app_id == app_id
            )
        )
        binding = result.scalar_one_or_none()
        
        if binding and binding.is_bound and binding.user_id:
            # 已绑定，返回关联用户
            result = await db.execute(
                select(User).where(User.id == binding.user_id)
            )
            user = result.scalar_one_or_none()
            if user:
                # 更新缓存
                await self._cache_user(open_id, app_id, {
                    "user_id": str(user.id),
                    "employee_no": binding.employee_no,
                })
                return user, binding.employee_no, ""
        
        # 3. 未绑定或绑定失效，从飞书API获取信息
        client = await self._get_client(app_id, db)
        feishu_user = await client.get_user_by_open_id(open_id)
        
        if not feishu_user:
            return None, None, "无法从飞书获取用户信息"
        
        employee_no = feishu_user.get("employee_no")
        if not employee_no:
            return None, None, "用户未设置员工工号"
        
        # 4. 根据employee_no查找系统用户
        result = await db.execute(
            select(User).where(User.usernumb == employee_no)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            return None, employee_no, f"未找到工号为 {employee_no} 的系统用户"
        
        # 5. 创建或更新绑定记录
        if binding:
            # 更新现有绑定
            binding.employee_no = employee_no
            binding.user_id = user.id
            binding.is_bound = True
            binding.feishu_name = feishu_user.get("name")
            binding.feishu_email = feishu_user.get("email")
            binding.feishu_mobile = feishu_user.get("mobile")
            binding.feishu_avatar = feishu_user.get("avatar", {}).get("avatar_origin")
            binding.last_sync_at = datetime.utcnow()
        else:
            # 创建新绑定
            binding = FeishuUserBindings(
                id=uuid7(),
                open_id=open_id,
                union_id=feishu_user.get("union_id"),
                employee_no=employee_no,
                user_id=user.id,
                app_id=app_id,
                feishu_name=feishu_user.get("name"),
                feishu_email=feishu_user.get("email"),
                feishu_mobile=feishu_user.get("mobile"),
                feishu_avatar=feishu_user.get("avatar", {}).get("avatar_origin"),
                is_bound=True,
                last_sync_at=datetime.utcnow(),
            )
            db.add(binding)
        
        await db.commit()
        
        # 6. 更新缓存
        await self._cache_user(open_id, app_id, {
            "user_id": str(user.id),
            "employee_no": employee_no,
        })
        
        logger.info(f"用户绑定成功: {employee_no} -> {user.username}")
        
        return user, employee_no, ""
    
    async def create_binding(
        self,
        db: AsyncSession,
        open_id: str,
        app_id: str,
        employee_no: str,
    ) -> tuple[bool, str]:
        """
        手动创建用户绑定
        
        Returns:
            (是否成功, 消息)
        """
        # 检查用户是否存在
        result = await db.execute(
            select(User).where(User.usernumb == employee_no)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            return False, f"未找到工号为 {employee_no} 的系统用户"
        
        # 检查是否已存在绑定
        result = await db.execute(
            select(FeishuUserBindings).where(
                FeishuUserBindings.open_id == open_id,
                FeishuUserBindings.app_id == app_id
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            # 更新绑定
            existing.user_id = user.id
            existing.employee_no = employee_no
            existing.is_bound = True
            existing.updated_at = datetime.utcnow()
        else:
            # 创建新绑定
            binding = FeishuUserBindings(
                id=uuid7(),
                open_id=open_id,
                employee_no=employee_no,
                user_id=user.id,
                app_id=app_id,
                is_bound=True,
            )
            db.add(binding)
        
        await db.commit()
        
        # 清除缓存
        await redis_client.delete(FeishuRedisKeys.user(app_id, open_id))
        
        return True, f"绑定成功: {employee_no} -> {user.username}"
    
    async def unbind_user(
        self,
        db: AsyncSession,
        open_id: str,
        app_id: str,
    ) -> tuple[bool, str]:
        """
        解除用户绑定
        
        Returns:
            (是否成功, 消息)
        """
        result = await db.execute(
            select(FeishuUserBindings).where(
                FeishuUserBindings.open_id == open_id,
                FeishuUserBindings.app_id == app_id
            )
        )
        binding = result.scalar_one_or_none()
        
        if not binding:
            return False, "未找到绑定记录"
        
        binding.is_bound = False
        binding.user_id = None
        binding.updated_at = datetime.utcnow()
        
        await db.commit()
        
        # 清除缓存
        await redis_client.delete(FeishuRedisKeys.user(app_id, open_id))
        
        return True, "解绑成功"


# 全局用户解析器实例
_user_resolver: UserResolver | None = None


def get_user_resolver() -> UserResolver:
    """获取UserResolver单例"""
    global _user_resolver
    if _user_resolver is None:
        _user_resolver = UserResolver()
    return _user_resolver
