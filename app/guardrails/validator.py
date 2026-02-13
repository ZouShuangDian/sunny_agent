"""
M04 护栏层总入口：校验意图理解层输出的 JSON

完整流程：
1. JsonRepairer 修复畸形 JSON
2. DefaultFiller 填充缺失字段
3. Pydantic Schema 校验
4. 校验失败 → FallbackHandler 降级
"""

from dataclasses import dataclass

import structlog
from pydantic import ValidationError

from app.guardrails.default_filler import DefaultFiller
from app.guardrails.fallback_handler import FallbackHandler
from app.guardrails.json_repairer import JsonRepairer
from app.guardrails.schemas import IntentResult

log = structlog.get_logger()


@dataclass
class GuardrailsOutput:
    """护栏层输出"""

    result: IntentResult  # 校验通过的最终结果
    repaired: bool = False  # 是否经过 JSON 修复
    fell_back: bool = False  # 是否使用了降级结果


class GuardrailsValidator:
    """护栏层总入口"""

    def __init__(self):
        self.repairer = JsonRepairer()
        self.filler = DefaultFiller()
        self.fallback = FallbackHandler()

    def validate(
        self,
        raw_json: str,
        raw_input: str,
        session_id: str,
        trace_id: str,
    ) -> GuardrailsOutput:
        """
        校验并修复意图引擎输出。

        流程：
        1. JSON 修复 + 默认值填充 + Schema 校验
        2. 失败 → 降级
        """
        # 1. 尝试修复 + 校验
        try:
            data = self.repairer.repair(raw_json)
            data = self.filler.fill(data)

            # 注入上下文字段（这些不来自 LLM 输出）
            data["raw_input"] = raw_input
            data["session_id"] = session_id
            data["trace_id"] = trace_id

            result = IntentResult.model_validate(data)

            return GuardrailsOutput(result=result, repaired=True)

        except (ValueError, ValidationError) as e:
            log.warning(
                "护栏层校验失败，使用降级结果",
                error=str(e),
                trace_id=trace_id,
                raw_preview=raw_json[:200] if raw_json else "",
            )

        # 2. 降级
        return GuardrailsOutput(
            result=self.fallback.fallback(raw_input, session_id, trace_id),
            fell_back=True,
        )
