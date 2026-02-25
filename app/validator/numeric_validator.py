"""
M06 Layer 1：数值交叉校验（确定性，零 LLM 成本）

原理：
1. 从所有工具返回结果（ToolCall.result JSON）中递归提取所有数值
2. 从 LLM 输出文本中提取所有数值（正则）
3. 检查 LLM 输出中的数值是否都能在工具数据中找到匹配
4. 不匹配的数值视为潜在错误，上报为 ValidationIssue

匹配策略：
- 精确匹配（字符串相等）
- 浮点近似匹配（相对误差 < 0.01%，防止精度问题误报）
- 整数/浮点形式互换（92 vs 92.0）

注意：
- 误拦率控制：只有当数值在工具数据中「完全不存在」时才报 critical
- 百分比处理：92.3% 和 92.3 视为同一数值
"""

from __future__ import annotations

import json
import re

import structlog

from app.memory.schemas import ToolCall
from app.validator.schemas import ValidationIssue

log = structlog.get_logger()

# 匹配数字（含负号、小数点、千分位逗号）
_NUMBER_PATTERN = re.compile(
    r"""
    (?<![a-zA-Z\d])      # 前方不接字母或数字（避免匹配 v1.2 中的 1.2）
    -?                   # 可选负号
    (?:
        \d{1,3}(?:,\d{3})+  # 千分位格式：1,234,567
        |
        \d+              # 普通整数或小数的整数部分
    )
    (?:\.\d+)?           # 可选小数部分
    (?=%|\s|$|[,，。；、\)）\]】\s]|$)  # 后面接 %、空白、结束、标点
    """,
    re.VERBOSE,
)


def _extract_numbers_from_text(text: str) -> set[float]:
    """从文本中提取所有数值（去除千分位逗号后转 float）"""
    result: set[float] = set()
    for m in _NUMBER_PATTERN.finditer(text):
        raw = m.group().replace(",", "")
        try:
            result.add(float(raw))
        except ValueError:
            pass
    return result


def _extract_numbers_from_json(obj: object) -> set[float]:
    """递归从 JSON 对象中提取所有数值（int/float 字段值）"""
    result: set[float] = set()
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        result.add(float(obj))
    elif isinstance(obj, str):
        # 字符串中也可能内嵌数字（如 "良率: 92.3%"）
        result.update(_extract_numbers_from_text(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            result.update(_extract_numbers_from_json(v))
    elif isinstance(obj, list):
        for item in obj:
            result.update(_extract_numbers_from_json(item))
    return result


def _numbers_from_tool_calls(tool_calls: list[ToolCall]) -> set[float]:
    """从所有工具调用结果中提取数值集合"""
    all_numbers: set[float] = set()
    for tc in tool_calls:
        if not tc.result:
            continue
        try:
            obj = json.loads(tc.result)
            all_numbers.update(_extract_numbers_from_json(obj))
        except json.JSONDecodeError:
            # result 不是 JSON，当文本处理
            all_numbers.update(_extract_numbers_from_text(tc.result))
    return all_numbers


def _is_matched(num: float, reference: set[float], rel_tol: float = 1e-4) -> bool:
    """判断数值是否在参考集中存在（精确匹配 or 相对误差容忍）"""
    if num in reference:
        return True
    for ref in reference:
        if ref == 0:
            if abs(num) < 1e-9:
                return True
        elif abs(num - ref) / abs(ref) < rel_tol:
            return True
    return False


def validate_numerics(
    output_text: str,
    tool_calls: list[ToolCall],
) -> list[ValidationIssue]:
    """
    Layer 1 数值交叉校验。

    Args:
        output_text: LLM 最终回复文本
        tool_calls:  所有工具调用记录（含 result 字段）

    Returns:
        ValidationIssue 列表（空列表表示无问题）
    """
    if not tool_calls:
        # 无工具调用（纯 LLM 回复）则跳过数值校验
        return []

    tool_numbers = _numbers_from_tool_calls(tool_calls)
    if not tool_numbers:
        # 工具未返回任何数值，无法比对
        log.debug("数值校验：工具结果中无数值，跳过")
        return []

    output_numbers = _extract_numbers_from_text(output_text)
    if not output_numbers:
        return []

    issues: list[ValidationIssue] = []
    mismatched: list[float] = []

    for num in output_numbers:
        # 过滤掉「纯计数/序号」类小数字（1-10），这类数字来自 LLM 本身概率很高
        if 1 <= num <= 10 and num == int(num):
            continue
        if not _is_matched(num, tool_numbers):
            mismatched.append(num)

    if mismatched:
        # 超过 3 个不匹配数值才上报 critical（避免因格式差异误拦）
        severity = "critical" if len(mismatched) >= 3 else "warning"
        issues.append(ValidationIssue(
            type="numeric_mismatch",
            severity=severity,
            description=(
                f"输出中 {len(mismatched)} 个数值无法从工具返回数据中找到匹配："
                f" {[str(n) for n in mismatched[:5]]}"
                + ("..." if len(mismatched) > 5 else "")
            ),
        ))
        log.warning(
            "数值校验发现不匹配",
            mismatched_count=len(mismatched),
            samples=mismatched[:3],
        )
    else:
        log.debug("数值校验通过", output_numbers_count=len(output_numbers))

    return issues
