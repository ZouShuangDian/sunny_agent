"""
ReadFileTool — 读取沙箱容器内的文件内容

专为读取 /mnt/skills/ 下的 Skill 定义文件设计，同时支持读取用户上传文件。
通过 bash_tool 的 head 命令实现，限制返回行数防止 context 爆炸。

与 bash_tool 的区别：
- bash_tool 是通用命令执行，read_file 语义明确（读文件），LLM 选择更直观
- read_file 做路径安全校验，只允许读 /mnt/ 和 /tmp/ 下的文件
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

# 允许读取的路径前缀白名单
_ALLOWED_READ_PREFIXES = ("/mnt/", "/tmp/")


class ReadFileParams(BaseModel):
    path: str = Field(
        description="要读取的文件绝对路径，如 /mnt/skills/web_research/skill.md"
    )
    max_lines: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="最大返回行数，默认 500，最大 5000",
    )


class ReadFileTool(BaseTool):
    """读取容器内文件内容，支持 /mnt/skills/ 和用户目录下的文件"""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "读取沙箱容器内的文件内容。\n\n"
            "容器内目录结构：\n"
            "  /mnt/skills/{skill_name}/          — 系统 Skill 目录（只读）\n"
            "    skill.md                           → Skill 指令文件\n"
            "    scripts/{script}.py               → Skill 脚本\n\n"
            "  /mnt/users/{user_id}/              — 用户个人目录（支持读取其下所有文件）\n"
            "    uploads/                          → 用户上传文件\n"
            "    outputs/{session_id}/            → 任务产出物\n"
            "    plugins/{plugin_name}/\n"
            "      commands/{command}.md           → Plugin 命令文件\n"
            "      skills/{skill_name}/SKILL.md   → Plugin 内置 Skill 文件\n"
            "      scripts/                        → Plugin 脚本\n\n"
            "  /tmp/                              — 临时工作区（读写）\n\n"
            "返回文件内容文本，超出 max_lines 行时截断。"
        )

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def risk_level(self) -> str:
        return "read"

    @property
    def timeout_ms(self) -> int:
        return 15_000

    @property
    def params_model(self) -> type[BaseModel]:
        return ReadFileParams

    async def execute(self, args: dict) -> ToolResult:
        params = ReadFileParams(**args)
        session_id = get_session_id()
        user_id = get_user_id()

        if not session_id:
            return ToolResult.fail("read_file 需要有效的 session_id，当前上下文未设置")

        # 路径安全校验
        if not any(params.path.startswith(prefix) for prefix in _ALLOWED_READ_PREFIXES):
            return ToolResult.fail(
                f"read_file 只允许读取 /mnt/ 或 /tmp/ 下的文件，拒绝路径：{params.path}"
            )

        # 防止路径穿越
        import os
        normalized = os.path.normpath(params.path)
        if not any(normalized.startswith(prefix.rstrip("/")) for prefix in _ALLOWED_READ_PREFIXES):
            return ToolResult.fail(f"路径包含非法穿越符，拒绝：{params.path}")

        command = f"head -n {params.max_lines} {_quote(params.path)} 2>&1 && echo '---EOF---'"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{settings.SANDBOX_SERVICE_URL}/exec",
                    json={
                        "session_id": session_id,
                        "user_id": user_id,
                        "command": command,
                        "timeout": 10,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

            if data["returncode"] != 0:
                return ToolResult.fail(f"读取文件失败：{data['stdout'] or data['stderr']}")

            content = data["stdout"].removesuffix("\n---EOF---\n").removesuffix("---EOF---")
            return ToolResult.success(path=params.path, content=content)

        except httpx.TimeoutException:
            return ToolResult.fail("read_file 执行超时")
        except Exception as e:
            log.error("read_file 异常", error=str(e))
            return ToolResult.fail(f"read_file 失败：{e}")


def _quote(path: str) -> str:
    """对路径做单引号转义，避免 shell 注入"""
    return "'" + path.replace("'", "'\\''") + "'"
