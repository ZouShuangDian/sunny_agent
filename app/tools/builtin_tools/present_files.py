"""
PresentFilesTool — 将 outputs 目录的文件暴露为下载链接

职责：
1. 校验文件路径在当前 session 的 outputs 目录内
2. 将容器内路径转换为 API 下载 URL
3. 返回文件名 + 下载链接列表，LLM 在回复中告知用户

下载实际由 /api/files/download 端点处理，端点负责流式返回文件内容。
"""

import os

import structlog
from pydantic import BaseModel, Field

from app.execution.session_context import get_session_id
from app.execution.user_context import get_user_id
from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class PresentFilesParams(BaseModel):
    paths: list[str] = Field(
        description=(
            "要呈现给用户的文件路径列表，路径必须在 "
            "/mnt/users/{user_id}/outputs/{session_id}/ 目录下"
        )
    )


class PresentFilesTool(BaseTool):
    """将 outputs 目录中的文件转换为下载链接，供 LLM 在回复中告知用户"""

    @property
    def name(self) -> str:
        return "present_files"

    @property
    def description(self) -> str:
        return (
            "将 outputs 目录中的文件暴露为下载链接。\n"
            "用法：先用 write_file 写入文件，再调用 present_files 获取下载 URL。\n"
            "只能呈现当前 session 的 outputs 目录下的文件。\n"
            "返回文件名和下载链接列表，在最终回复中告知用户点击下载。"
        )

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def risk_level(self) -> str:
        return "read"

    @property
    def timeout_ms(self) -> int:
        return 5_000

    @property
    def params_model(self) -> type[BaseModel]:
        return PresentFilesParams

    async def execute(self, args: dict) -> ToolResult:
        params = PresentFilesParams(**args)
        session_id = get_session_id()
        user_id = get_user_id()

        if not session_id:
            return ToolResult.fail("present_files 需要有效的 session_id，当前上下文未设置")

        allowed_prefix = f"/mnt/users/{user_id}/outputs/{session_id}/"
        files = []

        for path in params.paths:
            normalized = os.path.normpath(path)
            if not normalized.startswith(allowed_prefix.rstrip("/")):
                return ToolResult.fail(
                    f"present_files 只允许呈现 {allowed_prefix} 下的文件，拒绝路径：{path}"
                )

            filename = os.path.basename(normalized)
            # 将容器内路径转换为宿主机相对路径（去掉 /mnt/ 前缀）
            # 实际下载由 /api/files/download?path=users/{uid}/outputs/{sid}/filename 处理
            relative = normalized.removeprefix("/mnt/")
            download_url = f"/api/files/download?path={relative}"

            files.append({
                "name": filename,
                "path": path,
                "download_url": download_url,
            })

        log.info("present_files 生成下载链接", count=len(files), session_id=session_id[:8])

        return ToolResult.success(
            message=f"已生成 {len(files)} 个文件的下载链接，请在回复中告知用户点击下载",
            files=files,
        )
