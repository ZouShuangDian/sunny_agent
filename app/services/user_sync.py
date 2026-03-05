"""
SSO 用户验证服务

功能：
- SSO 登录时校验工号是否存在
- 更新用户信息（公司、部门等属性）
- 若工号不存在则报错
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User


async def validate_sso_user(
    session: AsyncSession,
    attributes: dict,
) -> User:
    """
    SSO 登录验证：校验工号是否存在，存在则更新用户信息
    
    Args:
        session: 数据库会话
        attributes: SSO 返回的用户属性
            期望格式:
            {
                "user": "1050627",           # 工号（必填）
                "name": "邹双殿",            # 姓名
                "email": "jtzousd@...",      # 邮箱
                "dept": "技术创新中心",       # 部门
                "company": "舜宇光学科技"     # 公司
            }
    
    Returns:
        User: 用户对象
    
    Raises:
        HTTPException: 如果工号不存在
    """
    usernumb = attributes.get("user")
    if not usernumb:
        raise ValueError("SSO 返回的用户属性缺少 user 字段")
    
    # 1. 查询用户是否存在
    result = await session.execute(
        select(User).where(User.usernumb == usernumb)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail=f"工号 {usernumb} 不存在，请联系管理员创建账户"
        )
    
    # 2. 更新用户信息
    log_update = []
    
    if attributes.get("email") and attributes["email"] != user.email:
        user.email = attributes["email"]
        log_update.append("email")
    
    if attributes.get("dept") and attributes["dept"] != user.department:
        user.department = attributes["dept"]
        log_update.append("department")
    
    if attributes.get("company") and attributes["company"] != user.company:
        user.company = attributes["company"]
        log_update.append("company")
    
    if attributes.get("phone") and attributes["phone"] != user.phone:
        user.phone = attributes["phone"]
        log_update.append("phone")
    
    if attributes.get("avatar_url") and attributes["avatar_url"] != user.avatar_url:
        user.avatar_url = attributes["avatar_url"]
        log_update.append("avatar_url")
    
    # 更新 SSO 登录时间和来源
    user.sso_last_login = datetime.now(timezone.utc)
    user.source = "sso"
    
    if log_update:
        log = __import__("structlog").get_logger()
        log.info("SSO 用户信息更新", usernumb=usernumb, updated_fields=log_update)
    
    return user
