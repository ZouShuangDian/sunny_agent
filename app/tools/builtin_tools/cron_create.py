"""
CronCreateTool — Agent 在对话中创建定时任务

当用户说"帮我每天9点查产量"等含定时需求的消息时，
LLM 调用此工具创建 cron_jobs 记录，返回创建结果供用户确认。
"""

from pydantic import BaseModel, Field

from app.cron.service import CronJobLimitExceeded, CronService
from app.db.engine import async_session
from app.execution.user_context import get_user_id
from app.tools.base import BaseTool, ToolResult


class _Params(BaseModel):
    name: str = Field(description="任务名称，简洁描述任务内容")
    cron_expr: str = Field(
        description=(
            "标准 5 字段 Cron 表达式（分 时 日 月 周），最小间隔 30 分钟。"
            "示例：'0 9 * * *'（每天9点）、'0 8,17 * * 1-5'（工作日8点和17点）、"
            "'0 */2 * * *'（每2小时）"
        ),
    )
    input_text: str = Field(
        description="定时触发时投喂给 Agent 的消息内容，描述要执行的任务",
    )
    description: str | None = Field(
        None, description="任务描述（可选）",
    )
    timezone: str = Field(
        "Asia/Shanghai", description="时区，默认 Asia/Shanghai",
    )


class CronCreateTool(BaseTool):
    """在对话中创建定时任务"""

    @property
    def name(self) -> str:
        return "cron_create"

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def description(self) -> str:
        return (
            "创建定时任务。当用户表达定时、定期、每天、每周等周期性需求时调用。\n"
            "任务创建后，系统会按 cron 表达式定时触发 Agent 执行 input_text 中的任务。\n"
            "最小触发间隔为 30 分钟，每个用户最多创建 20 个定时任务。\n"
            "创建成功后返回任务详情（含下次触发时间），请展示给用户确认。"
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

        async with async_session() as db:
            service = CronService(db)
            try:
                job = await service.create(
                    usernumb=usernumb,
                    name=args["name"],
                    cron_expr=args["cron_expr"],
                    input_text=args["input_text"],
                    description=args.get("description"),
                    timezone_str=args.get("timezone", "Asia/Shanghai"),
                )
            except CronJobLimitExceeded as e:
                return ToolResult.fail(str(e))
            except ValueError as e:
                return ToolResult.fail(str(e))

        return ToolResult.success(
            message="定时任务创建成功",
            job_id=str(job.id),
            name=job.name,
            cron_expr=job.cron_expr,
            timezone=job.timezone,
            input_text=job.input_text,
            next_run_at=job.next_run_at.isoformat() if job.next_run_at else None,
            enabled=job.enabled,
        )
