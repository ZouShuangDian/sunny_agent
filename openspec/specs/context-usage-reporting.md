# Context Usage Reporting Spec — 上下文用量推送

## 1. 概述

每步 Think 完成后，系统计算上下文用量快照（`context_usage`），通过流式 SSE 事件和非流式响应字段推送给前端，供前端显示剩余 token 百分比。

---

## 2. context_usage 数据结构

```python
context_usage = {
    "prompt_tokens": int,   # 本次 Think 的 prompt_tokens（服务端精确值）
    "remaining": int,       # MODEL_CONTEXT_LIMIT - prompt_tokens
    "percent": float,       # round(prompt_tokens / MODEL_CONTEXT_LIMIT * 100, 1)
    "limit": int,           # MODEL_CONTEXT_LIMIT（98,304）
}
```

---

## 3. 流式 SSE context_usage 事件

`execute_stream()` 在每步 Think 后 yield 一个 `context_usage` 事件：

```json
{"event": "context_usage", "data": {"prompt_tokens": 52000, "remaining": 46304, "percent": 52.9, "limit": 98304}}
```

`chat_stream()` 将该事件透传给前端（与 thought / tool_call 等事件同级）。

事件顺序：`thought` → `context_usage` → `tool_call` → `tool_result`

---

## 4. 非流式 context_usage 响应字段

`ExecutionResult` 包含 `context_usage: dict | None` 字段，存储最后一步 Think 的用量快照。

`ChatResponse` 包含 `context_usage: dict | None` 字段，透传 `ExecutionResult.context_usage`。

- L3 执行完成 → `ChatResponse.context_usage` 包含最后一步数据
- L1 执行 / 追问场景 → `ChatResponse.context_usage` 为 None
