"""
执行层数据结构定义

ExecutionResult 是执行路由器的统一输出格式，
所有路由（standard_l1 / deep_l3）最终都返回此结构。

Week 7 扩展：新增 L3 特有字段（L1 返回时为默认值，不影响现有逻辑）。
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

    # ── L3 扩展字段（L1 返回时为默认值） ──
    reasoning_trace: list[dict] | None = None  # 推理链（仅 L3）
    iterations: int = 0                         # 循环步数
    token_usage: dict | None = None             # token 消耗明细
    is_degraded: bool = False                   # 是否触发了降级
    degrade_reason: str | None = None           # 降级原因
