"""
M04-4 降级处理器：校验失败时生成安全的降级结果

降级结果特征：
- route = standard_l1（最安全的路由，L1 标准执行）
- complexity = simple
- intent = general_qa
- confidence = 0.0（标记为不可靠）
"""

from app.guardrails.schemas import (
    IntentDetail,
    IntentResult,
)


class FallbackHandler:
    """校验失败时的降级处理"""

    def fallback(
        self, raw_input: str, session_id: str, trace_id: str
    ) -> IntentResult:
        """生成降级结果"""
        return IntentResult(
            route="standard_l1",
            complexity="simple",
            confidence=0.0,
            intent=IntentDetail(
                primary="general_qa",
                sub_intent=None,
                user_goal="无法识别用户意图",
            ),
            entity_hints={},
            needs_clarify=False,
            clarify_question=None,
            raw_input=raw_input,
            session_id=session_id,
            trace_id=trace_id,
        )
