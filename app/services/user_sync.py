"""
SSO 用户同步服务

功能：
- SSO 首次登录自动创建用户
- 非首次登录更新用户信息
- 同步公司、部门等属性
"""

from datetime import datetime, timezone
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import Role, User
from app.security.auth import hash_password


async def get_or_create_sso_user(
    session: AsyncSession,
    attributes: dict,
) -> User:
    """
    SSO 首次登录自动创建用户，非首次则更新信息
    
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
        HTTPException: 如果"普通用户"角色不存在
    """
    usernumb = attributes.get("user")
    if not usernumb:
        raise ValueError("SSO 返回的用户属性缺少 user 字段")
    
    # 1. 查询是否已存在
    result = await session.execute(
        select(User).where(User.usernumb == usernumb)
    )
    user = result.scalar_one_or_none()
    
    if user:
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
        
        # 更新 SSO 登录时间
        user.sso_last_login = datetime.now(timezone.utc)
        user.source = "sso"
        
        if log_update:
            from app.config import get_settings
            log = __import__("structlog").get_logger()
            log.info("SSO 用户信息更新", usernumb=usernumb, updated_fields=log_update)
        
        return user
    
    # 3. 创建新用户
    # 查询"普通用户"角色
    result = await session.execute(
        select(Role).where(Role.name == "user")
    )
    role = result.scalar_one_or_none()
    
    if not role:
        raise HTTPException(500, "角色'普通用户'不存在，请联系管理员")
    
    # 创建用户
    user = User(
        usernumb=usernumb,
        username=attributes.get("name", usernumb),
        email=attributes.get("email"),
        department=attributes.get("dept"),
        company=attributes.get("company"),
        phone=attributes.get("phone"),
        avatar_url=attributes.get("avatar_url"),
        role_id=role.id,
        source="sso",
        hashed_pwd=generate_random_password(),
        is_active=True,
    )
    
    session.add(user)
    await session.flush()
    
    from app.config import get_settings
    log = __import__("structlog").get_logger()
    log.info("SSO 首次登录创建用户", usernumb=usernumb, role=role.name)
    
    return user


def generate_random_password() -> str:
    """
    生成默认密码哈希（所有新用户默认密码 123456）
    """
    return hash_password("123456")

# 避免循环导入，延迟导入 HTTPException
from fastapi import HTTPException
