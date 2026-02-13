"""
M03-5 追问处理器：当意图不明确时，生成追问话术

仅处理意图级别的模糊：
- confidence < 0.6（LLM 不确定）
- needs_clarify = True（LLM 主动标记）

领域级追问（如缺少 product/metric）由 Sub-Agent 在执行阶段负责。
"""

from dataclasses import dataclass, field

from app.intent.intent_engine import IntentEngineResult


@dataclass
class ClarifyResult:
    """追问处理结果"""

    needs_clarify: bool  # 是否需要追问
    question: str | None = None  # 追问话术
    missing_fields: list[str] = field(default_factory=list)  # 缺失的关键字段
    suggestions: list[str] = field(default_factory=list)  # 候选建议


# ── Phase 1 追问模板 ──

CLARIFY_TEMPLATES: dict[str, str] = {
    "ambiguous_intent": "您的需求我还不太确定，能否再详细描述一下？",
}

# 置信度阈值
CONFIDENCE_THRESHOLD = 0.6


class ClarifyHandler:
    """意图级模糊追问处理器"""

    def check_and_clarify(
        self,
        intent_result: IntentEngineResult,
    ) -> ClarifyResult:
        """
        检查是否需要追问，生成追问话术。

        优先级：
        1. LLM 主动标记 needs_clarify → 使用 LLM 的追问话术
        2. confidence 低 → 使用"意图模糊"模板
        """
        # 1. LLM 主动标记追问
        if intent_result.needs_clarify and intent_result.clarify_question:
            return ClarifyResult(
                needs_clarify=True,
                question=intent_result.clarify_question,
                missing_fields=[],
                suggestions=[],
            )

        # 2. 置信度过低
        if intent_result.confidence < CONFIDENCE_THRESHOLD:
            return ClarifyResult(
                needs_clarify=True,
                question=CLARIFY_TEMPLATES["ambiguous_intent"],
                missing_fields=[],
                suggestions=[],
            )

        # 无需追问
        return ClarifyResult(needs_clarify=False)
