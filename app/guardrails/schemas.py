"""
Guardrails Schema：意图理解层最终输出的 Data Contract

IntentResult 是下游执行层（L3 ReAct）的标准数据契约。
所有字段都经过 Pydantic V2 校验，确保下游收到合法、完整的结构化数据。
"""

from typing import Literal

from pydantic import BaseModel, Field


class IntentDetail(BaseModel):
    """意图详情"""

    primary: str = Field(default="general", description="主意图，如 writing")
    sub_intent: str | None = Field(None, description="子意图")
    user_goal: str = Field(default="", description="用户目标自然语言描述")


class IntentResult(BaseModel):
    """意图理解层最终输出（Data Contract）"""

    route: Literal["deep_l3"] = "deep_l3"
    intent: IntentDetail
    raw_input: str
    session_id: str
    trace_id: str
    history_messages: list[dict] = Field(
        default_factory=list,
        description="过滤后的对话历史（仅 user/assistant），供执行层使用",
    )
