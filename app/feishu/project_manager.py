"""
飞书私聊项目管理器
负责私聊 chat_id 到 Project 的映射和项目管理
"""

import logging
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from uuid6 import uuid7
from app.db.models.project import Project
from app.db.models.feishu import FeishuAccessConfig

logger = logging.getLogger(__name__)


async def get_robot_name(db: AsyncSession, app_id: str) -> str:
    """
    获取机器人名称
    
    优先级：
    1. FeishuAccessConfig.app_name（数据库字段）
    2. app_id 前 8 位（降级方案）
    
    Args:
        db: 数据库会话
        app_id: 飞书应用 ID
    
    Returns:
        str: 机器人名称
    """
    result = await db.execute(
        select(FeishuAccessConfig).where(
            FeishuAccessConfig.app_id == app_id,
            FeishuAccessConfig.is_active == True
        )
    )
    config = result.scalar_one_or_none()
    
    if config and config.app_name:
        return config.app_name
    
    # 降级：使用 app_id 前 8 位
    logger.warning("App name not found, using app_id prefix",
                  app_id=app_id)
    return f"App-{app_id[:8]}"


async def get_or_create_feishu_project(
    db: AsyncSession,
    app_id: str,
    user_id: uuid.UUID,
    company: str | None = None,
) -> Project:
    """
    获取或创建飞书私聊项目
    
    防重逻辑：
    1. 查询：SELECT * FROM projects WHERE name = '飞书-{robot_name}' AND owner_id = {user_id}
    2. 如不存在则创建
    3. 返回 project
    
    Args:
        db: 数据库会话
        app_id: 飞书应用 ID
        user_id: 用户 ID（项目所有者）
        company: 公司标识（可选）
    
    Returns:
        Project: 项目对象
    """
    # 1. 获取机器人名称
    robot_name = await get_robot_name(db, app_id)
    project_name = f"飞书-{robot_name}"
    
    # 2. 查询现有项目
    result = await db.execute(
        select(Project).where(
            Project.name == project_name,
            Project.owner_id == user_id
        )
    )
    project = result.scalar_one_or_none()
    
    if project:
        logger.debug("Found existing Feishu project",
                    project_id=project.id,
                    project_name=project.name,
                    owner_id=user_id)
        return project
    
    # 3. 创建新项目
    project = Project(
        id=uuid7(),
        name=project_name,
        owner_id=user_id,
        company=company or "",
        file_count=0,
        session_count=0,
    )
    
    db.add(project)
    await db.commit()
    await db.refresh(project)
    
    logger.info("Created Feishu private chat project",
               project_id=project.id,
               project_name=project.name,
               owner_id=user_id,
               app_id=app_id)
    
    return project
