"""
ABAC 权限检查器

基于属性的访问控制（Attribute-Based Access Control）
用于实现公司级数据隔离
"""

from dataclasses import dataclass


@dataclass
class ABACResult:
    """ABAC 检查结果"""
    allowed: bool
    reason: str | None = None


async def check_company_isolation(
    user_company: str | None,
    target_company: str,
    user_permissions: list[str],
) -> ABACResult:
    """
    检查公司数据隔离
    
    Args:
        user_company: 用户所属公司
        target_company: 目标数据所属公司
        user_permissions: 用户权限列表
    
    Returns:
        ABACResult: 检查结果
    """
    # 管理员豁免
    if "admin" in user_permissions:
        return ABACResult(allowed=True)
    
    # 公司匹配检查
    if not user_company:
        return ABACResult(
            allowed=False,
            reason="用户未设置公司属性，无法访问数据"
        )
    
    if user_company != target_company:
        return ABACResult(
            allowed=False,
            reason=f"无权访问{target_company}的数据，您属于{user_company}"
        )
    
    return ABACResult(allowed=True)


async def check_data_access(
    user_company: str | None,
    user_permissions: list[str],
    resource_company: str | None,
    resource_type: str,
) -> ABACResult:
    """
    通用数据访问检查
    
    Args:
        user_company: 用户所属公司
        user_permissions: 用户权限列表
        resource_company: 资源所属公司
        resource_type: 资源类型（chat_messages, chat_sessions, files 等）
    
    Returns:
        ABACResult: 检查结果
    """
    # 管理员豁免
    if "admin" in user_permissions:
        return ABACResult(allowed=True)
    
    # 资源无公司属性（系统级资源）
    if not resource_company:
        return ABACResult(allowed=True)
    
    # 公司隔离检查
    if not user_company:
        return ABACResult(
            allowed=False,
            reason="用户未设置公司属性"
        )
    
    if user_company != resource_company:
        return ABACResult(
            allowed=False,
            reason=f"跨公司访问被拒绝：{user_company} -> {resource_company}"
        )
    
    return ABACResult(allowed=True)
