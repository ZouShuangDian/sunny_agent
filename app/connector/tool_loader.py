"""
MCP 工具加载器 — 对话时从 DB 加载已启用的连接器工具 schema

在 L3ReActEngine._build_context() 中调用，将 MCP 工具 schema 合并到内置工具中。
"""

import structlog
from sqlalchemy import select

from app.db.engine import async_session
from app.db.models.connector import UserConnector, UserConnectorTool

log = structlog.get_logger()


async def load_mcp_tool_schemas(usernumb: str) -> list[dict]:
    """查 DB 加载用户已启用的 MCP 工具 schema（OpenAI function calling 格式）

    Args:
        usernumb: 用户工号

    Returns:
        OpenAI function calling 格式的工具 schema 列表
    """
    if not usernumb:
        return []

    async with async_session() as db:
        result = await db.execute(
            select(UserConnector, UserConnectorTool)
            .join(
                UserConnectorTool,
                (UserConnector.connector_id == UserConnectorTool.connector_id)
                & (UserConnector.usernumb == UserConnectorTool.usernumb),
            )
            .where(
                UserConnector.usernumb == usernumb,
                UserConnector.is_enabled == True,   # noqa: E712
                UserConnectorTool.is_enabled == True,  # noqa: E712
            )
        )
        rows = result.all()

    if not rows:
        return []

    schemas = []
    for connector, tool in rows:
        # 工具名：{prefix}__{tool_name}，双下划线分隔
        full_name = f"{connector.tool_prefix}__{tool.tool_name}"
        schemas.append({
            "type": "function",
            "function": {
                "name": full_name,
                "description": f"[{connector.connector_name}] {tool.tool_description or tool.tool_name}",
                "parameters": tool.tool_schema or {"type": "object", "properties": {}},
            },
        })

    log.debug("MCP 工具加载完成", usernumb=usernumb, tool_count=len(schemas))
    return schemas
