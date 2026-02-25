"""
M06 Output Validator — 三层校验编排器

执行顺序（按成本从低到高）：
  Layer 1：数值交叉校验（确定性，零 LLM 成本）→ 始终执行
  Layer 2：幻觉检测（LLM 交叉校验，Haiku 级别）→ enable_hallucination=True 时执行
  Layer 3：逻辑自洽检查（仅 L3，可选）→ enable_logic_check=True 时执行（当前为 stub）

置信度计算规则：
  初始 1.0，每个 critical 问题 -0.3，每个 warning 问题 -0.1，最低 0.0

输出处理规则：
  - 有 critical 问题：在回复末尾附加醒目警告标注
  - 仅 warning：不修改回复，仅记录到 issues
  - 无问题：原样返回
"""

from __future__ import annotations

import structlog

from app.llm.client import LLMClient
from app.memory.schemas import ToolCall
from app.validator.hallucination_detector import detect_hallucinations
from app.validator.numeric_validator import validate_numerics
from app.validator.schemas import ValidatorInput, ValidatorOutput, ValidationIssue

log = structlog.get_logger()

# 置信度扣分规则
_PENALTY_CRITICAL = 0.3
_PENALTY_WARNING = 0.1


def _compute_confidence(issues: list[ValidationIssue]) -> float:
    """基于问题列表计算整体置信度"""
    score = 1.0
    for issue in issues:
        if issue.severity == "critical":
            score -= _PENALTY_CRITICAL
        elif issue.severity == "warning":
            score -= _PENALTY_WARNING
    return max(0.0, round(score, 2))


def _append_warning_note(output: str, issues: list[ValidationIssue]) -> str:
    """在输出末尾附加 critical 问题的警告标注"""
    critical_issues = [i for i in issues if i.severity == "critical"]
    if not critical_issues:
        return output

    note_lines = [
        "",
        "---",
        "⚠️ **数据准确性提示**：以下内容可能存在数据偏差，建议核实：",
    ]
    for issue in critical_issues:
        note_lines.append(f"- {issue.description}")

    return output + "\n".join(note_lines)


class OutputValidator:
    """
    输出校验器：编排三层校验，返回置信度标注的最终输出。

    应用启动时实例化一次，无状态，所有请求共享。
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def validate(self, validator_input: ValidatorInput) -> ValidatorOutput:
        """
        执行全部校验层，返回校验结果。

        不抛出异常：任何校验层失败均静默降级，返回原始输出 + 满置信度。
        """
        output = validator_input.execution_output
        all_issues: list[ValidationIssue] = []

        # ── Layer 1：数值交叉校验（确定性） ──
        try:
            numeric_issues = validate_numerics(output, validator_input.tool_calls)
            all_issues.extend(numeric_issues)
        except Exception as e:
            log.warning("Layer 1 数值校验异常，跳过", error=str(e))

        # ── Layer 2：幻觉检测（LLM） ──
        if validator_input.enable_hallucination and validator_input.tool_calls:
            try:
                hallucination_issues = await detect_hallucinations(
                    output, validator_input.tool_calls, self._llm
                )
                all_issues.extend(hallucination_issues)
            except Exception as e:
                log.warning("Layer 2 幻觉检测异常，跳过", error=str(e))

        # ── Layer 3：逻辑自洽检查（stub，默认关闭） ──
        if validator_input.enable_logic_check and validator_input.reasoning_trace:
            # Phase 3+ 实现，当前 stub
            log.debug("Layer 3 逻辑自洽检查：stub，暂未实现")

        # ── 组装结果 ──
        confidence = _compute_confidence(all_issues)
        has_critical = any(i.severity == "critical" for i in all_issues)

        validated_output = _append_warning_note(output, all_issues) if has_critical else output
        is_modified = validated_output != output

        if all_issues:
            log.info(
                "输出校验完成",
                total_issues=len(all_issues),
                critical=sum(1 for i in all_issues if i.severity == "critical"),
                warning=sum(1 for i in all_issues if i.severity == "warning"),
                confidence=confidence,
                is_modified=is_modified,
            )
        else:
            log.debug("输出校验通过，无问题", confidence=confidence)

        return ValidatorOutput(
            validated_output=validated_output,
            confidence=confidence,
            issues=all_issues,
            is_modified=is_modified,
        )
