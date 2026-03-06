"""
登录接口：用户名密码登录 + Token 签发 / 刷新 / 注销 + SSO 单点登录
"""

import asyncio
import json
import httpx
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode

import bcrypt
import jwt
import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import RedisKeys, get_redis
from app.config import get_settings
from app.db.engine import async_session, get_db
from app.db.models.user import Role, User
from app.api.response import ApiResponse, ok
from app.utils.token_util import hash_password, verify_password

from app.security.auth import (
    AuthenticatedUser,
    bearer_scheme,
    create_access_token,
    create_refresh_token,
    get_current_user,
)
from app.services.user_sync import validate_sso_user

router = APIRouter(prefix="/auth", tags=["认证"])
log = structlog.get_logger()
settings = get_settings()




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

@router.post("/login", response_model=ApiResponse[TokenResponse])
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
    return ok(data=TokenResponse(access_token=access_token, refresh_token=refresh_token))


@router.post("/refresh", response_model=ApiResponse[TokenResponse])
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

    return ok(data=TokenResponse(access_token=access_token, refresh_token=refresh_token))


@router.post("/logout", response_model=ApiResponse)
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
    return ok(message="已注销")


# ──────────────────────────────────────────────────────────────
# SSO 单点登录接口
# ──────────────────────────────────────────────────────────────

@router.get("/sso/login")
async def sso_login(request: Request, model: str | None = None):
    """重定向到 OA SSO 登录页"""
    settings = get_settings()
    SSO_LOGIN_URL = settings.SSO_LOGIN_URL
    callback_url = str(request.url_for("sso_callback"))
    service = f"{callback_url}?model={model}" if model else callback_url
    
    log = structlog.get_logger()
    log.info("SSO 登录重定向", service=service)
    
    return RedirectResponse(
        url=f"{SSO_LOGIN_URL}?{urlencode({'service': service})}"
    )


@router.get("/sso/callback")
async def sso_callback(
    request: Request,
    ticket: str,
    service: str,
    model: str | None = None,
    redis_conn: aioredis.Redis = Depends(get_redis),
):
    """OA SSO 回调接口"""
    log = structlog.get_logger()
    log.info("SSO 回调", ticket=ticket[:20] + "..." if len(ticket) > 20 else ticket)
    
    settings = get_settings()
    result_key = RedisKeys.sso_ticket_result(ticket)
    lock_key = RedisKeys.sso_ticket_lock(ticket)

    cached_payload = await redis_conn.get(result_key)
    if cached_payload:
        log.info("SSO ticket cache hit")
        return ok(data=json.loads(cached_payload), message="sso 单点登录成功")

    lock_acquired = await redis_conn.set(lock_key, "1", ex=15, nx=True)
    if not lock_acquired:
        for _ in range(10):
            await asyncio.sleep(0.2)
            cached_payload = await redis_conn.get(result_key)
            if cached_payload:
                log.info("SSO ticket cache hit after wait")
                return ok(data=json.loads(cached_payload), message="sso 单点登录成功")
        raise HTTPException(409, "SSO ticket 正在处理中，请稍后重试")

    
    # 1. 验证 ticket
    SSO_VALIDATE_URL = settings.SSO_VALIDATE_URL
    validation_url = f"{SSO_VALIDATE_URL}?ticket={ticket}&service={service}"
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(validation_url)
            response.raise_for_status()
    except httpx.HTTPError as e:
        log.error("SSO ticket 验证失败", error=str(e))
        raise HTTPException(503, f"SSO 验证服务不可用：{str(e)}")
    
    # 2. 解析 XML
    attributes = _parse_cas_xml(response.text)
    if not attributes or "user" not in attributes:
        log.error("SSO 验证失败：无法解析用户属性")
        raise HTTPException(401, "身份验证失败，无法获取用户信息")
    
    log.info("SSO 验证成功", user=attributes.get("user"), company=attributes.get("company"))
    
    # 3. 创建/更新用户
    async with async_session() as session:
        try:
            user = await validate_sso_user(session, attributes)
            await session.commit()
            
            # 预加载 role 属性，避免 DetachedInstanceError
            await session.refresh(user, attribute_names=['role'])
            
            # 4. 签发 JWT（在 session 有效期内）
            access_token = create_access_token(
                sub=str(user.id),
                usernumb=user.usernumb,
                role=user.role.name,
                department=user.department,
                company=user.company,
                permissions=user.role.permissions,
            )
            
            log.info("SSO 登录成功", user=user.usernumb, role=user.role.name)
            
            response_data = {
                "access_token": access_token,
                "refresh_token": create_refresh_token(sub=str(user.id)),
                "token_type": "bearer",
                "user": {
                    "id": str(user.id),
                    "usernumb": user.usernumb,
                    "username": user.username,
                    "email": user.email,
                    "company": user.company,
                    "department": user.department,
                    "role": user.role.name,
                }
            }
            await redis_conn.setex(result_key, 120, json.dumps(response_data, ensure_ascii=False))
            return ok(data=response_data, message="sso 单点登录成功")

        except Exception as e:
            await session.rollback()
            log.error("SSO 登录失败", error=str(e), exc_info=True)
            raise HTTPException(500, f"登录失败：{str(e)}")
        finally:
            if lock_acquired:
                await redis_conn.delete(lock_key)


def _parse_cas_xml(xml_data: str) -> dict | None:
    """
    解析 CAS SSO XML 响应
    
    期望格式:
    <cas:authenticationSuccess>
        <cas:user>1050627</cas:user>
        <cas:attributes>
            <cas:employeeNo>1050627</cas:employeeNo>
            <cas:name>邹双殿</cas:name>
            <cas:email>jtzousd@sunnyoptical.com</cas:email>
            <cas:dept>技术创新中心</cas:dept>
            <cas:company>舜宇光学科技</cas:company>
        </cas:attributes>
    </cas:authenticationSuccess>
    """
    try:
        root = ET.fromstring(xml_data)
        ns = {"cas": "http://www.yale.edu/tp/cas"}
        
        # 查找 authenticationSuccess
        auth_success = root.find(".//cas:authenticationSuccess", ns)
        if not auth_success:
            # 检查是否认证失败
            auth_failure = root.find(".//cas:authenticationFailure", ns)
            if auth_failure:
                error = auth_failure.text
                log = structlog.get_logger()
                log.error("SSO 认证失败", error=error)
            return None
        
        attributes = {}
        
        # 提取 user
        user_elem = auth_success.find("cas:user", ns)
        if user_elem is not None and user_elem.text:
            attributes["user"] = user_elem.text
        
        # 提取 attributes
        attr_elem = auth_success.find("cas:attributes", ns)
        if attr_elem is not None:
            for child in attr_elem:
                tag = child.tag.split("}")[-1]  # 去除命名空间
                if child.text:
                    attributes[tag] = child.text
        
        return attributes
    except ET.ParseError as e:
        log = structlog.get_logger()
        log.error("XML 解析失败", error=str(e), xml_preview=xml_data[:200])
        return None
