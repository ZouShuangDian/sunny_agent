"""
CreateTaskTool — Agent 创建异步后台任务

设计要点：
- 这是 Agent 的 tool call，不是前端直接调用的 API
- 任务类型白名单预定义（Phase 1 仅 deep_research），不由 Agent 自主判断
- Agent 的职责是：澄清用户意图 → 调用此工具创建任务
- 工具负责：写 DB + enqueue arq job + 返回 task_id
"""

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from uuid6 import uuid7

from app.db.engine import async_session
from app.db.models.async_task import AsyncTask
from app.db.models.user import User
from app.execution.session_context import get_session_id
from app.execution.user_context import get_user_id
from app.tasks.arq_pool import get_arq_pool
from app.tools.base import BaseTool, ToolResult

# 预定义的异步任务类型白名单（Phase 1 仅支持深度研究，后续按需扩展）
ASYNC_TASK_TYPES = Literal["deep_research"]

# 每用户最大活跃任务数（pending + running）
MAX_ACTIVE_TASKS_PER_USER = 10


class _Params(BaseModel):
    task_type: ASYNC_TASK_TYPES = Field(
        default="deep_research",
        description="任务类型：deep_research（深度研究）",
    )
    task_description: str = Field(
        description="经过澄清后的完整任务描述（Agent 加工过，非用户原始输入）",
    )


class CreateTaskTool(BaseTool):
    """创建异步后台任务（Agent 驱动，非前端直接调用）"""

    @property
    def name(self) -> str:
        return "create_task"

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def description(self) -> str:
        return (
            "将深度研究等长时任务提交到后台执行。\n"
            "适用的任务类型（系统预定义，Phase 1 仅支持）：\n"
            "- deep_research：深度研究分析\n\n"
            "调用前必须：与用户充分沟通，确认任务内容和参数。\n"
            "调用后：告知用户任务已创建，完成后会通知。"
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return _Params

    @property
    def risk_level(self) -> str:
        return "write"

    @property
    def mode_only(self) -> bool:
        return True

    async def execute(self, args: dict) -> ToolResult:
        params = _Params(**args)

        session_id = get_session_id()
        usernumb = get_user_id()
        if not session_id or not usernumb:
            return ToolResult.fail("无法获取会话上下文")

        task_id = uuid7()

        async with async_session() as db:
            # 查用户 UUID
            result = await db.execute(
                select(User.id).where(User.usernumb == usernumb)
            )
            user_uuid = result.scalar_one_or_none()
            if not user_uuid:
                return ToolResult.fail(f"用户 {usernumb} 不存在")

            # 检查活跃任务数量限制
            active_count = await db.scalar(
                select(func.count())
                .select_from(AsyncTask)
                .where(
                    AsyncTask.usernumb == usernumb,
                    AsyncTask.status.in_(["pending", "running"]),
                )
            )
            if active_count >= MAX_ACTIVE_TASKS_PER_USER:
                return ToolResult.fail(
                    f"当前有 {active_count} 个任务正在执行或等待中，"
                    f"最多允许 {MAX_ACTIVE_TASKS_PER_USER} 个，请等待完成后再创建"
                )

            # 写 DB（flush 不 commit，等 enqueue 成功再 commit）
            db.add(AsyncTask(
                id=task_id,
                usernumb=usernumb,
                user_id=str(user_uuid),
                session_id=session_id,
                task_type=params.task_type,
                status="pending",
                input_text=params.task_description,
            ))
            await db.flush()

            # 通过 arq_pool 入队
            try:
                pool = await get_arq_pool()
                await pool.enqueue_job(
                    "execute_async_task",
                    _job_id=str(task_id),
                    task_id=str(task_id),
                    session_id=session_id,
                    usernumb=usernumb,
                    user_id=str(user_uuid),
                    input_text=params.task_description,
                    task_type=params.task_type,
                )
            except Exception as e:
                await db.rollback()
                return ToolResult.fail(f"任务入队失败：{e}")

            # enqueue 成功 → commit DB
            await db.commit()

        return ToolResult.success(
            task_id=str(task_id),
            task_type=params.task_type,
            task_description=params.task_description[:100],
            message=f"后台任务已创建（ID: {str(task_id)[:8]}...），预计需要几分钟完成，届时会通知您。",
        )
