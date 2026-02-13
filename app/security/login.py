"""
登录接口：用户名密码登录 + Token 签发 / 刷新 / 注销
"""

import uuid
from datetime import datetime, timezone

import bcrypt
import jwt
import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import RedisKeys, get_redis
from app.config import get_settings
from app.db.engine import get_db
from app.db.models.user import Role, User
from app.security.auth import (
    AuthenticatedUser,
    bearer_scheme,
    create_access_token,
    create_refresh_token,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["认证"])
log = structlog.get_logger()
settings = get_settings()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码"""
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def hash_password(password: str) -> str:
    """生成密码哈希"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


# ── 请求/响应模型 ──

class LoginRequest(BaseModel):
    usernumb: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# ── 接口 ──

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录：校验密码，签发 JWT"""
    result = await db.execute(select(User).where(User.usernumb == body.usernumb))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_pwd):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账户已禁用")

    # 查角色权限
    role = await db.get(Role, user.role_id)

    access_token = create_access_token(
        sub=str(user.id),
        usernumb=user.usernumb,
        role=role.name,
        department=user.department,
        data_scope=user.data_scope,
        permissions=role.permissions,
    )
    refresh_token = create_refresh_token(sub=str(user.id))

    log.info("用户登录成功", usernumb=user.usernumb, role=role.name)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """刷新 Token：用 refresh_token 换取新的 access_token"""
    try:
        payload = jwt.decode(body.refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh Token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效 Refresh Token")

    if payload.get("token_type") != "refresh":
        raise HTTPException(status_code=401, detail="Token 类型错误")

    # 查用户最新信息
    user_id = payload["sub"]
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="用户不存在或已禁用")

    role = await db.get(Role, user.role_id)

    access_token = create_access_token(
        sub=str(user.id),
        usernumb=user.usernumb,
        role=role.name,
        department=user.department,
        data_scope=user.data_scope,
        permissions=role.permissions,
    )
    refresh_token = create_refresh_token(sub=str(user.id))

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout")
async def logout(
    user: AuthenticatedUser = Depends(get_current_user),
    credentials=Depends(bearer_scheme),
    redis_conn: aioredis.Redis = Depends(get_redis),
):
    """注销：将当前 Token 加入黑名单"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效 Token")

    jti = payload.get("jti")
    exp = payload.get("exp")
    if jti and exp:
        # 黑名单 TTL = token 剩余有效时间
        ttl = max(int(exp - datetime.now(timezone.utc).timestamp()), 0)
        await redis_conn.setex(RedisKeys.token_blacklist(jti), ttl, "1")

    log.info("用户注销", usernumb=user.usernumb)
    return {"message": "已注销"}
