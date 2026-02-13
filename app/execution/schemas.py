"""
执行层数据结构定义

ExecutionResult 是执行路由器的统一输出格式，
所有路由（standard_l1 / deep_l3）最终都返回此结构。
"""

from pydantic import BaseModel, Field

from app.memory.schemas import ToolCall


class ExecutionResult(BaseModel):
    """执行层统一输出"""

    reply: str = ""  # 最终回复文本
    tool_calls: list[ToolCall] = Field(default_factory=list)  # 工具调用记录
    data: dict | None = None  # 结构化数据（如查询结果）
    source: str = ""  # 来源标识（如 "standard_l1", "deep_l3"）
    duration_ms: int = 0  # 执行耗时（毫秒）
