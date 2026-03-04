"""
SSO 认证接口
"""

import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import get_settings
from app.db.engine import async_session
from app.db.models.user import Role, User
from app.security.auth import create_access_token, create_refresh_token
from app.services.user_sync import get_or_create_sso_user

router = APIRouter(prefix="/api/auth", tags=["认证"])
log = structlog.get_logger()
settings = get_settings()


@router.get("/sso/login")
async def sso_login(request: Request, model: str | None = None):
    """重定向到 OA SSO 登录页"""
    SSO_LOGIN_URL = settings.SSO_LOGIN_URL
    callback_url = str(request.url_for("sso_callback"))
    service = f"{callback_url}?model={model}" if model else callback_url
    
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
):
    """OA SSO 回调接口"""
    log.info("SSO 回调", ticket=ticket[:20] + "..." if len(ticket) > 20 else ticket)
    
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
    attributes = parse_cas_xml(response.text)
    if not attributes or "user" not in attributes:
        log.error("SSO 验证失败：无法解析用户属性")
        raise HTTPException(401, "身份验证失败，无法获取用户信息")
    
    log.info("SSO 验证成功", user=attributes.get("user"), company=attributes.get("company"))
    
    # 3. 创建/更新用户
    async with async_session() as session:
        try:
            user = await get_or_create_sso_user(session, attributes)
            await session.commit()
        except Exception as e:
            await session.rollback()
            log.error("SSO 用户同步失败", error=str(e), exc_info=True)
            raise HTTPException(500, f"用户同步失败：{str(e)}")
    
    # 4. 签发 JWT
    access_token = create_access_token(
        sub=str(user.id),
        usernumb=user.usernumb,
        role=user.role.name,
        department=user.department,
        company=user.company,
        permissions=user.role.permissions,
    )
    
    log.info("SSO 登录成功", user=user.usernumb, role=user.role.name)
    
    return {
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


@router.post("/logout")
async def logout():
    """SSO 登出（TODO: 实现 JWT 黑名单）"""
    # TODO: 将 JWT 加入黑名单
    # TODO: 可选重定向到 SSO 登出页
    log.info("用户登出")
    return {"status": "success", "message": "已登出"}


def parse_cas_xml(xml_data: str) -> dict | None:
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
        log.error("XML 解析失败", error=str(e), xml_preview=xml_data[:200])
        return None
