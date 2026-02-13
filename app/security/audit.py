"""
审计日志写入：异步写入 PG，不阻塞主请求
审计日志直接保存用户原始输入
"""

import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import async_session
from app.db.models.audit import AuditLog

log = structlog.get_logger()


class AuditLogger:
    """异步审计日志写入器"""

    async def log(
        self,
        *,
        trace_id: str,
        user_id: str | None = None,
        usernumb: str | None = None,
        action: str,
        risk_level: str = "read",
        route: str | None = None,
        input_text: str | None = None,
        status: str = "success",
        metadata: dict | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """异步写入审计日志到数据库"""
        try:
            async with async_session() as session:
                entry = AuditLog(
                    trace_id=trace_id,
                    user_id=user_id,
                    usernumb=usernumb,
                    action=action,
                    risk_level=risk_level,
                    route=route,
                    input_text=input_text,
                    status=status,
                    metadata_=metadata or {},
                    duration_ms=duration_ms,
                )
                session.add(entry)
                await session.commit()
        except Exception as e:
            # 审计日志写入失败不应影响主流程，只记录错误
            log.error("审计日志写入失败", error=str(e), trace_id=trace_id, exc_info=True)

    def log_background(self, **kwargs) -> None:
        """发后即忘：在后台异步写入审计日志"""
        asyncio.create_task(self.log(**kwargs))


# 单例
audit_logger = AuditLogger()
