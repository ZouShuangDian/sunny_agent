"""
M04-1 Guardrails Schema：意图理解层最终输出的 Data Contract

IntentResult 是 M03 意图链路 → M04 护栏层 → 下游执行层 之间的标准数据契约。
所有字段都经过 Pydantic V2 校验，确保下游收到合法、完整的结构化数据。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class IntentDetail(BaseModel):
    """意图详情"""

    primary: str = Field(..., description="主意图，如 writing")
    sub_intent: str | None = Field(None, description="子意图")
    user_goal: str = Field(..., description="用户目标自然语言描述")


class IntentResult(BaseModel):
    """意图理解层最终输出（Data Contract）"""

    route: Literal["standard_l1", "deep_l3"]
    complexity: Literal["simple", "moderate", "complex"]
    confidence: float = Field(ge=0.0, le=1.0)
    intent: IntentDetail
    entity_hints: dict[str, Any] = Field(
        default_factory=dict,
        description="LLM 提取的实体线索（弱类型，值可为 str/list/None），供 Sub-Agent 参考",
    )
    needs_clarify: bool = False
    clarify_question: str | None = None
    raw_input: str
    session_id: str
    trace_id: str
    history_messages: list[dict] = Field(
        default_factory=list,
        description="过滤后的对话历史（仅 user/assistant），供执行层使用",
    )
