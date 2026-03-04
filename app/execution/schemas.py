"""
执行层数据结构定义

ExecutionResult 是执行路由器的统一输出格式，
统一通过 L3 ReAct 引擎返回此结构。
"""

from pydantic import BaseModel, Field

from app.memory.schemas import ToolCall


class ExecutionResult(BaseModel):
    """执行层统一输出"""

    reply: str = ""  # 最终回复文本
    tool_calls: list[ToolCall] = Field(default_factory=list)  # 工具调用记录
    data: dict | None = None  # 结构化数据（如查询结果）
    source: str = ""  # 来源标识（统一为 "deep_l3"）
    duration_ms: int = 0  # 执行耗时（毫秒）

    # ── L3 扩展字段 ──
    reasoning_trace: list[dict] | None = None  # 推理链
    iterations: int = 0                         # 循环步数
    token_usage: dict | None = None             # token 消耗明细
    is_degraded: bool = False                   # 是否触发了降级
    degrade_reason: str | None = None           # 降级原因
    l3_steps: list[dict] | None = None          # 中间步骤原始消息（用于持久化到 l3_steps 表）
    context_usage: dict | None = None           # 上下文用量（最后一步 Think 的 prompt_tokens/remaining/percent/limit）
    compaction_summary: str | None = None       # Level 2 摘要内容（供 chat.py 持久化为 genesis block）
