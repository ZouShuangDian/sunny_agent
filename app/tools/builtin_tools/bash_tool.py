"""
BashTool — 在沙箱容器内执行 bash 命令

调用链：LLM → bash_tool → sandbox_service HTTP /exec → 容器内 bash → stdout/stderr/returncode

设计要点：
- tier=L3：仅 L3 ReAct 引擎可用，不暴露给 L1 FastTrack
- session_id / user_id 从 ContextVar 自动读取，LLM 无需传入
- timeout 由 LLM 按需指定，上限受 BaseTool.timeout_ms 兜底
"""

import httpx
import structlog
from pydantic import BaseModel, Field

from app.config import get_settings
from app.execution.session_context import get_session_id
from app.execution.user_context import get_user_id
from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()
settings = get_settings()


class BashParams(BaseModel):
    command: str = Field(description="要在沙箱容器内执行的 bash 命令")
    timeout: int = Field(default=30, ge=1, le=300, description="执行超时秒数，默认 30，最大 300")


class BashTool(BaseTool):
    """在隔离沙箱容器内执行任意 bash 命令"""

    @property
    def name(self) -> str:
        return "bash_tool"

    @property
    def description(self) -> str:
        return (
            "在隔离的沙箱容器内执行 bash 命令，返回 stdout、stderr 和退出码。\n"
            "容器内预装工具：python3、pip、curl、wget、git、jq、unzip、build-essential。\n"
            "可用路径：\n"
            "  /mnt/skills/{skill_name}/   — 系统 Skill 文件（只读）\n"
            "  /mnt/users/{user_id}/uploads/  — 用户上传文件（只读）\n"
            "  /mnt/users/{user_id}/outputs/{session_id}/  — 产出物写入区（读写）\n"
            "  /tmp/  — 临时工作区（读写，容器销毁后清理）\n"
            "注意：同一 session 多次调用共享同一容器，pip install 等状态跨调用保留。"
        )

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def risk_level(self) -> str:
        return "write"

    @property
    def timeout_ms(self) -> int:
        # 留 10s 余量给 HTTP 开销，实际执行超时由 command timeout 参数控制
        return (settings.SANDBOX_BASH_TIMEOUT + 10) * 1000

    @property
    def params_model(self) -> type[BaseModel]:
        return BashParams

    async def execute(self, args: dict) -> ToolResult:
        params = BashParams(**args)
        session_id = get_session_id()
        user_id = get_user_id()

        if not session_id:
            return ToolResult.fail("bash_tool 需要有效的 session_id，当前上下文未设置")

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{settings.SANDBOX_SERVICE_URL}/exec",
                    json={
                        "session_id": session_id,
                        "user_id": user_id,
                        "command": params.command,
                        "timeout": params.timeout,
                    },
                    timeout=params.timeout + 10,
                )
                resp.raise_for_status()
                data = resp.json()

            log.debug(
                "bash_tool 执行完成",
                session_id=session_id[:8],
                returncode=data["returncode"],
                command_preview=params.command[:80],
            )

            return ToolResult.success(
                stdout=data["stdout"],
                stderr=data["stderr"],
                returncode=data["returncode"],
            )

        except httpx.TimeoutException:
            return ToolResult.fail(f"bash_tool 执行超时（>{params.timeout}s）")
        except httpx.HTTPStatusError as e:
            return ToolResult.fail(f"sandbox_service 返回错误：{e.response.status_code} {e.response.text}")
        except Exception as e:
            log.error("bash_tool 异常", error=str(e))
            return ToolResult.fail(f"bash_tool 执行失败：{e}")
