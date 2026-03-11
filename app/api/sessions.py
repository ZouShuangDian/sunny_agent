"""
会话与消息历史 API

提供历史会话列表、消息内容查看、会话归档、标题修改等能力。
所有查询从 PG 读取（source of truth），不涉及 Redis WorkingMemory。
"""

import json
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.cache.redis_client import RedisKeys, get_redis
from app.db.engine import get_db
from app.db.models.chat import ChatMessage, ChatSession, L3Step as L3StepModel
from app.security.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/sessions", tags=["会话历史"])
log = structlog.get_logger()

# ── 常量 ────────────────────────────────────────────────────────────────────

TOOL_CALL_RESULT_MAX_LEN = 500
"""tool_calls.result 超过此长度时截断"""

L3_STEP_CONTENT_MAX_LEN = 1000
"""L3 step content 超过此长度时截断"""


# ── Pydantic 响应模型 ──────────────────────────────────────────────────────

class SessionItem(BaseModel):
    session_id: str
    title: str | None
    turn_count: int
    status: str
    source: str = "chat"
    created_at: datetime
    last_active_at: datetime
    project_id: str | None = None


class SessionListResponse(BaseModel):
    items: list[SessionItem]
    total: int
    page: int
    page_size: int


class ToolCallItem(BaseModel):
    tool_name: str
    arguments: dict
    result: str | None = None
    status: str
    duration_ms: int | None = None


class L3StepItem(BaseModel):
    step_index: int
    role: str
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: dict | None = None


class MessageItem(BaseModel):
    message_id: str
    role: str
    content: str
    created_at: datetime
    tool_calls: list[ToolCallItem] | None = None
    l3_steps: list[L3StepItem] | None = None


class MessageListResponse(BaseModel):
    session_id: str
    title: str | None
    session_status: str                    # active / running / archived
    source: str = "chat"                   # chat / async_task / cron
    messages: list[MessageItem]
    has_more: bool


class SessionUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="会话标题")


class SessionUpdateResponse(BaseModel):
    session_id: str
    title: str


# ── 内部工具函数 ──────────────────────────────────────────────────────────

def _truncate_tool_result(result: str | None) -> str | None:
    """截断超长的 tool_call result"""
    if result is None:
        return None
    if len(result) > TOOL_CALL_RESULT_MAX_LEN:
        return result[:TOOL_CALL_RESULT_MAX_LEN] + "...（已截断）"
    return result


def _build_tool_calls(raw: list | None) -> list[ToolCallItem] | None:
    """将 JSONB 中的 tool_calls 列表转换为响应模型，result 超长时截断"""
    if not raw:
        return None
    items = []
    for tc in raw:
        items.append(ToolCallItem(
            tool_name=tc.get("tool_name", ""),
            arguments=tc.get("arguments", {}),
            result=_truncate_tool_result(tc.get("result")),
            status=tc.get("status", "success"),
            duration_ms=tc.get("duration_ms"),
        ))
    return items or None


# ── 端点 ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_sessions(
    status: str = Query("active", description="过滤状态：active / archived / all"),
    page: int = Query(1, ge=1, description="页码（从 1 开始）"),
    page_size: int = Query(20, ge=1, le=50, description="每页数量（上限 50）"),
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """获取当前用户的历史会话列表"""
    # 基础条件：只看自己的会话
    base_where = [ChatSession.user_id == user.id]
    if status == "active":
        # active 筛选同时包含 running（执行中的会话也应在列表中展示）
        base_where.append(ChatSession.status.in_(["active", "running"]))
    elif status != "all":
        base_where.append(ChatSession.status == status)

    # 查询列表
    query = (
        select(ChatSession)
        .where(*base_where)
        .order_by(ChatSession.last_active_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.scalars().all()

    # 查询总数
    count_query = select(func.count()).select_from(
        select(ChatSession.id).where(*base_where).subquery()
    )
    total = (await db.execute(count_query)).scalar() or 0

    items = [
        SessionItem(
            session_id=s.session_id,
            title=s.title,
            turn_count=s.turn_count,
            status=s.status,
            source=s.source,
            created_at=s.created_at,
            last_active_at=s.last_active_at,
            project_id=str(s.project_id) if s.project_id else None,
        )
        for s in rows
    ]

    return ok(data=SessionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    ))


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    before: str | None = Query(None, description="游标：返回此 message_id 之前的消息"),
    limit: int = Query(50, ge=1, le=100, description="返回数量（上限 100）"),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """获取指定会话的消息内容（cursor 分页，跳过 compaction 节点）"""
    # 校验会话存在且属于当前用户（越权返回 404，不暴露存在性）
    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.session_id == session_id,
            ChatSession.user_id == user.id,
        )
    )
    session_row = session_result.scalar_one_or_none()
    if not session_row:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 构建消息查询
    conditions = [
        ChatMessage.session_id == session_id,
        ChatMessage.is_compaction == False,  # noqa: E712
    ]

    # cursor 分页：查 before message_id 对应的 created_at
    if before:
        cursor_query = select(ChatMessage.created_at).where(
            ChatMessage.message_id == before,
        )
        cursor_result = await db.execute(cursor_query)
        cursor_time = cursor_result.scalar_one_or_none()
        if cursor_time is None:
            raise HTTPException(status_code=400, detail="无效的 before 游标")
        conditions.append(ChatMessage.created_at < cursor_time)

    query = (
        select(ChatMessage)
        .where(*conditions)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.scalars().all()

    # 反转为正序
    rows = list(reversed(rows))

    # 批量查询 assistant 消息关联的 L3 steps
    assistant_msg_ids = [r.message_id for r in rows if r.role == "assistant"]
    steps_map: dict[str, list[L3StepItem]] = {}
    if assistant_msg_ids:
        steps_query = (
            select(L3StepModel)
            .where(
                L3StepModel.message_id.in_(assistant_msg_ids),
                L3StepModel.compacted == False,  # noqa: E712
            )
            .order_by(L3StepModel.message_id, L3StepModel.step_index)
        )
        steps_result = await db.execute(steps_query)
        for s in steps_result.scalars().all():
            content = s.content or ""
            if len(content) > L3_STEP_CONTENT_MAX_LEN:
                content = content[:L3_STEP_CONTENT_MAX_LEN] + "...（已截断）"
            steps_map.setdefault(s.message_id, []).append(L3StepItem(
                step_index=s.step_index,
                role=s.role,
                content=content,
                tool_name=s.tool_name,
                tool_call_id=s.tool_call_id,
                tool_args=s.tool_args,
            ))

    messages = [
        MessageItem(
            message_id=r.message_id,
            role=r.role,
            content=r.content,
            created_at=r.created_at,
            tool_calls=_build_tool_calls(r.tool_calls),
            l3_steps=steps_map.get(r.message_id) if r.role == "assistant" else None,
        )
        for r in rows
    ]

    # running 状态且首页请求时，从 Redis 读取实时步骤，
    # 构造临时 assistant MessageItem 挂载 l3_steps，保持与 active 状态格式一致
    if session_row.status == "running" and before is None:
        try:
            raw = await redis.lrange(RedisKeys.live_steps(session_id), 0, -1)
            if raw:
                live_step_items = []
                for s in raw:
                    item = json.loads(s)
                    # 与 PG 路径对齐：截断超长 content
                    content = item.get("content") or ""
                    if len(content) > L3_STEP_CONTENT_MAX_LEN:
                        item["content"] = content[:L3_STEP_CONTENT_MAX_LEN] + "...（已截断）"
                    live_step_items.append(L3StepItem(**item))
                # 构造临时 assistant 消息，格式与完成后的 assistant 消息一致
                messages.append(MessageItem(
                    message_id=f"live-{session_id}",
                    role="assistant",
                    content="",
                    created_at=datetime.now(timezone.utc),
                    tool_calls=None,
                    l3_steps=live_step_items,
                ))
        except Exception as e:
            log.warning("读取 live_steps 失败", session_id=session_id, error=str(e))

    return ok(data=MessageListResponse(
        session_id=session_id,
        title=session_row.title,
        session_status=session_row.status,
        source=session_row.source,
        messages=messages,
        has_more=len(rows) == limit,
    ))


@router.delete("/{session_id}")
async def archive_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """归档会话（软删除，status 改为 archived）"""
    # 校验会话存在且属于当前用户
    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.session_id == session_id,
            ChatSession.user_id == user.id,
        )
    )
    session_row = session_result.scalar_one_or_none()
    if not session_row:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 更新状态并解除项目关联（如果存在）
    await db.execute(
        update(ChatSession)
        .where(ChatSession.session_id == session_id)
        .values(status="archived", project_id=None)
    )
    await db.commit()

    # 清理 Redis 中该会话的缓存（WorkingMemory + Todo + live_steps）
    await redis.delete(
        RedisKeys.working_memory(session_id),
        RedisKeys.todo(session_id),
        RedisKeys.live_steps(session_id),
    )

    log.info("归档会话", session_id=session_id, usernumb=user.usernumb)

    return ok(message="ok")


@router.patch("/{session_id}")
async def update_session_title(
    session_id: str,
    body: SessionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """修改会话标题"""
    # 校验会话存在且属于当前用户
    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.session_id == session_id,
            ChatSession.user_id == user.id,
        )
    )
    session_row = session_result.scalar_one_or_none()
    if not session_row:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 更新标题
    await db.execute(
        update(ChatSession)
        .where(ChatSession.session_id == session_id)
        .values(title=body.title)
    )
    await db.commit()

    return ok(data=SessionUpdateResponse(
        session_id=session_id,
        title=body.title,
    ))


