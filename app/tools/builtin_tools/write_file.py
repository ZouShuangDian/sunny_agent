"""
WriteFileTool — 在沙箱容器的 outputs 目录写入文件

写入路径严格限定在 /mnt/users/{user_id}/outputs/{session_id}/ 下，
防止 LLM 越权写入其他用户目录或 Skill 文件。

实现方式：通过 base64 编码内容传给容器，避免 shell heredoc 特殊字符问题。
"""

import base64
import os

import httpx
import structlog
from pydantic import BaseModel, Field

from app.config import get_settings
from app.execution.session_context import get_session_id
from app.execution.user_context import get_user_id
from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()
settings = get_settings()


class WriteFileParams(BaseModel):
    path: str = Field(
        description=(
            "写入文件的绝对路径，必须在 /mnt/users/{user_id}/outputs/{session_id}/ 目录下。"
            "例如：/mnt/users/1131618/outputs/abc123/report.md"
        )
    )
    content: str = Field(description="文件内容（UTF-8 文本）")


class WriteFileTool(BaseTool):
    """在 outputs 目录写入文件，供用户后续通过 present_files 下载"""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "在 outputs 目录写入文件，供用户后续下载。\n"
            "写入路径格式：/mnt/users/{user_id}/outputs/{session_id}/filename\n"
            "写入完成后，调用 present_files 将文件暴露为下载链接。\n"
            "支持任意文本格式：.md、.csv、.json、.txt、.html 等。"
        )

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def risk_level(self) -> str:
        return "write"

    @property
    def timeout_ms(self) -> int:
        return 15_000

    @property
    def params_model(self) -> type[BaseModel]:
        return WriteFileParams

    async def execute(self, args: dict) -> ToolResult:
        params = WriteFileParams(**args)
        session_id = get_session_id()
        user_id = get_user_id()

        if not session_id:
            return ToolResult.fail("write_file 需要有效的 session_id，当前上下文未设置")

        # 写入路径必须在当前 session 的 outputs 目录下
        allowed_prefix = f"/mnt/users/{user_id}/outputs/{session_id}/"
        normalized = os.path.normpath(params.path)
        if not normalized.startswith(allowed_prefix.rstrip("/")):
            return ToolResult.fail(
                f"write_file 只允许写入 {allowed_prefix} 目录，拒绝路径：{params.path}"
            )

        # 通过 base64 传输内容，避免 shell 特殊字符问题
        b64_content = base64.b64encode(params.content.encode("utf-8")).decode("ascii")
        quoted_path = "'" + params.path.replace("'", "'\\''") + "'"
        command = (
            f"mkdir -p {_quote(os.path.dirname(params.path))} && "
            f"echo {b64_content} | base64 -d > {quoted_path} && "
            f"echo 'write_ok'"
        )

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

            if data["returncode"] != 0 or "write_ok" not in data["stdout"]:
                return ToolResult.fail(
                    f"写入文件失败：{data['stderr'] or data['stdout']}"
                )

            log.info("write_file 成功", path=params.path, size=len(params.content))
            return ToolResult.success(
                path=params.path,
                size_bytes=len(params.content.encode("utf-8")),
                message=f"文件已写入 {params.path}，调用 present_files 获取下载链接",
            )

        except httpx.TimeoutException:
            return ToolResult.fail("write_file 执行超时")
        except Exception as e:
            log.error("write_file 异常", error=str(e))
            return ToolResult.fail(f"write_file 失败：{e}")


def _quote(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"
