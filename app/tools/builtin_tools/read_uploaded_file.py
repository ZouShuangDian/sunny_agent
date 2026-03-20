from __future__ import annotations

from pathlib import Path
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.engine import async_session
from app.db.models.file import File
from app.db.models.user import User
from app.execution.user_context import get_user_id
from app.services.file_service import FileService
from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".log",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".sql",
    ".yaml", ".yml", ".xml", ".html", ".htm", ".css", ".scss", ".sh",
}


class ReadUploadedFileParams(BaseModel):
    file_id: str = Field(description="Uploaded file UUID.")
    max_chars: int = Field(
        default=12000,
        ge=200,
        le=50000,
        description="Maximum number of characters to return.",
    )


class ReadUploadedFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_uploaded_file"

    @property
    def description(self) -> str:
        return (
            "Read the content of a user-uploaded file by file_id. "
            "Use this only when the current task requires file content. "
            "Works best for text-like files. Binary files return metadata only."
        )

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def risk_level(self) -> str:
        return "read"

    @property
    def timeout_ms(self) -> int:
        return 10_000

    @property
    def params_model(self) -> type[BaseModel]:
        return ReadUploadedFileParams

    async def execute(self, args: dict) -> ToolResult:
        params = ReadUploadedFileParams(**args)
        usernumb = get_user_id()
        if not usernumb:
            return ToolResult.fail("read_uploaded_file requires a valid user context")

        try:
            file_uuid = UUID(params.file_id)
        except ValueError:
            return ToolResult.fail(f"Invalid file_id: {params.file_id}")

        async with async_session() as db:
            user_id_result = await db.execute(
                select(User.id).where(User.usernumb == usernumb)
            )
            user_id = user_id_result.scalar_one_or_none()
            if user_id is None:
                return ToolResult.fail(f"User not found for usernumb={usernumb}")

            file_result = await db.execute(select(File).where(File.id == file_uuid))
            file_record = file_result.scalar_one_or_none()
            if file_record is None:
                return ToolResult.fail(f"File not found: {params.file_id}")

            if file_record.uploaded_by != user_id:
                return ToolResult.fail("Access denied for this file")

            absolute_path = FileService(db).get_file_absolute_path(file_record)
            if not absolute_path.exists() or not absolute_path.is_file():
                return ToolResult.fail(f"File is missing on disk: {absolute_path}")

            suffix = (file_record.file_extension or "").lower()
            mime_type = file_record.mime_type or ""
            if suffix in _TEXT_EXTENSIONS or mime_type.startswith("text/"):
                content = absolute_path.read_text(encoding="utf-8", errors="ignore")
                if len(content) > params.max_chars:
                    content = content[: params.max_chars] + "\n\n[Truncated]"
            else:
                content = "[Binary or unsupported file type. Content was not expanded.]"

            log.info("read_uploaded_file executed", file_id=params.file_id)
            return ToolResult.success(
                file_id=str(file_record.id),
                file_name=file_record.file_name,
                mime_type=file_record.mime_type,
                file_path=str(absolute_path),
                content=content,
            )
