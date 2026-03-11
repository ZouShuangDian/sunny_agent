"""
CronManageTool — Agent 在对话中管理定时任务（查询 / 修改 / 删除）

统一入口，通过 action 参数区分操作：
- list：查询当前用户的定时任务列表
- update：修改定时任务（名称、频率、内容、启用/禁用）
- delete：删除定时任务

创建操作由独立的 cron_create 工具负责（参数差异较大，合并会增加复杂度）。
"""

from pydantic import BaseModel, Field

from app.cron.service import CronService
from app.db.engine import async_session
from app.execution.user_context import get_user_id
from app.tools.base import BaseTool, ToolResult


class _Params(BaseModel):
    action: str = Field(
        description=(
            "操作类型：\n"
            "- list：查询定时任务列表\n"
            "- update：修改定时任务（需提供 job_id 和要修改的字段）\n"
            "- delete：删除定时任务（需提供 job_id）"
        ),
    )
    job_id: str | None = Field(
        None, description="定时任务 ID（update / delete 时必填，list 时不需要）",
    )
    name: str | None = Field(None, description="新的任务名称（仅 update）")
    cron_expr: str | None = Field(
        None,
        description=(
            "新的 Cron 表达式（5 字段：分 时 日 月 周），最小间隔 30 分钟（仅 update）。"
            "示例：'0 15 * * *'（每天15点）、'0 8 * * 1-5'（工作日8点）"
        ),
    )
    input_text: str | None = Field(
        None, description="新的定时触发时投喂给 Agent 的消息内容（仅 update）",
    )
    description: str | None = Field(None, description="新的任务描述（仅 update）")
    timezone: str | None = Field(None, description="新的时区（仅 update）")
    enabled: bool | None = Field(
        None, description="启用或禁用定时任务，true=启用，false=暂停（仅 update）",
    )


class CronManageTool(BaseTool):
    """查询、修改、删除定时任务"""

    @property
    def name(self) -> str:
        return "cron_manage"

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def description(self) -> str:
        return (
            "管理定时任务（查询/修改/删除）。\n"
            "action=list：查询当前用户所有定时任务，返回 ID、名称、频率、状态等。\n"
            "action=update：修改定时任务，需先知道 job_id（可通过 list 获取）。\n"
            "action=delete：删除定时任务，需提供 job_id。\n"
            "暂停任务用 update + enabled=false，恢复用 enabled=true。"
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return _Params

    @property
    def risk_level(self) -> str:
        return "write"

    async def execute(self, args: dict) -> ToolResult:
        usernumb = get_user_id()
        if not usernumb:
            return ToolResult.fail("无法获取当前用户信息，请重新登录")

        action = args.get("action", "").strip().lower()

        if action == "list":
            return await self._list(usernumb)
        elif action == "update":
            return await self._update(usernumb, args)
        elif action == "delete":
            return await self._delete(usernumb, args)
        else:
            return ToolResult.fail(f"不支持的操作：{action}，可选值：list / update / delete")

    async def _list(self, usernumb: str) -> ToolResult:
        async with async_session() as db:
            service = CronService(db)
            jobs, total = await service.list_by_user(usernumb, offset=0, limit=50)

        items = [
            {
                "job_id": str(j.id),
                "name": j.name,
                "cron_expr": j.cron_expr,
                "timezone": j.timezone,
                "enabled": j.enabled,
                "last_status": j.last_status,
                "next_run_at": j.next_run_at.isoformat() if j.next_run_at else None,
                "input_text": j.input_text[:100],
            }
            for j in jobs
        ]

        return ToolResult.success(
            message=f"共 {len(items)} 个定时任务",
            items=items,
            total=total,
        )

    async def _update(self, usernumb: str, args: dict) -> ToolResult:
        job_id = args.get("job_id")
        if not job_id:
            return ToolResult.fail("修改定时任务需要提供 job_id，请先用 action=list 查询")

        # 提取要修改的字段（排除 action 和 job_id）
        fields = {
            k: v for k, v in args.items()
            if k not in ("action", "job_id") and v is not None
        }

        if not fields:
            return ToolResult.fail("请提供至少一个要修改的字段")

        async with async_session() as db:
            service = CronService(db)
            try:
                job = await service.update(job_id, usernumb, **fields)
            except ValueError as e:
                return ToolResult.fail(str(e))

        if not job:
            return ToolResult.fail("定时任务不存在或无权修改")

        return ToolResult.success(
            message="定时任务修改成功",
            job_id=str(job.id),
            name=job.name,
            cron_expr=job.cron_expr,
            timezone=job.timezone,
            input_text=job.input_text,
            enabled=job.enabled,
            next_run_at=job.next_run_at.isoformat() if job.next_run_at else None,
        )

    async def _delete(self, usernumb: str, args: dict) -> ToolResult:
        job_id = args.get("job_id")
        if not job_id:
            return ToolResult.fail("删除定时任务需要提供 job_id，请先用 action=list 查询")

        async with async_session() as db:
            service = CronService(db)
            deleted = await service.delete(job_id, usernumb)

        if not deleted:
            return ToolResult.fail("定时任务不存在或无权删除")

        return ToolResult.success(message="定时任务已删除", job_id=job_id)
