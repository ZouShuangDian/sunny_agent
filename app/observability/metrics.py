"""
M13-2 Prometheus 指标定义

所有指标统一在此文件定义，中间件和业务代码按需引用。
"""

from prometheus_client import Counter, Histogram

# ── 请求级指标 ──

REQUEST_TOTAL = Counter(
    "sunny_request_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status_code"],
)

REQUEST_DURATION = Histogram(
    "sunny_request_duration_ms",
    "HTTP 请求耗时（毫秒）",
    ["method", "endpoint"],
    buckets=[50, 100, 200, 500, 1000, 2000, 5000, 10000],
)

# ── LLM 调用指标 ──

LLM_CALL_TOTAL = Counter(
    "sunny_llm_call_total",
    "LLM 调用总数",
    ["model", "purpose"],  # purpose: intent/execution/direct_response
)

LLM_CALL_DURATION = Histogram(
    "sunny_llm_call_duration_ms",
    "LLM 调用耗时（毫秒）",
    ["model", "purpose"],
    buckets=[200, 500, 1000, 2000, 5000, 10000, 30000],
)

# ── 执行层指标 ──

ROUTE_TOTAL = Counter(
    "sunny_route_total",
    "执行路由分发总数",
    ["route", "intent"],
)

TOOL_CALL_TOTAL = Counter(
    "sunny_tool_call_total",
    "工具调用总数",
    ["tool_name", "status"],  # status: success/error
)

# ── 错误指标 ──

ERROR_TOTAL = Counter(
    "sunny_error_total",
    "错误总数",
    ["error_type"],  # llm_error/tool_error/guardrails_fallback/unknown
)
