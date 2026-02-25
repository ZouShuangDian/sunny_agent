"""
M06 Layer 2：幻觉检测（LLM 交叉校验，Haiku 级别低成本）

原理：
将所有工具返回的原始数据 + LLM 的最终回复一起交给校验 LLM，让它判断：
"输出中哪些结论/事实无法从给定的工具数据中推导出来？"

设计要点：
- 校验 LLM 使用轻量模型（Haiku），不影响主对话成本
- 工具数据截断至 3000 字符，避免校验本身 token 过高
- 返回结构化 JSON，解析失败时返回空（降级为不拦截）
- 整体调用失败（LLM 异常）时静默降级，不阻塞主链路
"""

from __future__ import annotations

import json

import structlog

from app.llm.client import LLMClient
from app.memory.schemas import ToolCall
from app.validator.schemas import ValidationIssue

log = structlog.get_logger()

# 校验 Prompt（稳定模板，硬编码）
_HALLUCINATION_CHECK_PROMPT = """\
你是一个事实准确性校验助手。请分析以下内容：

【工具返回的原始数据】
{tool_data}

【AI 助手的回复】
{output}

任务：找出 AI 回复中哪些具体的事实陈述（数字、结论、描述）无法从上方工具数据中推导出来。

输出格式（JSON 数组，找不到问题时返回空数组）：
[
  {{
    "description": "具体描述哪句话/哪个数据无法从工具数据中找到依据",
    "severity": "critical 或 warning"
  }}
]

注意：
- 只关注具体的事实性陈述（数字、实体名称、结论），不关注语气/措辞
- 如果某个结论可以从工具数据合理推断，不要上报
- 如果工具数据不足以判断（数据太少），上报 warning 而非 critical
- 直接返回 JSON，不要包含其他说明文字\
"""


def _summarize_tool_data(tool_calls: list[ToolCall], max_chars: int = 3000) -> str:
    """将工具返回结果汇总为文本，截断到 max_chars 字符"""
    parts: list[str] = []
    for tc in tool_calls:
        if not tc.result:
            continue
        try:
            obj = json.loads(tc.result)
            # 去掉 status 字段（冗余），直接展示数据内容
            if isinstance(obj, dict):
                obj.pop("status", None)
            data_str = json.dumps(obj, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            data_str = tc.result
        parts.append(f"[{tc.tool_name}]\n{data_str}")

    combined = "\n\n".join(parts)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n...[数据已截断]"
    return combined or "（无工具数据）"


async def detect_hallucinations(
    output_text: str,
    tool_calls: list[ToolCall],
    llm: LLMClient,
) -> list[ValidationIssue]:
    """
    Layer 2 幻觉检测。

    Args:
        output_text: LLM 最终回复文本
        tool_calls:  工具调用记录列表
        llm:         LLMClient 实例（使用轻量模型）

    Returns:
        ValidationIssue 列表（空列表表示无问题）
    """
    if not tool_calls:
        log.debug("幻觉检测：无工具调用，跳过")
        return []

    tool_data_str = _summarize_tool_data(tool_calls)
    prompt = _HALLUCINATION_CHECK_PROMPT.format(
        tool_data=tool_data_str,
        output=output_text[:2000],  # 回复也截断，避免过长
    )

    try:
        resp = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        raw = resp.content.strip()

        # 解析返回的 JSON
        parsed = json.loads(raw)
        # 兼容直接返回列表或包在对象中
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            # 有些模型会返回 {"issues": [...]}
            items = next(
                (v for v in parsed.values() if isinstance(v, list)),
                [],
            )
        else:
            items = []

    except Exception as e:
        # 幻觉检测失败时静默降级，不阻塞主链路
        log.warning("幻觉检测调用失败，静默降级", error=str(e))
        return []

    issues: list[ValidationIssue] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        desc = item.get("description", "")
        severity_raw = item.get("severity", "warning").lower()
        severity = "critical" if severity_raw == "critical" else "warning"
        if desc:
            issues.append(ValidationIssue(
                type="hallucination",
                severity=severity,
                description=desc,
            ))

    if issues:
        log.warning(
            "幻觉检测发现问题",
            issue_count=len(issues),
            critical_count=sum(1 for i in issues if i.severity == "critical"),
        )
    else:
        log.debug("幻觉检测通过")

    return issues
