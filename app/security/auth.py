"""
JWT 鉴权模块：Token 签发 / 校验 / 黑名单检查
"""

import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

import jwt
import redis.asyncio as aioredis
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.cache.redis_client import RedisKeys, get_redis
from app.config import get_settings

settings = get_settings()
bearer_scheme = HTTPBearer()


@dataclass
class AuthenticatedUser:
    """鉴权后的用户上下文，贯穿整个请求生命周期"""

    id: str
    usernumb: str
    username: str = ""
    role: str = "viewer"
    department: str | None = None
    data_scope: dict = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)


def create_access_token(
    *,
    sub: str,
    usernumb: str,
    role: str,
    department: str | None = None,
    data_scope: dict | None = None,
    permissions: list[str] | None = None,
) -> str:
    """签发 access_token"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "jti": str(uuid.uuid4()),
        "usernumb": usernumb,
        "role": role,
        "department": department,
        "data_scope": data_scope or {},
        "permissions": permissions or [],
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
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

    # 3. 构造用户上下文
    return AuthenticatedUser(
        id=payload["sub"],
        usernumb=payload.get("usernumb", ""),
        username=payload.get("username", ""),
        role=payload.get("role", "viewer"),
        department=payload.get("department"),
        data_scope=payload.get("data_scope", {}),
        permissions=payload.get("permissions", []),
    )
