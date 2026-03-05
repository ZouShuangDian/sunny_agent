"""
数据过滤器 - 自动注入公司过滤条件

用于在查询时自动应用 ABAC 公司隔离策略
"""

from sqlalchemy import Select
from app.security.auth import AuthenticatedUser


class CompanyFilter:
    """公司数据隔离过滤器"""
    
    @staticmethod
    def apply(query: Select, model, user: AuthenticatedUser) -> Select:
        """
        自动注入公司过滤条件
        
        Args:
            query: SQLAlchemy 查询对象
            model: 要过滤的模型类（必须有 company 字段）
            user: 认证用户
        
        Returns:
            添加了公司过滤条件的查询对象
        """
        # 管理员不受限制
        if "admin" in user.permissions:
            return query
        
        # 普通用户只能看到自己公司的数据
        if hasattr(model, "company"):
            return query.where(model.company == user.company)
        
        return query


class ResourceFilter:
    """资源访问过滤器"""
    
    @staticmethod
    def apply_by_company(query: Select, model, company: str | None) -> Select:
        """
        按公司过滤资源
        
        Args:
            query: SQLAlchemy 查询对象
            model: 要过滤的模型类
            company: 公司名
        
        Returns:
            添加了公司过滤条件的查询对象
        """
        if company and hasattr(model, "company"):
            return query.where(model.company == company)
        
        return query
