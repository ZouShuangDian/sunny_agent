## ADDED Requirements

### Requirement: context_usage 数据结构

每步 Think 完成后，系统 SHALL 计算上下文用量快照：

```python
context_usage = {
    "prompt_tokens": int,   # 本次 Think 的 prompt_tokens（服务端精确值）
    "remaining": int,       # MODEL_CONTEXT_LIMIT - prompt_tokens
    "percent": float,       # round(prompt_tokens / MODEL_CONTEXT_LIMIT * 100, 1)
    "limit": int,           # MODEL_CONTEXT_LIMIT（98,304）
}
```

#### Scenario: Think 返回用量后计算 context_usage
- **WHEN** Thinker.think() 返回 `think_result.usage["prompt_tokens"] = 52000`
- **THEN** 系统计算 `context_usage = {"prompt_tokens": 52000, "remaining": 46304, "percent": 52.9, "limit": 98304}`

#### Scenario: Think 无 usage 信息时降级
- **WHEN** `think_result.usage` 为空或不含 `prompt_tokens`
- **THEN** 不生成 `context_usage`（字段为 None），不影响后续流程

### Requirement: 流式 SSE context_usage 事件

`execute_stream()` 在每步 Think 后 SHALL yield 一个 `context_usage` 事件：

```json
{"event": "context_usage", "data": {"prompt_tokens": 52000, "remaining": 46304, "percent": 52.9, "limit": 98304}}
```

`chat_stream()` SHALL 将该事件透传给前端（与 thought / tool_call 等事件同级）。

#### Scenario: 流式执行推送 context_usage
- **WHEN** L3 流式执行每步 Think 完成
- **THEN** 前端收到 `context_usage` SSE 事件，可实时更新上下文用量显示

#### Scenario: 多步执行推送多次
- **WHEN** L3 流式执行经过 5 步 Think
- **THEN** 前端累计收到 5 个 `context_usage` 事件，每个反映对应步骤的用量

### Requirement: 非流式 context_usage 响应字段

`ExecutionResult` SHALL 包含 `context_usage: dict | None` 字段，存储最后一步 Think 的用量快照。

`ChatResponse` SHALL 包含 `context_usage: dict | None` 字段，透传 `ExecutionResult.context_usage`。

#### Scenario: 非流式 L3 执行返回 context_usage
- **WHEN** L3 非流式执行完成，最后一步 Think 的 `prompt_tokens = 65000`
- **THEN** `ChatResponse.context_usage = {"prompt_tokens": 65000, "remaining": 33304, "percent": 66.1, "limit": 98304}`

#### Scenario: L1 执行不返回 context_usage
- **WHEN** L1 FastTrack 执行完成（route=standard_l1）
- **THEN** `ChatResponse.context_usage` 为 None（L1 不做 context 管理）

#### Scenario: 追问场景不返回 context_usage
- **WHEN** 意图分析判断需要追问（needs_clarify=True）
- **THEN** `ChatResponse.context_usage` 为 None（未进入执行层）
