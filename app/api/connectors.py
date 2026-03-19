"""
MCP 连接器管理 API

GET    /api/connectors/available     获取可用连接器列表（从 MCP 平台）
POST   /api/connectors/add          添加连接器
GET    /api/connectors               获取个人已添加的连接器列表
PATCH  /api/connectors/{id}          更新连接器开关
PATCH  /api/connectors/{id}/tools/{name}  更新工具开关
POST   /api/connectors/{id}/refresh  刷新工具列表
DELETE /api/connectors/{id}          删除连接器
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.db.engine import get_db
from app.db.models.connector import UserConnector, UserConnectorTool
from app.mcp.client import MCPClient, MCPError
from app.mcp.platform import fetch_available_connectors, generate_tool_prefix
from app.security.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/connectors", tags=["MCP 连接器"])
log = structlog.get_logger()


# ── 请求模型 ──

class AddConnectorRequest(BaseModel):
    connector_id: str = Field(..., description="MCP 平台连接器 ID")
    connector_name: str = Field(..., description="展示名称")
    connector_desc: str | None = Field(None, description="描述")
    connector_code: str | None = Field(None, description="MCP 平台 code")
    classify: str | None = Field(None, description="分类")
    mcp_url: str = Field(..., description="MCP Server URL")
    env: str = Field("2", description="环境选择：1=测试 2=生产")


class ToggleRequest(BaseModel):
    is_enabled: bool


# ── 端点 ──

@router.get("/available")
async def list_available_connectors(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取可用连接器列表（从 MCP 平台 + 自定义），合并本地添加/启用状态"""
    infos = await fetch_available_connectors(user.usernumb)

    # 查本地已添加的 connector_id 集合
    result = await db.execute(
        select(UserConnector.connector_id, UserConnector.is_enabled)
        .where(UserConnector.usernumb == user.usernumb)
    )
    local_map = {str(row.connector_id): row.is_enabled for row in result.all()}

    # 查本地已添加的，用 (connector_id, mcp_url) 作为唯一标识
    result_urls = await db.execute(
        select(UserConnector.connector_id, UserConnector.mcp_url, UserConnector.is_enabled)
        .where(UserConnector.usernumb == user.usernumb)
    )
    local_map = {
        (str(row.connector_id), row.mcp_url): row.is_enabled
        for row in result_urls.all()
    }

    # 每个 URL 拆为一个独立的连接器卡片
    items = []
    for info in infos:
        base_id = str(info.get("id", ""))
        url_list = info.get("urlList", [])
        for u in url_list:
            url = u.get("url", "")
            env = u.get("env", "")
            env_label = "测试" if env == "1" else "生产" if env == "2" else ""
            display_name = info.get("name", "")
            if env_label:
                display_name = f"{display_name}（{env_label}）"

            key = (base_id, url)
            items.append({
                "connector_id": base_id,
                "name": display_name,
                "classify": info.get("classify", ""),
                "classify_name": info.get("classifyName", ""),
                "description": info.get("remark", ""),
                "txxy": info.get("txxy", ""),
                "env": env,
                "mcp_url": url,
                "auth_type": u.get("authType", ""),
                "is_added": key in local_map,
                "is_enabled": local_map.get(key, False),
        })

    return ok(data={"items": items})


@router.post("/add")
async def add_connector(
    body: AddConnectorRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """添加连接器：连接 MCP Server 获取工具列表并存 DB"""
    # 检查是否已添加（同一 connector_id + mcp_url 为唯一）
    existing = await db.execute(
        select(UserConnector.id).where(
            UserConnector.usernumb == user.usernumb,
            UserConnector.connector_id == body.connector_id,
            UserConnector.mcp_url == body.mcp_url,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "该连接器已添加")

    # 自动生成 tool_prefix（从 classify 派生，如 MCP_GX_FOUR → gx_four）
    existing_prefixes_result = await db.execute(
        select(UserConnector.tool_prefix).where(UserConnector.usernumb == user.usernumb)
    )
    existing_prefixes = {row[0] for row in existing_prefixes_result.all()}
    prefix = generate_tool_prefix(body.classify, body.connector_code, existing_prefixes)

    # 写入 user_connectors（字段直接从前端传入，不回查平台）
    connector = UserConnector(
        usernumb=user.usernumb,
        connector_id=body.connector_id,
        connector_code=body.connector_code,
        connector_name=body.connector_name,
        connector_desc=body.connector_desc,
        classify=body.classify,
        mcp_url=body.mcp_url,
        mcp_env=body.env,
        tool_prefix=prefix,
    )
    db.add(connector)

    # 尝试获取工具列表
    tools: list[dict] = []
    warning: str | None = None
    try:
        client = MCPClient(body.mcp_url)
        tools = await client.list_tools()
        log.info("MCP Server 工具列表获取成功", connector_id=body.connector_id, tool_count=len(tools))
    except Exception as e:
        warning = f"连接器已添加，但工具列表获取失败（{e}），请稍后手动刷新"
        log.warning("MCP Server 连接失败", connector_id=body.connector_id, error=str(e))

    # 写入工具列表
    for tool in tools:
        db.add(UserConnectorTool(
            usernumb=user.usernumb,
            connector_id=body.connector_id,
            tool_name=tool.get("name", ""),
            tool_description=tool.get("description"),
            tool_schema=tool.get("inputSchema"),
        ))

    await db.commit()

    return ok(
        data={
            "connector_id": body.connector_id,
            "name": body.connector_name,
            "tool_prefix": prefix,
            "mcp_url": body.mcp_url,
            "tools": [
                {"name": t.get("name"), "description": t.get("description"), "is_enabled": True}
                for t in tools
            ],
        },
        message=warning or "连接器添加成功",
    )


@router.get("")
async def list_my_connectors(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取个人已添加的连接器列表 + 工具列表 + 开关状态"""
    # 查连接器
    connectors_result = await db.execute(
        select(UserConnector)
        .where(UserConnector.usernumb == user.usernumb)
        .order_by(UserConnector.created_at.desc())
    )
    connectors = connectors_result.scalars().all()

    if not connectors:
        return ok(data={"items": []})

    # 批量查工具
    connector_ids = [c.connector_id for c in connectors]
    tools_result = await db.execute(
        select(UserConnectorTool)
        .where(
            UserConnectorTool.usernumb == user.usernumb,
            UserConnectorTool.connector_id.in_(connector_ids),
        )
        .order_by(UserConnectorTool.tool_name)
    )
    tools_all = tools_result.scalars().all()

    # 按 connector_id 分组
    tools_map: dict[str, list] = {}
    for t in tools_all:
        tools_map.setdefault(t.connector_id, []).append({
            "name": t.tool_name,
            "description": t.tool_description,
            "is_enabled": t.is_enabled,
        })

    items = []
    for c in connectors:
        items.append({
            "connector_id": c.connector_id,
            "name": c.connector_name,
            "description": c.connector_desc,
            "classify": c.classify,
            "mcp_url": c.mcp_url,
            "mcp_env": c.mcp_env,
            "tool_prefix": c.tool_prefix,
            "is_enabled": c.is_enabled,
            "tools": tools_map.get(c.connector_id, []),
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })

    return ok(data={"items": items})


@router.patch("/{connector_id}")
async def toggle_connector(
    connector_id: str,
    body: ToggleRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """开启/关闭连接器"""
    result = await db.execute(
        update(UserConnector)
        .where(
            UserConnector.usernumb == user.usernumb,
            UserConnector.connector_id == connector_id,
        )
        .values(is_enabled=body.is_enabled)
    )
    if result.rowcount == 0:
        raise HTTPException(404, "连接器不存在")
    await db.commit()
    return ok(message="ok")


@router.patch("/{connector_id}/tools/{tool_name}")
async def toggle_tool(
    connector_id: str,
    tool_name: str,
    body: ToggleRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """开启/关闭单个工具"""
    result = await db.execute(
        update(UserConnectorTool)
        .where(
            UserConnectorTool.usernumb == user.usernumb,
            UserConnectorTool.connector_id == connector_id,
            UserConnectorTool.tool_name == tool_name,
        )
        .values(is_enabled=body.is_enabled)
    )
    if result.rowcount == 0:
        raise HTTPException(404, "工具不存在")
    await db.commit()
    return ok(message="ok")


@router.post("/{connector_id}/refresh")
async def refresh_tools(
    connector_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """刷新工具列表（重连 MCP Server，按 tool_name diff 合并）"""
    # 查连接器
    connector_result = await db.execute(
        select(UserConnector).where(
            UserConnector.usernumb == user.usernumb,
            UserConnector.connector_id == connector_id,
        )
    )
    connector = connector_result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "连接器不存在")

    # 连接 MCP Server 获取最新工具列表
    try:
        client = MCPClient(connector.mcp_url)
        new_tools = await client.list_tools()
    except (MCPError, Exception) as e:
        raise HTTPException(502, f"MCP Server 连接失败: {e}")

    new_tools_map = {t.get("name", ""): t for t in new_tools}

    # 查现有工具
    existing_result = await db.execute(
        select(UserConnectorTool).where(
            UserConnectorTool.usernumb == user.usernumb,
            UserConnectorTool.connector_id == connector_id,
        )
    )
    existing_tools = existing_result.scalars().all()
    existing_map = {t.tool_name: t for t in existing_tools}

    # diff 合并
    added = 0
    updated = 0
    removed = 0

    # 新增 + 更新
    for name, tool_data in new_tools_map.items():
        if name in existing_map:
            # 更新 description + schema，保留 is_enabled
            existing = existing_map[name]
            existing.tool_description = tool_data.get("description")
            existing.tool_schema = tool_data.get("inputSchema")
            updated += 1
        else:
            # 新增
            db.add(UserConnectorTool(
                usernumb=user.usernumb,
                connector_id=connector_id,
                tool_name=name,
                tool_description=tool_data.get("description"),
                tool_schema=tool_data.get("inputSchema"),
            ))
            added += 1

    # 删除不在新列表中的
    for name in existing_map:
        if name not in new_tools_map:
            await db.execute(
                delete(UserConnectorTool).where(
                    UserConnectorTool.usernumb == user.usernumb,
                    UserConnectorTool.connector_id == connector_id,
                    UserConnectorTool.tool_name == name,
                )
            )
            removed += 1

    await db.commit()

    return ok(data={
        "added": added,
        "updated": updated,
        "removed": removed,
        "total": len(new_tools),
    })


@router.delete("/{connector_id}")
async def delete_connector(
    connector_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除连接器（同时删除关联的工具记录）"""
    # 删工具
    await db.execute(
        delete(UserConnectorTool).where(
            UserConnectorTool.usernumb == user.usernumb,
            UserConnectorTool.connector_id == connector_id,
        )
    )
    # 删连接器
    result = await db.execute(
        delete(UserConnector).where(
            UserConnector.usernumb == user.usernumb,
            UserConnector.connector_id == connector_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(404, "连接器不存在")

    await db.commit()
    return ok(message="已删除")
