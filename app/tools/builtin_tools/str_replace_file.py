"""
StrReplaceFileTool — 查找并替换 outputs 目录文件中的指定内容

适合对已生成文件做局部修改（修正错误、补充内容）而无需重写整个文件。
写入路径同样严格限定在 /mnt/users/{user_id}/outputs/{session_id}/ 下。

实现方式：base64 编码 old/new 字符串后交给容器内 python3 做替换，
避免 shell heredoc 对特殊字符的转义问题。
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


class StrReplaceFileParams(BaseModel):
    path: str = Field(
        description=(
            "目标文件的绝对路径，必须在 /mnt/users/{user_id}/outputs/{session_id}/ 目录下。"
            "例如：/mnt/users/1131618/outputs/abc123/report.md"
        )
    )
    old_string: str = Field(
        description="要被替换的原始内容片段（必须与文件中的内容完全匹配，包括空格和换行）"
    )
    new_string: str = Field(
        description="替换后的新内容（可以为空字符串以实现删除效果）"
    )


class StrReplaceFileTool(BaseTool):
    """查找替换 outputs 目录中已有文件的内容片段"""

    @property
    def name(self) -> str:
        return "str_replace_file"

    @property
    def description(self) -> str:
        return (
            "查找并替换 outputs 目录中已有文件的指定内容片段（首次出现）。\n"
            "适合局部修改已生成的文件，无需重写整个文件。\n"
            "old_string 必须与文件中的内容完全匹配（包括空白和换行）。\n"
            "若 old_string 在文件中不存在，操作失败并返回错误。"
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
        return StrReplaceFileParams

    async def execute(self, args: dict) -> ToolResult:
        params = StrReplaceFileParams(**args)
        session_id = get_session_id()
        user_id = get_user_id()

        if not session_id:
            return ToolResult.fail("str_replace_file 需要有效的 session_id，当前上下文未设置")

        # 写入路径必须在当前 session 的 outputs 目录下
        allowed_prefix = f"/mnt/users/{user_id}/outputs/{session_id}/"
        normalized = os.path.normpath(params.path)
        if not normalized.startswith(allowed_prefix.rstrip("/")):
            return ToolResult.fail(
                f"str_replace_file 只允许操作 {allowed_prefix} 目录，拒绝路径：{params.path}"
            )

        # base64 编码 old/new/path，避免 shell 引号和换行问题
        b64_old = base64.b64encode(params.old_string.encode("utf-8")).decode("ascii")
        b64_new = base64.b64encode(params.new_string.encode("utf-8")).decode("ascii")
        b64_path = base64.b64encode(params.path.encode("utf-8")).decode("ascii")

        # 把整个 Python 脚本 base64 化后 pipe 给 python3，彻底规避 shell 引号问题
        python_script = "\n".join([
            "import base64, sys",
            f"path = base64.b64decode('{b64_path}').decode('utf-8')",
            f"old  = base64.b64decode('{b64_old}').decode('utf-8')",
            f"new  = base64.b64decode('{b64_new}').decode('utf-8')",
            "content = open(path, encoding='utf-8').read()",
            "if old not in content:",
            "    print('NOT_FOUND'); sys.exit(1)",
            "open(path, 'w', encoding='utf-8').write(content.replace(old, new, 1))",
            "print('replace_ok')",
        ])
        b64_script = base64.b64encode(python_script.encode("utf-8")).decode("ascii")
        command = f"echo {b64_script} | base64 -d | python3"

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

            stdout = data.get("stdout", "")
            stderr = data.get("stderr", "")

            if "NOT_FOUND" in stdout:
                return ToolResult.fail(
                    f"str_replace_file 失败：old_string 在文件中不存在，请确认内容完全匹配。\n"
                    f"文件：{params.path}"
                )

            if data["returncode"] != 0 or "replace_ok" not in stdout:
                return ToolResult.fail(
                    f"str_replace_file 失败：{stderr or stdout}"
                )

            log.info("str_replace_file 成功", path=params.path)
            return ToolResult.success(
                path=params.path,
                message=f"内容已替换，文件：{params.path}",
            )

        except httpx.TimeoutException:
            return ToolResult.fail("str_replace_file 执行超时")
        except Exception as e:
            log.error("str_replace_file 异常", error=str(e))
            return ToolResult.fail(f"str_replace_file 失败：{e}")
