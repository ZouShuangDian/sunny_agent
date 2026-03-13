"""
JWT 鉴权模块：Token 签发 / 校验 / 黑名单检查
"""

import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

import jwt
import redis.asyncio as aioredis
import structlog
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.cache.redis_client import RedisKeys, get_redis
from app.config import get_settings
from app.utils.token_util import hash_password, verify_password

log = structlog.get_logger()

settings = get_settings()
bearer_scheme = HTTPBearer()
bearer_scheme_optional = HTTPBearer(auto_error=False)


@dataclass
class AuthenticatedUser:
    """鉴权后的用户上下文，贯穿整个请求生命周期"""

    id: str
    usernumb: str
    username: str = ""
    role: str = "viewer"
    department: str | None = None
    company: str | None = None
    data_scope: dict = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)


def is_super_admin(user: AuthenticatedUser) -> bool:
    """检查用户是否为超级管理员"""
    return "admin" in user.permissions or "*" in user.permissions


def create_access_token(
    *,
    sub: str,
    usernumb: str,
    role: str,
    department: str | None = None,
    data_scope: dict | None = None,
    permissions: list[str] | None = None,
    company: str | None = None,
    expires_in_seconds: int | None = None,
) -> str:
    """签发 access_token
    
    Args:
        expires_in_seconds: 自定义过期时间（秒），如果不指定则使用默认配置
    """
    now = datetime.now(timezone.utc)
    
    # 计算过期时间
    if expires_in_seconds:
        expires_delta = timedelta(seconds=expires_in_seconds)
    else:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    payload = {
        "sub": sub,
        "jti": str(uuid.uuid4()),
        "usernumb": usernumb,
        "role": role,
        "department": department,
        "data_scope": data_scope or {},
        "permissions": permissions or [],
        "company": company,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(*, sub: str) -> str:
    """签发 refresh_token"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "jti": str(uuid.uuid4()),
        "token_type": "refresh",
        "iat": now,
        "exp": now + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    redis: aioredis.Redis = Depends(get_redis),
) -> AuthenticatedUser:
    """FastAPI 依赖注入：校验 JWT 并返回用户上下文"""
    token = credentials.credentials

    # 1. 解码 JWT
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效 Token")

    # 2. 检查黑名单（已注销的 Token）
    jti = payload.get("jti")
    if jti and await redis.exists(RedisKeys.token_blacklist(jti)):
        raise HTTPException(status_code=401, detail="Token 已注销")

    # 3. 校验用户在 DB 中存在且激活（Redis 缓存 10 分钟）
    user_id = payload["sub"]
    cache_key = RedisKeys.user_active(user_id)
    cached = await redis.get(cache_key)
    if not cached:
        # 缓存未命中，查 DB
        from app.db.engine import async_session
        from app.db.models.user import User
        async with async_session() as session:
            result = await session.execute(
                select(User.is_active).where(User.id == uuid.UUID(user_id))
            )
            row = result.scalar_one_or_none()

        if row is None:
            log.warning("JWT 用户在 DB 中不存在", user_id=user_id)
            raise HTTPException(status_code=401, detail="用户不存在，请重新登录")
        if not row:
            raise HTTPException(status_code=403, detail="账户已禁用")

        # 写入缓存，10 分钟过期
        await redis.setex(cache_key, 600, "1")

    # 4. 构造用户上下文
    return AuthenticatedUser(
        id=user_id,
        usernumb=payload.get("usernumb", ""),
        username=payload.get("username", ""),
        role=payload.get("role", "viewer"),
        department=payload.get("department"),
        company=payload.get("company"),
        data_scope=payload.get("data_scope", {}),
        permissions=payload.get("permissions", []),
    )


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme_optional),
    redis: aioredis.Redis = Depends(get_redis),
) -> AuthenticatedUser | None:
    """
    可选的当前用户依赖
    
    用于支持可选认证的端点，如带 token 的下载接口
    如果没有提供凭证，返回 None
    """
    if not credentials:
        return None
    
    token = credentials.credentials
    
    # 1. 解码 JWT
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效 Token")
    
    # 2. 检查黑名单（已注销的 Token）
    jti = payload.get("jti")
    if jti and await redis.exists(RedisKeys.token_blacklist(jti)):
        raise HTTPException(status_code=401, detail="Token 已注销")

    # 3. 构造用户上下文
    return AuthenticatedUser(
        id=payload["sub"],
        usernumb=payload.get("usernumb", ""),
        username=payload.get("username", ""),
        role=payload.get("role", "viewer"),
        department=payload.get("department"),
        company=payload.get("company"),
        data_scope=payload.get("data_scope", {}),
        permissions=payload.get("permissions", []),
    )


async def verify_download_token(token: str) -> AuthenticatedUser:
    """
    验证临时下载令牌

    Args:
        token: JWT 令牌

    Returns:
        AuthenticatedUser: 令牌中的用户信息

    Raises:
        HTTPException: 令牌无效或过期
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])

        # 检查令牌是否过期
        exp = payload.get("exp")
        if exp:
            if datetime.now(timezone.utc).timestamp() > exp:
                raise HTTPException(status_code=401, detail="下载链接已过期")

        # 检查令牌权限
        permissions = payload.get("permissions", [])
        if "file_download" not in permissions:
            raise HTTPException(status_code=403, detail="无效的下载令牌")

        # 构建用户对象
        return AuthenticatedUser(
            id=payload["sub"],
            usernumb=payload.get("usernumb", ""),
            username=payload.get("username", ""),
            role=payload.get("role", "user"),
            company=payload.get("company"),
            permissions=permissions,
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="下载链接已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=403, detail="无效的下载令牌")
