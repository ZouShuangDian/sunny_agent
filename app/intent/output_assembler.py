"""
M03-6 输出组装器：将意图链路各步骤的结果组装为最终 IntentResult

IntentResult 是 M03 意图理解层的最终产出（Data Contract），
交给 M04 Guardrails 做校验后传递到下游执行层。
"""

from app.guardrails.schemas import IntentDetail, IntentResult
from app.intent.clarify_handler import ClarifyResult
from app.intent.context_builder import AssembledContext
from app.intent.intent_engine import IntentEngineResult
from app.security.auth import AuthenticatedUser


class OutputAssembler:
    """组装最终 IntentResult"""

    def assemble(
        self,
        user_input: str,
        intent_result: IntentEngineResult,
        context: AssembledContext,
        clarify: ClarifyResult,
        user: AuthenticatedUser,
        session_id: str,
        trace_id: str,
    ) -> IntentResult:
        """
        将意图链路各步骤结果合并为统一的 IntentResult。

        合并逻辑：
        - 意图/路由/复杂度：以 IntentEngine 输出为主
        - 实体线索：仅来源于 LLM 提取（用于调试/日志，不持久化）
        - 追问：以 ClarifyHandler 判断为准
        """
        # 构建 history_messages：user/assistant 消息（compaction 节点转为 role=user 注入）
        # [-10:] 硬截断已移除：由 L3ReActEngine._build_initial_messages() 做 token 动态边界
        history_messages = []
        for m in context.history.messages:
            if m.role == "user" and m.content:
                history_messages.append({"role": "user", "content": m.content})
            elif m.role == "assistant" and m.content:
                if m.is_compaction:
                    # compaction 节点注入为 role=user，带框架说明（避免连续 assistant 消息）
                    from app.memory.schemas import _COMPACTION_INJECT_TEMPLATE
                    history_messages.append({
                        "role": "user",
                        "content": _COMPACTION_INJECT_TEMPLATE.format(summary_content=m.content),
                    })
                else:
                    history_messages.append({"role": "assistant", "content": m.content})

        return IntentResult(
            route=intent_result.route,
            complexity=intent_result.complexity,
            confidence=intent_result.confidence,
            intent=IntentDetail(
                primary=intent_result.intent_primary,
                sub_intent=intent_result.sub_intent,
                user_goal=intent_result.user_goal,
            ),
            entity_hints=intent_result.entity_hints,
            needs_clarify=clarify.needs_clarify,
            clarify_question=clarify.question,
            raw_input=user_input,
            session_id=session_id,
            trace_id=trace_id,
            history_messages=history_messages,
        )
